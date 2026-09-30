"""Step 3: turn linked Wikipedia pages into chunks with metadata. Writes tables pages + chunks in wiki.db.

python wiki_chunks.py --movies movies.db --corpus /path/corpus.jsonl.zip --wiki-db wiki.db

kinds written: lead (article intro) and plot (Plot/Synopsis/Premise ...).
Refuses to rebuild once vectors exist, unless --rebuild (which also drops the vectors).
"""
import argparse
import collections
import hashlib
import json
import sqlite3
import time

import wiki_common as wc

SCHEMA = """
create table if not exists pages(
  doc_id text primary key, film_id, title text, wikidata_id text, url text, snapshot text,
  plot_key text, sections_json text, infobox_json text);
create table if not exists chunks(
  chunk_id integer primary key,
  film_id, doc_id text, title text,
  kind text, section text, position integer, n_parts integer, is_ending integer,
  year integer, decade integer, language text, is_animated integer, genres_json text,
  tier integer, popularity real,
  source text, url text, snapshot text, link_method text, link_conf real,
  text text, text_hash text, n_words integer);
create index if not exists chunks_film on chunks(film_id);
create index if not exists chunks_kind on chunks(kind);
create index if not exists pages_film on pages(film_id);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--movies", default="movies.db")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--table")
    ap.add_argument("--col", action="append")
    ap.add_argument("--target-words", type=int, default=180)
    ap.add_argument("--max-words", type=int, default=260)
    ap.add_argument("--min-conf", type=float, default=0.6)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()

    _, _, movies = wc.load_movies(a.movies, a.table, wc.parse_overrides(a.col))
    db = sqlite3.connect(a.wiki_db)
    try:                      # needed to drop vec_chunks on --rebuild
        import sqlite_vec
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
    except Exception:  # noqa: BLE001
        pass
    has_vec = db.execute("select 1 from sqlite_master where name='vec_chunks'").fetchone()
    if has_vec and not a.rebuild:
        raise SystemExit("vec_chunks already exists; chunk ids would no longer match. Use --rebuild to start over.")
    if a.rebuild:
        db.executescript("drop table if exists vec_chunks; drop table if exists pages; drop table if exists chunks;")
    db.executescript("delete from chunks;" if db.execute(
        "select 1 from sqlite_master where name='chunks'").fetchone() else "")
    db.executescript(SCHEMA)
    db.execute("delete from pages")

    links = {}
    for fid, did, meth, conf in db.execute("select film_id, doc_id, method, conf from links where conf >= ?",
                                           (a.min_conf,)):
        links[str(did)] = (fid, meth, conf)
    print(f"linked pages to process: {len(links)}")

    t0 = time.time()
    stats = collections.Counter()
    rows, page_rows, seen = [], [], set()
    for doc in wc.open_corpus(a.corpus):
        did = str(doc.get("doc_id"))
        if did not in links or did in seen:
            continue
        seen.add(did)
        fid, meth, conf = links[did]
        m = movies.get(fid) or movies.get(str(fid))
        if m is None:
            continue
        secs = doc.get("sections") or {}
        heads = set(secs)
        lead = wc.clean_section(secs.get("abstract", ""), heads)
        plot_key, plot = wc.pick_plot(secs)
        extra = {k: wc.clean_section(secs[k], heads) for k in wc.EXTRA_KEYS if secs.get(k)}
        url = f"https://en.wikipedia.org/?curid={did}"
        page_rows.append((did, fid, m["title"], doc.get("wikidata_id", ""), url, wc.SNAPSHOT, plot_key,
                          json.dumps({"abstract": lead, "plot": plot, **extra}, ensure_ascii=False),
                          json.dumps(wc.film_infobox(doc), ensure_ascii=False)))
        base = dict(
            film_id=fid, doc_id=did, title=m["title"], year=m["year"],
            decade=(m["year"] // 10 * 10) if m["year"] else 0, language=m["language"],
            is_animated=int(any(g.lower() == "animation" for g in m["genres"])),
            genres_json=json.dumps(m["genres"]), tier=0, popularity=wc.popularity(m["votes"]),
            source="wikipedia", url=url, snapshot=wc.SNAPSHOT, link_method=meth, link_conf=conf)
        for kind, section, text in (("lead", "intro", lead), ("plot", plot_key or "plot", plot)):
            if not text:
                stats[f"no_{kind}"] += 1
                continue
            parts = wc.chunk_text(text, a.target_words, a.max_words)
            for i, part in enumerate(parts, 1):
                rows.append(dict(base, kind=kind, section=section, position=i, n_parts=len(parts),
                                 is_ending=int(kind == "plot" and i == len(parts) and len(parts) > 1),
                                 text=part, text_hash=hashlib.sha1(part.encode()).hexdigest(),
                                 n_words=len(part.split())))
                stats[kind] += 1
        stats["pages"] += 1
        if stats["pages"] % 5000 == 0:
            print(f"  {stats['pages']} pages, {time.time() - t0:.0f}s")

    cols = ["film_id", "doc_id", "title", "kind", "section", "position", "n_parts", "is_ending", "year",
            "decade", "language", "is_animated", "genres_json", "tier", "popularity", "source", "url",
            "snapshot", "link_method", "link_conf", "text", "text_hash", "n_words"]
    db.executemany(f"insert into chunks({','.join(cols)}) values ({','.join('?' * len(cols))})",
                   [tuple(r[c] for c in cols) for r in rows])
    db.executemany("insert or replace into pages values (?,?,?,?,?,?,?,?,?)", page_rows)
    db.commit()

    words = [r["n_words"] for r in rows]
    print("\n== chunk report")
    print(f"pages: {stats['pages']} | lead chunks: {stats['lead']} | plot chunks: {stats['plot']}")
    print(f"pages without plot: {stats['no_plot']} | without intro: {stats['no_lead']}")
    if words:
        words.sort()
        print(f"words per chunk: median {words[len(words) // 2]}, max {words[-1]}")
    print(f"wrote chunks to {a.wiki_db} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
