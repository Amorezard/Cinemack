#!/usr/bin/env python3
"""
v3 review intelligence: what audiences and critics liked or disliked about a film, by aspect,
with a quote for every count - so an answer can say "audiences praised the acting (12 of 15
reviews) but often found it slow ('the three hours drag')" and every part of that is checkable.

    raw text ──► aspect extraction (small model, JSON schema) ──► quote check ──► counts per film
      audience: TMDB user reviews (facts.reviews)
      critics:  Wikipedia "Reception" / "Critical response" passages (facts.sections, kind=reception)

    python reviews_analyze.py --films 20            # smoke test: the 20 most-voted films
    python reviews_analyze.py --films 1500          # overnight (~4 h with llama3.2)
    python reviews_analyze.py aggregate             # rebuild film_aspects from what is done
    python reviews_analyze.py show "Oppenheimer"

v4 (14 Oct):
  - The 300-token output cap cut answers off mid-JSON (8 Oct finding: llama3.2 malformed JSON stopped the
    analysis at 208 films; temperature 0 made every retry identical). Now REVIEW_NUM_PREDICT (700) and a
    retry with twice the room when the JSON does not parse.
  - REVIEW_FILTER=1 (default when review_quality.db exists): only reviews review_filter.py marks usable are
    analysed and counted (aggregate drops aspects from unusable ones).
  - REVIEW_FILMS_MAX no longer defaults to 200 (that cap was for the CPU-only week); 0 = no cap.

21 Oct (--kinds pro,ytc): two more sources from fetch_free_data.py, each its own source_type
  pro    professional reviews (critic_reviews: the Guardian with --body text, else the standfirst; NYT summary),
         one review = one item, only reviews matched to the film (match >= 1)
  ytc    the top 40 comments under the film's main YouTube trailer (most-liked first), joined into one text:
         what viewers say they are excited or worried about. Comments are mostly written after release.
  YouTube review-video descriptions are not analysed: they are mostly links and channel promotion.
    python reviews_analyze.py --kinds pro,ytc --films 1500

Design choices:
  - A fixed aspect list (the brief's: acting, pacing, writing, cinematography, soundtrack,
    characters, ending, originality, emotional impact, humor, action, visual effects, plus
    story and direction), so counts add up across reviews and films.
  - Every aspect needs a quote that literally appears in the text (checked in code, case and
    spacing ignored). An aspect the model can't quote is dropped: no evidence, no count.
  - Critic passages are Wikipedia's summary of critics, so a "critic" count means "statements
    in the reception text", not "number of critics" - the answer prompt says so.
  - Resumable (review_done), versioned (VERSION): changing the prompt re-runs only with a new version.
  - Never thousands of reviews in a prompt: the answer sees the counts and 1 quote per aspect.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

import facts_db as F

VERSION = "ra1"
NUM_PREDICT = int(os.environ.get("REVIEW_NUM_PREDICT", "700"))
ASPECTS = ["acting", "pacing", "writing", "story", "cinematography", "soundtrack", "characters", "ending",
           "originality", "emotional impact", "humor", "action", "visual effects", "direction"]
SCHEMA = {"type": "object", "properties": {
    "aspects": {"type": "array", "items": {"type": "object", "properties": {
        "aspect": {"type": "string", "enum": ASPECTS},
        "sentiment": {"type": "string", "enum": ["positive", "negative", "mixed"]},
        "quote": {"type": "string"}}, "required": ["aspect", "sentiment", "quote"]}}},
    "required": ["aspects"]}
SYSTEM_AUDIENCE = (
    "You analyse one viewer's movie review. List the aspects of the film the reviewer clearly praises or "
    "criticises, from this list only: " + ", ".join(ASPECTS) + ". For each, give the sentiment and a short "
    "quote (3-15 words) copied exactly from the review that shows it. At most 6 aspects. Skip aspects the "
    "review only mentions in passing. Reply with JSON only.")
SYSTEM_PRO = (
    "You analyse one professional critic's film review. List the aspects of the film the critic clearly praises or "
    "criticises, from this list only: " + ", ".join(ASPECTS) + ". For each, give the sentiment and a short quote "
    "(3-15 words) copied exactly from the review that shows it. At most 6 aspects. Reply with JSON only.")
SYSTEM_COMMENTS = (
    "You read comments viewers left under a film's trailer, separated by ' | '. List the aspects of the film the "
    "comments clearly praise or criticise, from this list only: " + ", ".join(ASPECTS) + ". Use 'mixed' when "
    "comments disagree. For each, give a short quote (3-15 words) copied exactly from one comment. At most 6 "
    "aspects. Reply with JSON only.")
SYSTEMS = {}
SYSTEM_CRITIC = (
    "You analyse a passage summarising how critics received a film. List the aspects critics clearly praised "
    "or criticised, from this list only: " + ", ".join(ASPECTS) + ". For each, give the sentiment and a short "
    "quote (3-15 words) copied exactly from the passage that shows it. Ignore box office, awards and "
    "audience scores. At most 6 aspects. Reply with JSON only.")


STATS = {"ok": 0, "salvaged": 0, "lost": 0}
_OBJ = re.compile(r"\{[^{}]*\}")


def salvage(raw):
    """Complete aspect objects from a cut-off or whitespace-flooded answer (14 Oct: llama3.2 sometimes
    pads structured output with hundreds of blank lines until it hits num_predict, so the JSON never
    closes; the objects written before that are still fine)."""
    items = []
    for m in _OBJ.finditer(raw or ""):
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(d, dict) and {"aspect", "sentiment", "quote"} <= set(d):
            items.append(d)
    return {"aspects": items}


def call(url, model, system, text):
    """{"aspects": [...]} or None. A stop on runs of blank lines ends the padding early; a reply that
    still does not parse is salvaged; only an empty salvage is retried, once, with more room."""
    n_pred = NUM_PREDICT
    for attempt in range(2):
        body = json.dumps({"model": model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
                           "options": {"temperature": 0, "num_predict": n_pred, "stop": ["\n\n\n\n", "\t\t\t\t"]},
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": text}]}).encode()
        try:
            req = urllib.request.Request(url + "/api/chat", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = json.load(r)["message"]["content"]
        except Exception as e:  # noqa: BLE001 - network / server error
            print(f"  retry {attempt + 1}: {str(e)[:100]}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
            continue
        try:
            out = json.loads(raw)
            STATS["ok"] += 1
            return out
        except ValueError:
            got = salvage(raw)
            if got["aspects"]:
                STATS["salvaged"] += 1
                return got
            n_pred *= 2
    STATS["lost"] += 1
    return None


def usable():
    """Review ids review_filter.py marks usable, or None (filter off / not built)."""
    if os.environ.get("REVIEW_FILTER", "1") != "1":
        return None
    try:
        import review_filter
        return review_filter.usable_ids()
    except Exception:  # noqa: BLE001
        return None


def _flat(s):
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”‘’`]", "", str(s or ""))).strip().casefold()


def verified(text, items):
    """Keep aspects whose quote is really in the text; dedupe by aspect."""
    flat = _flat(text)
    out, seen = [], set()
    for a in (items or {}).get("aspects", []) if isinstance(items, dict) else []:
        asp, sen, q = a.get("aspect"), a.get("sentiment"), str(a.get("quote") or "").strip()
        if asp not in ASPECTS or sen not in ("positive", "negative", "mixed") or asp in seen:
            continue
        if len(q.split()) < 2 or _flat(q) not in flat:
            continue
        seen.add(asp)
        out.append((asp, {"positive": 1, "negative": -1, "mixed": 0}[sen], q[:200]))
    return out


def work_items(conn, films, kinds, per_film, critic_passages, ok=None):
    done = {r[0] for r in conn.execute("select review_id from review_done where version = ?", (VERSION,))}
    ids = [r[0] for r in conn.execute("select tmdb_id from films order by vote_count desc limit ?", (films,))]
    items = []
    for fid in ids:
        if "audience" in kinds:
            n = 0
            for rid, content in conn.execute("select review_id, content from reviews where tmdb_id = ? "
                                             "and length(content) > 200 order by created_at desc", (fid,)):
                if ok is not None and rid not in ok:
                    continue
                n += 1
                if n > per_film:
                    break
                if rid not in done:
                    items.append(("audience", fid, rid, content[:1800]))
        if "critic" in kinds:
            for sid, text in conn.execute("select sec_id, text from sections where tmdb_id = ? and kind = 'reception' "
                                          "order by position limit ?", (fid, critic_passages)):
                key = f"sec:{sid}"
                if key not in done:
                    items.append(("critic", fid, key, text))
        if "pro" in kinds:
            try:
                rows = conn.execute("select review_key, coalesce(body, summary) from critic_reviews where tmdb_id = ? "
                                    "and match >= 1 order by published desc limit 6", (fid,)).fetchall()
            except sqlite3.Error:
                rows = []
            for rk, text in rows:
                key = f"cr:{rk}"
                if text and len(text) > 60 and key not in done:
                    items.append(("pro", fid, key, text[:1800]))
        if "ytc" in kinds:
            try:
                cs = [t for (t,) in conn.execute("select text from yt_comments where tmdb_id = ? and length(text) > 15 "
                                                 "order by likes desc limit 40", (fid,))]
            except sqlite3.Error:
                cs = []
            key = f"ytc:{fid}"
            if len(cs) >= 5 and key not in done:
                items.append(("ytc", fid, key, " | ".join(" ".join(c.split()) for c in cs)[:1800]))
    return items


def aggregate(conn, fids=None):
    where = "" if fids is None else f"where tmdb_id in ({','.join(str(int(f)) for f in fids)})"
    rows = conn.execute(f"select tmdb_id, source_type, aspect, sentiment, quote, review_id from review_aspects "
                        f"{where}").fetchall()
    ok = usable()
    if ok is not None:                     # audience aspects only from usable reviews; critic passages always
        rows = [r for r in rows if r[1] != "audience" or r[5] in ok]
    agg, nrev = {}, {}
    for fid, st, asp, sen, q, rid in rows:
        a = agg.setdefault((fid, st, asp), {"pos": 0, "neg": 0, "mixed": 0, "quotes": []})
        a["pos" if sen > 0 else "neg" if sen < 0 else "mixed"] += 1
        if q and len(a["quotes"]) < 3 and q not in a["quotes"]:
            a["quotes"].append(q)
        nrev.setdefault((fid, st), set()).add(rid)
    # n_reviews counts every analysed item, including those with no verified aspect.
    for fid, st, rid in conn.execute(f"select r.tmdb_id, 'audience', d.review_id from review_done d join reviews r "
                                     f"on r.review_id = d.review_id {where.replace('tmdb_id', 'r.tmdb_id')}"):
        if ok is None or rid in ok:
            nrev.setdefault((fid, st), set()).add(rid)
    if fids is None:
        conn.execute("delete from film_aspects")
    else:
        conn.execute(f"delete from film_aspects {where}")
    ts = F.now()
    conn.executemany("insert or replace into film_aspects values (?,?,?,?,?,?,?,?,?)",
                     [(fid, st, asp, a["pos"], a["neg"], a["mixed"], len(nrev.get((fid, st), ())),
                       json.dumps(sorted(a["quotes"], key=len)), ts) for (fid, st, asp), a in agg.items()])
    conn.commit()
    return len(agg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "aggregate", "show"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--films", type=int, default=1500, help="most-voted films to cover")
    ap.add_argument("--per-film", type=int, default=25, help="audience reviews per film (usable ones, newest first)")
    ap.add_argument("--critic-passages", type=int, default=3)
    ap.add_argument("--kinds", default="audience,critic")
    ap.add_argument("--model", default="llama3.2")
    ap.add_argument("--ollama", default="http://localhost:11434")
    a = ap.parse_args()
    conn = F.connect(a.facts)
    if a.cmd == "aggregate":
        print(f"film_aspects rows: {aggregate(conn)}")
        return
    if a.cmd == "show":
        for fid, t, y in conn.execute("select tmdb_id, title, year from films where title like ? "
                                      "order by vote_count desc limit 1", (a.title,)):
            print(f"{t} ({y})")
            for row in conn.execute("select source_type, aspect, pos, neg, mixed, n_reviews, quotes_json from "
                                    "film_aspects where tmdb_id = ? order by source_type, pos + neg desc", (fid,)):
                print("  ", row)
        return
    # 5 Oct: another user's job holds 20 of the A10's 23 GB, so models run on the CPU. Cap the work
    # (REVIEW_FILMS_MAX, default 200) so the run finishes; resumable, so raise it later to continue.
    cap = int(os.environ.get("REVIEW_FILMS_MAX", "0"))
    films = min(a.films, cap) if cap else a.films
    ok = usable()
    items = work_items(conn, films, a.kinds.split(","), a.per_film, a.critic_passages, ok)
    print(f"{len(items):,} texts to analyse with {a.model} (version {VERSION}; "
          f"{'review filter on: ' + format(len(ok), ',') + ' usable reviews' if ok is not None else 'no review filter'}, "
          f"num_predict {NUM_PREDICT})")
    t0, kept, touched = time.time(), 0, set()
    for i, (st, fid, rid, text) in enumerate(items, 1):
        system = {"audience": SYSTEM_AUDIENCE, "critic": SYSTEM_CRITIC, "pro": SYSTEM_PRO, "ytc": SYSTEM_COMMENTS}[st]
        res = call(a.ollama, a.model, system, text)
        good = verified(text, res)
        conn.execute("delete from review_aspects where review_id = ?", (rid,))
        conn.executemany("insert into review_aspects values (?,?,?,?,?,?,?,?)",
                         [(rid, fid, st, asp, sen, q, a.model, VERSION) for asp, sen, q in good])
        conn.execute("insert or replace into review_done values (?,?,?)", (rid, VERSION, len(good)))
        kept += len(good)
        touched.add(fid)
        if i % 50 == 0 or i == len(items):
            conn.commit()
            aggregate(conn, touched)
            touched = set()
            rate = i / max(time.time() - t0, 1e-6)
            print(f"  {i:,}/{len(items):,}  {rate:.2f}/s  aspects kept {kept:,}  replies ok {STATS['ok']:,} "
                  f"salvaged {STATS['salvaged']:,} lost {STATS['lost']:,}  "
                  f"eta {(len(items) - i) / max(rate, 1e-6) / 60:.0f} min", flush=True)
    conn.commit()
    print(f"film_aspects rows: {aggregate(conn)}")
    F.set_meta(conn, "reviews_analyzed_at", F.now())
    conn.commit()


if __name__ == "__main__":
    main()
