#!/usr/bin/env python3
"""
Review quality filter (8 Oct, future-work step 1): which stored TMDB reviews are fit to use as
evidence for "why did this film succeed / fail" answers. No model calls, runs in seconds.

A review passes only if it clears every check below (cheapest first). A review can fail several
checks; all of its reasons are recorded.

  short          fewer than 3 full sentences (a full sentence = 5+ words ending in . ! or ?)
  swearing       a swear word or slur, including disguised spellings (f*ck, sh1t); the film's own
                 title and character names are removed first, so "Kick-Ass" or "Inglourious
                 Basterds" reviews are not failed for naming the film; ambiguous words that are
                 also names (dick, cock, prick, ...) only count in lower case ("Moby Dick" is fine)
  not_english    too few common English words to check for swearing
  duplicate      the same text already stored for this film (kept once: the earliest)
  copy_paste     the same text posted under more than one film (spam; every copy fails)
  generic_extreme  rating <= 2 or >= 9.5 with no film-specific detail (no title, cast, character,
                 director or keyword named) - the classic troll / shill review
  rating_mismatch  rating contradicts the wording (1/10 with clearly positive words, or the reverse)
  early_extreme  extreme rating posted before the film's release date
  burst          one of 5+ extreme ratings for the film inside 7 days, making up half its reviews
  one_note_author  author with 5+ rated reviews that are all <= 2 or all >= 9.5
  shouting       more than half of the letters are capitals
  links          more than 2 web links
  political      2+ political trigger terms and at most 1 film-specific detail (off-topic rant)

"Verified" in answers should mean: passed these checks AND every quote matches the stored text
(reviews_analyze.verified). It cannot prove the reviewer watched the film.

Reads facts.db read-only and writes results to a separate file, review_quality.db, so facts.db
(whose checksum is recorded with the sealed run) is not changed and nothing in the answering
pipeline reads the result yet.

    python review_filter.py                       # check every review, print + save the summary
    python review_filter.py show "Oppenheimer"    # each review of a film: pass/fail + reasons
    python review_filter.py sample 100            # results/review_label_sample.csv to hand-label
    python review_filter.py model [--limit N]     # v4: the small-model check, last (llama3.2, structured
                                                  # output): genuine / troll / spam / off_topic + reason, for
                                                  # reviews that passed every check above (table review_model)
    python review_filter.py score results/review_label_sample.csv   # v4: precision / recall vs your labels

A review is USABLE as evidence when it passed every check and, once the model check has run on it,
the model labelled it genuine (usable_ids() below; reviews_analyze.py uses it with REVIEW_FILTER=1).
"""
import argparse
import collections
import csv
import datetime
import hashlib
import os
import random
import re
import sqlite3
import sys

import facts_db as F

VERSION = "rf1"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DB = os.environ.get("REVIEW_QUALITY_DB", os.path.join(HERE, "review_quality.db"))
SUMMARY = os.path.join(HERE, "results", "review_filter_summary.txt")

MIN_SENTENCES = 3
MIN_WORDS_PER_SENTENCE = 5
LOW, HIGH = 2.0, 9.5            # TMDB ratings are 0-10
EVIDENCE_MIN = 10               # passing reviews a film needs before the model makes claims

# ---------------------------------------------------------------- word lists
# Regex roots matched as whole words, case-insensitive.
SWEARS = [
    r"fuck\w*", r"\w*fucker\w*", r"motherfuck\w*", r"fck\w*", r"fuk\w*", r"wtf", r"stfu",
    r"shit\w*", r"\w*shit", r"bullshit\w*", r"sh!t",
    r"bitch\w*", r"asshole\w*", r"arsehole\w*", r"dumbass\w*", r"jackass\w*", r"badass\w*", r"smartass\w*",
    r"cunt\w*", r"twat\w*", r"wank\w*", r"bollocks", r"piss\w*",
    r"damn\w*", r"goddamn\w*", r"crap\w*", r"hell", r"bastard\w*",
    r"whore\w*", r"slut\w*", r"retard\w*", r"fag\w*", r"nigg\w*", r"spastic",
]
# Words that are also names or ordinary words when capitalised: only lower case counts.
AMBIGUOUS_LOWER = [r"dicks?", r"dickhead\w*", r"cocks?", r"pricks?", r"pussy", r"pussies", r"ass", r"asses", r"tits?"]
# Plain forms used to undo disguised spellings (f*ck, f**k, sh1t, $hit, b!tch).
PLAIN = ["fuck", "fucking", "fucked", "fucker", "shit", "shitty", "bitch", "cunt", "asshole",
         "bastard", "dick", "cock", "pussy", "ass", "whore", "slut", "damn", "crap", "piss"]
