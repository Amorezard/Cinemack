"""Step 5: sanity-check the new Wikipedia index with a vague description.

python wiki_try.py "a man keeps waking up on the same day in a small town"
python wiki_try.py "..." --kind plot --year 1993      # year as a hard filter, only when sure
Shows the top films (best passage per film) and the passage that matched.
"""
import argparse
import json
import sqlite3
import time
import urllib.request

import sqlite_vec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--kind", default="plot")
    ap.add_argument("--year", type=int)
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--styles", help="with --kind question: only these styles, e.g. short,scene")
    a = ap.parse_args()

    db = sqlite3.connect(a.wiki_db)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    man = dict(db.execute("select key, value from manifest"))
    t0 = time.time()
    body = json.dumps({"model": man["embedder"], "input": [man.get("query_prefix", "") + a.query]}).encode()
    req = urllib.request.Request(a.ollama + "/api/embed", body, {"Content-Type": "application/json"})
    qv = json.load(urllib.request.urlopen(req, timeout=120))["embeddings"][0]
    t1 = time.time()

    sql = "select chunk_id, distance, film_id from vec_chunks where embedding match ? and k = ? and kind = ?"
    args = [sqlite_vec.serialize_float32(qv), a.k, a.kind]
    if a.year:
        sql += " and year = ?"
        args.append(a.year)
    hits = db.execute(sql, args).fetchall()
    t2 = time.time()

    if a.styles:
        keep = set(a.styles.split(","))
        hits = [h for h in hits if db.execute("select section from chunks where chunk_id = ?",
                                              (h[0],)).fetchone()[0] in keep]
    best = {}
    for cid, dist, fid in hits:
        if fid not in best or dist < best[fid][0]:
            best[fid] = (dist, cid)
    print(f"embed {1000 * (t1 - t0):.0f} ms | search {1000 * (t2 - t1):.0f} ms | {len(hits)} passages, "
          f"{len(best)} films\n")
    for rank, (fid, (dist, cid)) in enumerate(sorted(best.items(), key=lambda x: x[1][0])[:10], 1):
        title, year, pos, n, text, sec = db.execute(
            "select title, year, position, n_parts, text, section from chunks where chunk_id = ?", (cid,)).fetchone()
        print(f"{rank:2d}. {title} ({year})  sim {1 - dist:.3f}  [{sec}, part {pos}/{n}]")
        print(f"    {text[:160]}...")


if __name__ == "__main__":
    main()
