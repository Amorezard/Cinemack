"""Step 1: check that movies.db, the TREC corpus, sqlite-vec and Ollama look the way the pipeline expects.

python wiki_inspect.py --movies movies.db --corpus /path/to/corpus.jsonl.zip
Paste the output back to Claude if anything says WARN.
"""
import argparse
import collections
import json
import sqlite3
import time
import urllib.request

import wiki_common as wc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--movies", default="movies.db")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--table")
    ap.add_argument("--col", action="append", help="override a column, e.g. --col votes=vote_count")
    ap.add_argument("--scan", type=int, default=20000, help="corpus records to sample")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    a = ap.parse_args()

    print("== movies.db")
    con = sqlite3.connect(f"file:{a.movies}?mode=ro", uri=True)
    print("tables:", wc.list_plain_tables(con))
    table, mapping, movies = wc.load_movies(a.movies, a.table, wc.parse_overrides(a.col))
    print("movies table:", table, "| rows:", len(movies))
    print("column mapping:", json.dumps(mapping))
    for key in ("year", "genres", "language", "votes"):
        if not mapping.get(key) and not (key == "year" and mapping.get("release_date")):
            print(f"WARN no column found for {key}; pass --col {key}=<name> if it exists")
    sample = list(movies.values())[:3]
    for m in sample:
        print("  sample:", json.dumps(m, ensure_ascii=False)[:300])
    print("with imdb id:", sum(1 for m in movies.values() if m["imdb"]),
          "| with wikidata id:", sum(1 for m in movies.values() if m["wikidata"]),
          "| with year:", sum(1 for m in movies.values() if m["year"]))

    print("\n== corpus (first %d records)" % a.scan)
    keys = collections.Counter()
    secs = collections.Counter()
    n = films = plots = 0
    first = None
    for doc in wc.open_corpus(a.corpus):
        if first is None:
            first = doc
        n += 1
        keys.update(doc.keys())
        secs.update((doc.get("sections") or {}).keys())
        if wc.is_film(doc):
            films += 1
            plots += wc.has_plot(doc)
        if n >= a.scan:
            break
    print("fields:", dict(keys))
    print("first record:", first.get("doc_id"), first.get("page_title"), first.get("wikidata_id"))
    print("film pages: %d/%d, of which with a usable plot section: %d" % (films, n, plots))
    print("top sections:", secs.most_common(25))

    print("\n== sqlite-vec")
    try:
        import sqlite_vec
        c2 = sqlite3.connect(":memory:")
        c2.enable_load_extension(True)
        sqlite_vec.load(c2)
        v = c2.execute("select vec_version()").fetchone()[0]
        print("version:", v)
        nums = tuple(int(x) for x in v.lstrip("v").split("-")[0].split(".")[:3])
        if nums < (0, 1, 6):
            print("WARN need >= 0.1.6 for partition keys / metadata columns: pip install -U sqlite-vec")
    except Exception as e:  # noqa: BLE001
        print("WARN sqlite-vec not loadable:", e)

    print("\n== ollama embeddings")
    try:
        texts = ["a test sentence about a movie"] * 32
        body = json.dumps({"model": a.embed_model, "input": texts}).encode()
        t0 = time.time()
        req = urllib.request.Request(a.ollama + "/api/embed", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
        dt = time.time() - t0
        print("dim:", len(out["embeddings"][0]), "| 32 texts in %.2fs" % dt)
    except Exception as e:  # noqa: BLE001
        print("WARN ollama embed failed:", e)


if __name__ == "__main__":
    main()