LEET = str.maketrans({"1": "i", "!": "i", "0": "o", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s", "7": "t"})

POLITICAL = ["woke", "agenda", "propaganda", "leftist", "liberals", "libtard", "snowflake", "snowflakes",
             "sjw", "feminazi", "maga", "trump", "biden", "democrats", "republicans", "dei", "virtue signalling",
             "virtue signaling", "go woke go broke", "political correctness", "pc culture"]

POSITIVE = ["masterpiece", "brilliant", "excellent", "amazing", "outstanding", "superb", "wonderful", "beautiful",
            "fantastic", "loved", "love", "perfect", "great", "best", "stunning", "incredible", "enjoyed",
            "favourite", "favorite", "recommend", "gripping", "moving", "powerful", "terrific", "delightful"]
NEGATIVE = ["terrible", "awful", "boring", "worst", "horrible", "waste", "garbage", "trash", "stupid",
            "disappointing", "disappointment", "dull", "mess", "pointless", "bad", "hated", "hate", "poor",
            "unwatchable", "lazy", "painful", "ridiculous", "nonsense", "forgettable", "tedious"]

ENGLISH_COMMON = set("""the a an and or but of to in on at for with is was are were be been it this that
these those i you he she they we his her their my your not no so as by from have has had do does did
what who which when there than then just about very more most can will would could should all its film
movie story""".split())
STOP = ENGLISH_COMMON | set("""part film movie man woman girl boy love life night day year years time world
last first great little big black white blue dark house city king lord return rise""".split())

_SWEAR_RE = re.compile(r"(?<![\w*])(?:" + "|".join(SWEARS) + r")(?![\w*])", re.I)
_AMBIG_RE = re.compile(r"(?<![\w*])(?:" + "|".join(AMBIGUOUS_LOWER) + r")(?![\w*])")  # case-sensitive
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_TOKEN_RE = re.compile(r"[A-Za-z0-9@$*#!]+")
_ABBREV_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|Prof|Vol|No|Inc|Ltd|approx|e\.g|i\.e|U\.S|U\.K)\.", re.I)
_INITIAL_RE = re.compile(r"\b([A-Z])\.")
_SENT_END_RE = re.compile(r"[.!?][\"'”’)\]]*$")


# ---------------------------------------------------------------- single-review checks
def clean(text):
    t = _TAG_RE.sub(" ", text or "")
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"[*_#>`~]+", " ", t.replace("\r", ""))
    return t


def full_sentences(text):
    """Number of sentences with 5+ words that end in . ! or ? (abbreviations and initials kept whole)."""
    t = _ABBREV_RE.sub(lambda m: m.group(0).replace(".", "\u2024"), clean(text))
    t = _INITIAL_RE.sub("\\1\u2024", t)
    n = 0
    for seg in re.split(r"(?<=[.!?])[\"'”’)\]]*\s+|\n+", t):
        s = seg.strip()
        if _SENT_END_RE.search(s) and len(_WORD_RE.findall(s)) >= MIN_WORDS_PER_SENTENCE:
            n += 1
    return n


def _disguised(token):
    """True for tokens like f*ck, f**k, sh1t, $hit, b!tch."""
    tok = token.rstrip("!")
    if tok.startswith("*"):            # markdown emphasis (**word**), not a disguise
        tok = tok.strip("*")
    if not re.search(r"[a-z]", tok, re.I) or not re.search(r"[*#@$0-9!]", tok):
        return False
    low = tok.lower().translate(LEET)
    if "*" in low or "#" in low:
        pat = re.compile("^" + re.sub(r"[*#]", "[a-z]", re.escape(low).replace(r"\*", "*").replace(r"\#", "#")) + "$")
        return any(pat.match(w) for w in PLAIN if len(w) == len(low))
    return low in PLAIN


