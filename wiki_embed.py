"""Step 4: embed chunks with Ollama into a sqlite-vec table with metadata. Resumable; safe to Ctrl-C and rerun.

python wiki_embed.py --wiki-db wiki.db
python wiki_embed.py --wiki-db wiki.db --kinds plot        # only one kind

vec_chunks columns: chunk_id (pk), embedding, kind (partition key), year, language, is_animated, tier
(metadata, filterable during KNN), +film_id (auxiliary, returned only).
"""
import argparse
import datetime
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import sqlite_vec

import wiki_common as wc


def embed(url, model, texts):
    body = json.dumps({"model": model, "input": texts, "truncate": True}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(url + "/api/embed", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["embeddings"]
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  embed retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("Ollama embedding kept failing")


def open_db(path):
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    v = db.execute("select vec_version()").fetchone()[0]
    nums = tuple(int(x) for x in v.lstrip("v").split("-")[0].split(".")[:3])
    if nums < (0, 1, 6):
        raise SystemExit(f"sqlite-vec {v} is too old; need >= 0.1.6 (pip install -U sqlite-vec)")
    return db, v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki-db", default="wiki.db")
    ap.add_argument("--model", default="mxbai-embed-large")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--kinds", default="lead,plot")
    ap.add_argument("--build-id", default="v2-" + datetime.date.today().strftime("%m%d"))
    a = ap.parse_args()

    db, vec_version = open_db(a.wiki_db)
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()]
    todo_sql = (f"select chunk_id, title, kind, section, position, n_parts, text, year, language, "
                f"is_animated, tier, film_id from chunks where kind in ({','.join('?' * len(kinds))}) "
                f"order by popularity desc, chunk_id")
    rows = db.execute(todo_sql, kinds).fetchall()

    exists = db.execute("select 1 from sqlite_master where name='vec_chunks'").fetchone()
    if exists:
        done = {r[0] for r in db.execute("select chunk_id from vec_chunks")}
        dim = int(db.execute("select value from manifest where key='dim'").fetchone()[0])
        old_model = db.execute("select value from manifest where key='embedder'").fetchone()[0]
        if old_model != a.model:
            raise SystemExit(f"vec_chunks was built with {old_model}, not {a.model}")
    else:
        done = set()
        dim = len(embed(a.ollama, a.model, ["dimension probe"])[0])
        db.executescript(f"""
        create virtual table vec_chunks using vec0(
          chunk_id integer primary key,
          embedding float[{dim}] distance_metric=cosine,
          kind text partition key,
          year integer,
          language text,
          is_animated integer,
          tier integer,
          +film_id integer
        );
        create table if not exists manifest(key text primary key, value text);
        """)
        meta = {"embedder": a.model, "dim": dim, "build_id": a.build_id, "vec_version": vec_version,
                "header_format": "Title | Section, part i of n: text", "snapshot": wc.SNAPSHOT,
                "query_prefix": "Represent this sentence for searching relevant passages: ",
                "created": datetime.datetime.now().isoformat(timespec="seconds")}
        db.executemany("insert or replace into manifest values (?,?)", [(k, str(v)) for k, v in meta.items()])
        db.commit()

    todo = [r for r in rows if r[0] not in done]
    print(f"chunks: {len(rows)} | already embedded: {len(rows) - len(todo)} | to do: {len(todo)} | dim {dim}")
    t0, n = time.time(), 0
    ins = ("insert into vec_chunks(chunk_id, embedding, kind, year, language, is_animated, tier, film_id) "
           "values (?,?,?,?,?,?,?,?)")
    for s in range(0, len(todo), a.batch):
        part = todo[s:s + a.batch]
        # generated descriptions are embedded as written (they read like questions, not passages)
        texts = [("" if r[2] == "question" else wc.embed_header(r[1], r[3], r[4], r[5])) + r[6]
                 for r in part]
        vecs = embed(a.ollama, a.model, texts)
        db.executemany(ins, [(r[0], sqlite_vec.serialize_float32(v), r[2], int(r[7] or 0), r[8] or "",
                              int(r[9] or 0), int(r[10] or 0), int(r[11]))
                             for r, v in zip(part, vecs)])
        db.commit()
        n += len(part)
        if (s // a.batch) % 25 == 0 or n == len(todo):
            rate = n / max(time.time() - t0, 1e-6)
            eta = (len(todo) - n) / max(rate, 1e-6) / 60
            print(f"  {n}/{len(todo)}  {rate:.0f} chunks/s  eta {eta:.1f} min", flush=True)

    db.execute("insert or replace into manifest values ('finished', ?)",
               (datetime.datetime.now().isoformat(timespec="seconds"),))
    db.execute("insert or replace into manifest values ('n_vectors', ?)",
               (str(db.execute("select count(*) from vec_chunks").fetchone()[0]),))
    db.commit()
    print("done")


if __name__ == "__main__":
    main()
