"""E10: the Wikipedia plot text again, in smaller chunks (kind 'plot_s'), next to the existing ones.

The existing plot chunks are ~180 words (wiki_chunks.py). A tip-of-the-tongue description
usually remembers one scene, and in a 180-word chunk that scene shares the vector with
several others. plot_s cuts the same cleaned plot text (pages.sections_json) into ~80-word
runs of whole sentences with one sentence of overlap. Nothing existing is changed: the new
rows get new chunk ids, and retrieve.py only searches them when WIKI_KINDS includes plot_s.

    cp wiki.db wiki.db.bak-e10                 # first (needs ~1x wiki.db of disk)
    python wiki_small.py                       # seconds; prints counts
    python wiki_embed.py --wiki-db wiki.db --kinds plot_s --batch 64    # resumable
    python mb.py build-cache                   # the vector cache must include them

    python wiki_small.py --replace --target-words 60 --max-words 90    # try another size
    python wiki_small.py --drop                                         # remove plot_s entirely
"""
import argparse
import collections
import hashlib
import json
import sqlite3

import wiki_common as wc

KIND = "plot_s"
COPY = ["film_id", "doc_id", "title", "section", "year", "decade", "language", "is_animated",
        "genres_json", "tier", "popularity", "source", "url", "snapshot", "link_method", "link_conf"]


def open_db(path):
    db = sqlite3.connect(path)
    import sqlite_vec
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def drop(db):
    ids = [r[0] for r in db.execute("SELECT chunk_id FROM chunks WHERE kind = ?", (KIND,))]
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'vec_chunks'").fetchone():
        db.executemany("DELETE FROM vec_chunks WHERE chunk_id = ?", [(i,) for i in ids])
    db.execute("DELETE FROM chunks WHERE kind = ?", (KIND,))
    db.commit()
    return len(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--target-words", type=int, default=80)
    ap.add_argument("--max-words", type=int, default=120)
    ap.add_argument("--replace", action="store_true", help="delete existing plot_s chunks + vectors first")
    ap.add_argument("--drop", action="store_true", help="delete plot_s chunks + vectors and stop")
    a = ap.parse_args()

    db = open_db(a.wiki_db)
    have = db.execute("SELECT count(*) FROM chunks WHERE kind = ?", (KIND,)).fetchone()[0]
    if a.drop:
        print(f"dropped {drop(db)} {KIND} chunks and their vectors")
        return
    if have and not a.replace:
        raise SystemExit(f"{have} {KIND} chunks already exist. --replace to rebuild them, --drop to remove.")
    if have:
        print(f"replacing {drop(db)} existing {KIND} chunks")

    # one existing plot chunk per film carries the metadata to copy
    meta = {}
    for row in db.execute(f"SELECT {', '.join(COPY)} FROM chunks WHERE kind = 'plot' AND position = 1"):
        meta[row[0]] = dict(zip(COPY, row))
    rows, per_film, words = [], collections.Counter(), []
    for fid, sections in db.execute("SELECT film_id, sections_json FROM pages"):
        m = meta.get(fid)
        if m is None:
            continue
        plot = (json.loads(sections or "{}").get("plot") or "").strip()
        if not plot:
            continue
        parts = wc.chunk_text(plot, a.target_words, a.max_words)
        for i, part in enumerate(parts, 1):
            n = len(part.split())
            rows.append(dict(m, kind=KIND, position=i, n_parts=len(parts),
                             is_ending=int(i == len(parts) and len(parts) > 1), text=part,
                             text_hash=hashlib.sha1(part.encode()).hexdigest(), n_words=n))
            per_film[fid] += 1
            words.append(n)
    cols = COPY + ["kind", "position", "n_parts", "is_ending", "text", "text_hash", "n_words"]
    db.executemany(f"INSERT INTO chunks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   [tuple(r[c] for c in cols) for r in rows])
    db.commit()
    old = db.execute("SELECT count(*), avg(n_words) FROM chunks WHERE kind = 'plot'").fetchone()
    words.sort()
    print(f"{KIND}: {len(rows):,} chunks for {len(per_film):,} films "
          f"({len(rows) / max(len(per_film), 1):.1f} per film); words median {words[len(words) // 2]}, "
          f"max {words[-1]}")
    print(f"plot (unchanged): {old[0]:,} chunks, {old[1] or 0:.0f} words on average")
    print(f"next: python wiki_embed.py --wiki-db {a.wiki_db} --kinds {KIND} --batch 64")


if __name__ == "__main__":
    main()