def swears(text, protected):
    """Swear words found, after removing the film's own title / character names (protected)."""
    raw = _URL_RE.sub(" ", _TAG_RE.sub(" ", text or ""))   # keeps * so f*ck stays visible
    for p in protected:
        if p:
            raw = re.sub(re.escape(p), " ", raw, flags=re.I)
    t = clean(raw)
    found = [m.group(0) for m in _SWEAR_RE.finditer(t)]
    found += [m.group(0) for m in _AMBIG_RE.finditer(t)]
    found += [tok for tok in _TOKEN_RE.findall(raw) if _disguised(tok)]
    return found


def english_share(words):
    return sum(w.lower() in ENGLISH_COMMON for w in words) / max(1, len(words))


def shouting(text):
    letters = [c for c in clean(text) if c.isalpha()]
    return len(letters) >= 40 and sum(c.isupper() for c in letters) / len(letters) > 0.5


def tone(words):
    low = [w.lower() for w in words]
    return sum(w in POSITIVE for w in low), sum(w in NEGATIVE for w in low)


def political_hits(text):
    low = " " + re.sub(r"[^a-z ]+", " ", (text or "").lower()) + " "
    return {p for p in POLITICAL if f" {p} " in low}


def norm_key(text):
    t = re.sub(r"[^a-z0-9]+", " ", clean(text).lower()).strip()
    return hashlib.sha1(t.encode()).hexdigest(), t


# ---------------------------------------------------------------- film context
def _terms(s):
    return {w.lower().strip("'’-") for w in _WORD_RE.findall(s or "")
            if len(w) >= 4 and w.lower() not in STOP}


def film_context(conn, fids):
    """Per film: film-specific terms (title, cast, characters, crew, keywords) and protected phrases."""
    ctx = {f: {"terms": set(), "protected": set(), "release": None} for f in fids}
    q = ",".join(str(int(f)) for f in fids) or "0"
    for fid, title, orig, coll, rel, us in conn.execute(
            f"select tmdb_id, title, original_title, collection_name, release_date, us_release_date "
            f"from films where tmdb_id in ({q})"):
        c = ctx[fid]
        for s in (title, orig, coll):
            if s:
                c["terms"] |= _terms(s)
                c["protected"].add(s)
        dates = [d for d in (rel, us) if d]
        c["release"] = min(dates) if dates else None
    for fid, name, char in conn.execute(
            f"select c.tmdb_id, p.name, c.character from credits c left join people p on p.person_id = c.person_id "
            f"where c.tmdb_id in ({q})"):
        c = ctx[fid]
        c["terms"] |= _terms(name) | _terms(char)
        if char:
            for part in re.split(r"\s*/\s*|\s*\(.*?\)\s*", char):
                if len(part) >= 3:
                    c["protected"].add(part.strip())
    try:
        for fid, name in conn.execute(f"select tmdb_id, name from keywords where tmdb_id in ({q})"):
            ctx[fid]["terms"] |= _terms(name)
    except sqlite3.Error:
        pass
    return ctx


def specific_hits(words, terms):
    return len({w.lower().strip("'’-") for w in words} & terms)


