"""Generated "tip of the tongue" descriptions for each film (doc2query), for the v2 index.

For every linked film with a Wikipedia plot, a small model writes N short, vague
descriptions of the kind people type when they can't remember a film's name. They
are stored in wiki.db (gen_questions) and added to chunks as kind='question', so
wiki_embed.py --kinds question embeds them and retrieve.py searches them
(WIKI_KINDS includes "question" by default). It is the build-time version of HyDE:
instead of guessing a plot for each question at ask time, guess the questions each
plot would get, once.

    python wiki_enrich.py --limit 200 --show 5          # trial: speed + samples
    python wiki_enrich.py --reset --limit 100 --show 5  # start over (e.g. after changing the prompt)
    python wiki_enrich.py                               # everything, most popular first
    python wiki_embed.py --wiki-db wiki.db --kinds question

Resumable: films that already have questions are skipped; safe to Ctrl-C.
Films are done in popularity order, never in eval order.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import wiki_common as wc

STYLES = [
    "short: one sentence of at most 15 words, the single thing they remember best",
    "scene: one specific scene, image or moment they remember seeing, at most 30 words",
    "fuzzy: saw it years ago, unsure about some details, mentions two or three things (setting, era, what happens), at most 45 words",
    "childhood: saw it as a kid on TV, remembers the feel and one detail, at most 35 words",
]

SYSTEM = """You write the kind of message people post when they are trying to remember a film but cannot recall its name.
Rules:
- Never use the film's title, character names or actor names. Refer to people by what they are or what they do.
- Sound like a real person remembering: plain words, a little uncertainty, not a plot summary.
- Use only details that are in the text you are given. Do not add anything else.
- Keep every description short and inside its word limit.
Reply with JSON only: {"descriptions": ["...", "..."]}"""

SCHEMA = {"type": "object",
          "properties": {"descriptions": {"type": "array", "items": {"type": "string"}}},
          "required": ["descriptions"]}


def build_user(title, year, genres, plot, intro, n):
    styles = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(STYLES[:n]))
    return (f"Film (do not name it): {title} ({year or 'year unknown'}), genres: {', '.join(genres) or 'unknown'}\n\n"
            f"Wikipedia intro:\n{intro[:600]}\n\nPlot:\n{plot[:2600]}\n\n"
            f"Write {n} descriptions, one in each of these styles:\n{styles}")


def call(url, model, user, n, timeout, no_think):
    body = {"model": model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
            "options": {"temperature": 0.8, "num_predict": 70 * n + 60},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    if no_think:
        body["think"] = False
    req = urllib.request.Request(url + "/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    text = out.get("message", {}).get("content", "")
    return json.loads(text).get("descriptions", []), int(out.get("eval_count") or 0)


def clean(descs, title, names, n):
    """Drop descriptions that give away the title or a name, or are too short."""
    nt = wc.norm_title(title)
    keep = []
    for i, d in enumerate(descs):
        d = re.sub(r"\s+", " ", str(d)).strip().strip('"')
        nd = " " + wc.norm_title(d) + " "
        if len(d.split()) < 6:
            continue
        if len(nt) >= 4 and f" {nt} " in nd:
            continue
        if any(f" {x} " in nd for x in names):
            continue
        keep.append((min(i, len(STYLES) - 1), d))
    return keep[:n]


def cast_names(infobox_json):
    """Lowercased surname tokens from the infobox 'starring' field, to catch leaked names."""
    try:
        box = json.loads(infobox_json or "{}")
    except json.JSONDecodeError:
        return set()
    raw = " ".join(str(box.get(k, "")) for k in ("starring", "director", "narrator"))
    names = re.findall(r"\[\[([^\]|]+)", raw) or re.split(r"[|,\n]", raw)
    out = set()
    for nm in names:
        parts = wc.norm_title(nm).split()
        if parts and len(parts[-1]) >= 4:
            out.add(parts[-1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--model", default="llama3.2")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--n", type=int, default=3, help="descriptions per film (max 4)")
    ap.add_argument("--reset", action="store_true", help="delete all generated descriptions first")
    ap.add_argument("--fix-styles", action="store_true",
                    help="copy each description's style into chunks.section (for WIKI_QSTYLES), then exit")
    ap.add_argument("--workers", type=int, default=4, help="requests in flight")
    ap.add_argument("--limit", type=int, help="stop after this many films (trial)")
    ap.add_argument("--show", type=int, default=0, help="print this many samples")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--no-think", action="store_true", help="send think=false (Qwen3-style models)")
    a = ap.parse_args()
    a.n = max(1, min(a.n, len(STYLES)))

    db = sqlite3.connect(a.wiki_db)
    db.executescript("""
    create table if not exists gen_questions(film_id, idx integer, style text, text text, model text,
                                             created text, primary key (film_id, idx));
    """)
    if a.fix_styles:
        n = db.execute("""update chunks set section = (select g.style from gen_questions g
                              where g.film_id = chunks.film_id and g.idx = chunks.position - 1)
                          where kind = 'question'""").rowcount
        db.commit()
        print(f"styles set on {n} descriptions:",
              db.execute("select section, count(*) from chunks where kind = 'question' group by section").fetchall())
        return
    if a.reset:
        try:                      # vectors too, if any were embedded
            import sqlite_vec
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.execute("delete from vec_chunks where kind = 'question'")
        except Exception:  # noqa: BLE001  (no vec table yet / extension missing)
            pass
        db.execute("delete from chunks where kind = 'question'")
        db.execute("delete from gen_questions")
        db.commit()
        print("reset: removed all generated descriptions")
    done = {r[0] for r in db.execute("select distinct film_id from gen_questions")}
    # Aggregate chunks per film first, then look pages up by an index. (Joining first
    # made SQLite rescan all 25k pages for each of 76k plot chunks - hours.)
    db.execute("create index if not exists pages_film on pages(film_id)")
    db.commit()
    films = db.execute("""
        select p.film_id, p.title, p.sections_json, p.infobox_json, c.pop, c.year, c.genres
        from (select film_id, max(popularity) pop, max(year) year, max(genres_json) genres
              from chunks where kind = 'plot' group by film_id) c
        join pages p on p.film_id = c.film_id
        order by c.pop desc""").fetchall()
    todo = [f for f in films if f[0] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"films with a plot: {len(films)} | already done: {len(done)} | this run: {len(todo)} "
          f"| model {a.model} | {a.workers} in flight", flush=True)
    if not todo:
        return

    meta_cols = ("film_id, doc_id, title, year, decade, language, is_animated, genres_json, tier, "
                 "popularity, url, snapshot, link_method, link_conf")

    def job(f):
        fid, title, sections, infobox, _pop, year, genres = f
        s = json.loads(sections or "{}")
        user = build_user(title, year, json.loads(genres or "[]"), s.get("plot", ""), s.get("abstract", ""), a.n)
        try:
            descs, toks = call(a.ollama, a.model, user, a.n, a.timeout, a.no_think)
        except json.JSONDecodeError:      # output ran past its budget; one more try
            descs, toks = call(a.ollama, a.model, user, a.n, a.timeout, a.no_think)
        return fid, title, clean(descs, title, cast_names(infobox), a.n), toks

    t0, n_done, n_tok, n_q, shown = time.time(), 0, 0, 0, 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(job, f): f for f in todo}
        try:
            for fut in cf.as_completed(futs):
                try:
                    fid, title, descs, toks = fut.result()
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
                    print(f"  skipped {futs[fut][1]}: {e}", file=sys.stderr)
                    continue
                if not descs:
                    continue
                meta = db.execute(f"select {meta_cols} from chunks where film_id = ? and kind = 'plot' limit 1",
                                  (fid,)).fetchone()
                for i, (style_i, d) in enumerate(descs):
                    db.execute("insert or replace into gen_questions values (?,?,?,?,?,?)",
                               (fid, i, STYLES[style_i].split(":")[0], d, a.model, now))
                    db.execute(f"insert into chunks({meta_cols}, kind, section, position, n_parts, is_ending, "
                               "source, text, text_hash, n_words) values "
                               f"({','.join('?' * 14)}, 'question', ?, ?, ?, 0, ?, ?, ?, ?)",
                               (*meta, STYLES[style_i].split(":")[0], i + 1, len(descs), "llm:" + a.model, d,
                                wc.hashlib_sha1(d), len(d.split())))
                db.commit()
                n_done += 1
                n_tok += toks
                n_q += len(descs)
                if shown < a.show:
                    shown += 1
                    print(f"\n--- {title}")
                    for style_i, d in descs:
                        print(f"  * [{STYLES[style_i].split(':')[0]}] {d}")
                if n_done % 50 == 0 or n_done == len(todo):
                    el = time.time() - t0
                    left = (len(films) - len(done) - n_done) * el / n_done / 3600
                    print(f"  {n_done}/{len(todo)} films | {n_done / el * 60:.1f} films/min | "
                          f"{n_tok / el:.0f} tokens/s | {n_q / n_done:.1f} kept/film | "
                          f"all remaining films: ~{left:.1f} h", flush=True)
        except KeyboardInterrupt:
            print("\nstopping; finished films are saved", file=sys.stderr)
            ex.shutdown(wait=False, cancel_futures=True)
    print(f"done: {n_done} films, {n_q} descriptions. Next: python wiki_embed.py --wiki-db {a.wiki_db} --kinds question")


if __name__ == "__main__":
    main()