# ---------------------------------------------------------------- whole run
def _day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def run(conn):
    rows = conn.execute("select review_id, tmdb_id, author, rating, created_at, content from reviews").fetchall()
    fids = sorted({r[1] for r in rows if r[1] is not None})
    ctx = film_context(conn, fids)
    res = {}
    by_key = collections.defaultdict(list)
    for rid, fid, author, rating, created, content in rows:
        c = ctx.get(fid, {"terms": set(), "protected": set(), "release": None})
        words = _WORD_RE.findall(clean(content))
        reasons = []
        n_sent = full_sentences(content)
        if n_sent < MIN_SENTENCES:
            reasons.append("short")
        if len(words) >= 30 and english_share(words) < 0.15:
            reasons.append("not_english")
        elif swears(content, c["protected"]):
            reasons.append("swearing")
        hits = specific_hits(words, c["terms"])
        extreme = rating is not None and (rating <= LOW or rating >= HIGH)
        if extreme and hits == 0:
            reasons.append("generic_extreme")
        pos, neg = tone(words)
        if rating is not None and ((rating <= 3 and pos >= 4 and pos > 2 * neg) or
                                   (rating >= 9 and neg >= 4 and neg > 2 * pos)):
            reasons.append("rating_mismatch")
        rel, made = _day(c["release"]), _day(created)
        if extreme and rel and made and made < rel:
            reasons.append("early_extreme")
        if shouting(content):
            reasons.append("shouting")
        if len(_URL_RE.findall(content or "")) > 2:
            reasons.append("links")
        if len(political_hits(content)) >= 2 and hits <= 1:
            reasons.append("political")
        key, flat = norm_key(content)
        if len(flat) >= 40:
            by_key[key].append((created or "", rid, fid))
        res[rid] = {"tmdb_id": fid, "author": author, "rating": rating, "created": created,
                    "reasons": reasons, "n_sent": n_sent, "hits": hits}

    # duplicates and copy-paste spam
    for items in by_key.values():
        if len(items) < 2:
            continue
        if len({f for _, _, f in items}) > 1:
            for _, rid, _ in items:
                res[rid]["reasons"].append("copy_paste")
        else:
            for _, rid, _ in sorted(items)[1:]:
                res[rid]["reasons"].append("duplicate")

    # bursts of extreme ratings per film
    per_film = collections.defaultdict(list)
    for rid, r in res.items():
        per_film[r["tmdb_id"]].append(rid)
    for fid, rids in per_film.items():
        for side in ("low", "high"):
            ext = sorted(((_day(res[r]["created"]), r) for r in rids
                          if res[r]["rating"] is not None and _day(res[r]["created"]) and
                          (res[r]["rating"] <= LOW if side == "low" else res[r]["rating"] >= HIGH)),
                         key=lambda x: x[0])
            for i in range(len(ext)):
                window = [r for d, r in ext if 0 <= (d - ext[i][0]).days <= 7]
                if len(window) >= 5 and len(window) >= 0.5 * len(rids):
                    for r in window:
                        if "burst" not in res[r]["reasons"]:
                            res[r]["reasons"].append("burst")

    # authors whose ratings are all at one extreme
    by_author = collections.defaultdict(list)
    for rid, r in res.items():
        if r["author"] and r["rating"] is not None:
            by_author[r["author"]].append(r["rating"])
    one_note = {a for a, rs in by_author.items() if len(rs) >= 5 and (max(rs) <= LOW or min(rs) >= HIGH)}
    for r in res.values():
        if r["author"] in one_note:
            r["reasons"].append("one_note_author")
    return res, rows


ORDER = ["short", "not_english", "swearing", "duplicate", "copy_paste", "generic_extreme", "rating_mismatch",
         "early_extreme", "burst", "one_note_author", "shouting", "links", "political"]


def save(res):
    out = sqlite3.connect(OUT_DB)
    out.execute("drop table if exists review_quality")
    out.execute("create table review_quality(review_id text primary key, tmdb_id integer, author text, "
                "rating real, passed integer, reasons text, n_sentences integer, specific_hits integer, "
                "version text, checked_at text)")
    ts = F.now()
    out.executemany("insert into review_quality values (?,?,?,?,?,?,?,?,?,?)",
                    [(rid, r["tmdb_id"], r["author"], r["rating"], int(not r["reasons"]),
                      ",".join(r["reasons"]), r["n_sent"], r["hits"], VERSION, ts) for rid, r in res.items()])
    out.execute("create index if not exists rq_film on review_quality(tmdb_id, passed)")
    out.commit()
    out.close()


def summary(res):
    n = len(res)
    passed = [r for r in res.values() if not r["reasons"]]
    any_count = collections.Counter(x for r in res.values() for x in r["reasons"])
    first = collections.Counter(min(r["reasons"], key=ORDER.index) for r in res.values() if r["reasons"])
    films = collections.Counter(r["tmdb_id"] for r in res.values())
    films_ok = collections.Counter(r["tmdb_id"] for r in passed)
    lines = [f"Review quality filter {VERSION}, {F.now()}",
             f"Reviews checked: {n:,}",
             f"Passed every check: {len(passed):,} ({100 * len(passed) / max(1, n):.1f}%)",
             f"Films with any review: {len(films):,}; with 1+ passing review: {len(films_ok):,}; "
             f"with {EVIDENCE_MIN}+ passing reviews (enough to make claims): "
             f"{sum(1 for v in films_ok.values() if v >= EVIDENCE_MIN):,}",
             "",
             f"{'check':18s} {'fails (any)':>12s} {'first reason':>13s}"]
    for k in ORDER:
        lines.append(f"{k:18s} {any_count[k]:>12,} {first[k]:>13,}")
    lines += ["", "'fails (any)' counts every review failing that check (a review can fail several);",
              "'first reason' counts each failed review once, at its first failing check in the order above."]
    return "\n".join(lines)


def cmd_show(conn, title):
    film = conn.execute("select tmdb_id, title, year from films where title = ? collate nocase "
                        "order by vote_count desc limit 1", (title,)).fetchone()
    if not film:
        sys.exit(f"no film titled {title!r} in facts.db")
    out = sqlite3.connect(OUT_DB)
    print(f"{film[1]} ({film[2]})")
    for rid, rating, ok, reasons in out.execute(
            "select review_id, rating, passed, reasons from review_quality where tmdb_id = ? order by passed desc",
            (film[0],)):
        text = conn.execute("select content from reviews where review_id = ?", (rid,)).fetchone()[0] or ""
        print(f"  {'PASS' if ok else 'fail'} {str(rating):>5s}  {reasons or '-':30s} {clean(text)[:90]!r}")


# ------------------------------------------------------------------ v4: model check + usable set
MODEL_VERSION = "rm1"
MODEL_SCHEMA = {"type": "object", "properties": {
    "label": {"type": "string", "enum": ["genuine", "troll", "spam", "off_topic"]},
    "reason": {"type": "string"}}, "required": ["label", "reason"]}
MODEL_SYSTEM = ("You check one user review of a film. label: 'genuine' if it is a real opinion about THIS film "
                "(any rating, positive or negative), 'troll' if it is written to provoke or insult rather than to "
                "review, 'spam' if it advertises or is unrelated boilerplate, 'off_topic' if it is mostly about "
                "something other than the film (politics, the cinema, delivery). reason: one short sentence. JSON only.")


def _out_conn():
    out = sqlite3.connect(OUT_DB)
    out.execute("create table if not exists review_model(review_id text primary key, label text, reason text, "
                "model text, version text, checked_at text)")
    return out


def cmd_model(conn, limit, model=None):
    if os.environ.get("MOCK", "off").lower() not in ("", "off", "0"):
        import mock_llm
        mock_llm.install()
    import json
    import ollama
    model = model or os.environ.get("REVIEW_CHECK_MODEL", "llama3.2")
    out = _out_conn()
    done = {r[0] for r in out.execute("select review_id from review_model where version = ?", (MODEL_VERSION,))}
    todo = [(rid, fid) for rid, fid in out.execute("select review_id, tmdb_id from review_quality where passed = 1")
            if rid not in done]
    if limit:
        todo = todo[:limit]
    print(f"model check ({model}): {len(todo):,} passing reviews to label")
    counts = collections.Counter()
    for i, (rid, fid) in enumerate(todo, 1):
        title = (conn.execute("select title from films where tmdb_id = ?", (fid,)).fetchone() or [""])[0]
        text = (conn.execute("select content from reviews where review_id = ?", (rid,)).fetchone() or [""])[0] or ""
        label, reason = None, ""
        for n_pred in (150, 400):
            try:
                r = ollama.chat(model=model, format=MODEL_SCHEMA, keep_alive="30m",
                                options={"temperature": 0, "num_predict": n_pred},
                                messages=[{"role": "system", "content": MODEL_SYSTEM},
                                          {"role": "user", "content": f"Film: {title}\nReview:\n{clean(text)[:2500]}"}])
                d = json.loads(r["message"]["content"])
                label, reason = d.get("label"), str(d.get("reason") or "")[:300]
                break
            except Exception:  # noqa: BLE001 - truncated JSON: once more with room
                continue
        if label not in ("genuine", "troll", "spam", "off_topic"):
            continue
        counts[label] += 1
        out.execute("insert or replace into review_model values (?,?,?,?,?,?)",
                    (rid, label, reason, model, MODEL_VERSION, F.now()))
        if i % 100 == 0 or i == len(todo):
            out.commit()
            print(f"  {i:,}/{len(todo):,}  " + ", ".join(f"{k} {v}" for k, v in counts.items()), flush=True)
    out.commit()


def usable_ids(path=None):
    """review_ids fit to use as evidence (None when review_quality.db does not exist)."""
    p = path or OUT_DB
    if not os.path.exists(p):
        return None
    out = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        has_model = out.execute("select 1 from sqlite_master where name = 'review_model'").fetchone()
        sql = ("select q.review_id from review_quality q left join review_model m on m.review_id = q.review_id "
               "where q.passed = 1 and (m.label is null or m.label = 'genuine')") if has_model else \
            "select review_id from review_quality where passed = 1"
        return {r[0] for r in out.execute(sql)}
    finally:
        out.close()


def cmd_score(path):
    """Agreement with hand labels (the my_label column of review_label_sample.csv)."""
    out = _out_conn()
    model = dict(out.execute("select review_id, label from review_model"))
    tp = fp = fn = tn = 0
    n = 0
    for row in csv.DictReader(open(path, encoding="utf-8")):
        lab = (row.get("my_label (genuine/troll/spam)") or "").strip().lower()
        if not lab:
            continue
        n += 1
        bad = lab != "genuine"
        flagged = row["filter_passed"] in ("0", "False") or model.get(row["review_id"], "genuine") != "genuine"
        tp += bad and flagged
        fp += (not bad) and flagged
        fn += bad and not flagged
        tn += (not bad) and not flagged
    if not n:
        sys.exit("no labelled rows yet: fill in the my_label column")
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    print(f"{n} labelled reviews: filter (rules + model) catches {tp} of {tp + fn} bad reviews (recall {rec:.2f}); "
          f"{fp} of {fp + tn} genuine reviews wrongly removed; precision {prec:.2f}")


def cmd_sample(conn, n):
    out = sqlite3.connect(OUT_DB)
    rows = out.execute("select review_id, tmdb_id, rating, passed, reasons from review_quality").fetchall()
    random.Random(8).shuffle(rows)
    half = n // 2
    pick = [r for r in rows if r[3]][:half] + [r for r in rows if not r[3]][:n - half]
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    path = os.path.join(HERE, "results", "review_label_sample.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "film", "rating", "filter_passed", "reasons", "my_label (genuine/troll/spam)", "text"])
        for rid, fid, rating, ok, reasons in pick:
            title = (conn.execute("select title from films where tmdb_id = ?", (fid,)).fetchone() or [""])[0]
            text = conn.execute("select content from reviews where review_id = ?", (rid,)).fetchone()[0] or ""
            w.writerow([rid, title, rating, ok, reasons, "", clean(text)[:1500]])
    print(f"wrote {len(pick)} reviews ({half} passed, {len(pick) - half} failed) to {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "show", "sample", "model", "score"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "score":
        return cmd_score(a.arg or os.path.join(HERE, "results", "review_label_sample.csv"))
    conn = F.connect(readonly=True)
    if conn is None:
        sys.exit(f"facts.db not found at {F.PATH} (set FACTS_DB)")
    if a.cmd == "show":
        return cmd_show(conn, a.arg or "")
    if a.cmd == "sample":
        return cmd_sample(conn, int(a.arg or 100))
    if a.cmd == "model":
        return cmd_model(conn, a.limit)
    res, _ = run(conn)
    save(res)
    text = summary(res)
    print(text)
    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    with open(SUMMARY, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\nsaved: {OUT_DB} (table review_quality), {SUMMARY}")


if __name__ == "__main__":
    main()
