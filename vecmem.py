"""
In-memory exact vector search (VEC_MEM=1 in retrieve.py, the default since 30 Sep 2026:
E9 gave identical results on tune and check, search 1.42 -> 0.19 s per question).
`python mb.py build-cache` runs build + check below.

sqlite-vec answers every search by comparing the question with every stored vector,
one row at a time, inside SQLite: ~1 s of the ~1.5-2.3 s search per description
(29 Sep profile: knn_multi 1.07 s/question, distance_to 0.33 s, question list 0.2 s).
The same exact search is one matrix-vector product in numpy. This file copies the
vector tables into .npy files once (build), and retrieve.py searches those instead.

Same results by construction: every vector is compared (no approximate index), with
the same distances (movie tables: L2; Wikipedia passages: cosine). Only float
rounding differs, which can swap two films whose distances tie to ~6 decimals.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python vecmem.py build      # ~1-3 min, writes vec_cache/ next to movies.db (~0.9 GB)
    python vecmem.py check      # compares a few searches with sqlite-vec, prints timings

The files are memory-mapped, so the website and an evaluation run share one copy in
RAM. If movies.db or wiki.db change after the build, the cache is ignored (with a
warning) until it is rebuilt, and retrieve.py falls back to sqlite-vec.
"""
import json
import math
import os
import shutil
import sqlite3
import sys
import time

import numpy as np

MOVIE_TABLES = ("movie_vectors", "movie_plot_vectors")      # L2 distance, key movie_id


def _stamp(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def _paths(db_path):
    root = os.path.dirname(os.path.abspath(db_path))
    return root, os.path.join(root, "wiki.db"), os.path.join(root, "vec_cache")


def _connect(path):
    import sqlite_vec
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _blobs_to_matrix(blobs, dim):
    out = np.empty((len(blobs), dim), dtype=np.float32)
    for i, b in enumerate(blobs):
        out[i] = np.frombuffer(b, dtype=np.float32)
    return out


def build(db_path="movies.db"):
    root, wiki_path, final = _paths(db_path)
    # Written to vec_cache.new and swapped in at the end: a running website or evaluation
    # keeps reading the old files (memory-mapped) instead of crashing mid-write.
    out = final + ".new"
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    meta = {"movies.db": _stamp(os.path.join(root, os.path.basename(db_path))), "tables": {}}
    t0 = time.time()
    conn = _connect(db_path)
    for table in MOVIE_TABLES:
        try:
            rows = conn.execute(f"SELECT movie_id, embedding FROM {table}").fetchall()
        except sqlite3.OperationalError as e:
            print(f"  {table}: skipped ({e})")
            continue
        dim = len(rows[0][1]) // 4
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        mat = _blobs_to_matrix([r[1] for r in rows], dim)
        np.save(os.path.join(out, f"{table}_ids.npy"), ids)
        np.save(os.path.join(out, f"{table}_vecs.npy"), mat)
        np.save(os.path.join(out, f"{table}_sq.npy"), np.einsum("ij,ij->i", mat, mat))
        meta["tables"][table] = {"rows": len(ids), "dim": dim}
        print(f"  {table}: {len(ids):,} vectors ({time.time() - t0:.0f} s)")
    conn.close()
    if os.path.exists(wiki_path):
        meta["wiki.db"] = _stamp(wiki_path)
        w = _connect(wiki_path)
        buckets = {}                 # kind -> ([chunk_id], [film_id], [blob]); one pass over the table
        for cid, fid, kind, blob in w.execute("SELECT chunk_id, film_id, kind, embedding FROM vec_chunks"):
            b = buckets.setdefault(kind, ([], [], []))
            b[0].append(cid); b[1].append(fid if fid is not None else -1); b[2].append(blob)
        ids_all, film_all, vec_parts, slices, start = [], [], [], {}, 0
        for kind in sorted(buckets):   # one contiguous block per kind, so a kind is a view, not a copy
            cids, fids, blobs = buckets.pop(kind)
            order = np.argsort(np.array(cids, dtype=np.int64), kind="stable")
            dim = len(blobs[0]) // 4
            ids_all.append(np.array(cids, dtype=np.int64)[order])
            film_all.append(np.array(fids, dtype=np.int64)[order])
            vec_parts.append(_blobs_to_matrix([blobs[i] for i in order], dim))
            slices[kind] = [start, start + len(cids)]
            start += len(cids)
            print(f"  vec_chunks[{kind}]: {len(cids):,} vectors ({time.time() - t0:.0f} s)")
            del blobs
        mat = np.concatenate(vec_parts)
        np.save(os.path.join(out, "chunks_ids.npy"), np.concatenate(ids_all))
        np.save(os.path.join(out, "chunks_film.npy"), np.concatenate(film_all))
        np.save(os.path.join(out, "chunks_vecs.npy"), mat)
        np.save(os.path.join(out, "chunks_norm.npy"), np.sqrt(np.einsum("ij,ij->i", mat, mat)))
        meta["chunks"] = {"rows": int(start), "dim": int(mat.shape[1]), "kinds": slices}
        w.close()
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)
    old = final + ".old"
    if os.path.exists(old):
        shutil.rmtree(old)
    if os.path.exists(final):
        os.rename(final, old)
    os.rename(out, final)
    if os.path.exists(old):
        shutil.rmtree(old)          # open memory maps keep the old data until they close
    print(f"Built {final} in {time.time() - t0:.0f} s")


class Store:
    """Exact nearest-neighbour search over the .npy copies. Methods take q as a list of floats."""

    def __init__(self, db_path):
        root, wiki_path, d = _paths(db_path)
        meta = json.load(open(os.path.join(d, "meta.json")))
        if meta["movies.db"] != _stamp(os.path.join(root, os.path.basename(db_path))):
            raise RuntimeError("movies.db changed since vec_cache was built")
        if "wiki.db" in meta and os.path.exists(wiki_path) and meta["wiki.db"] != _stamp(wiki_path):
            raise RuntimeError("wiki.db changed since vec_cache was built")
        ld = lambda n: np.load(os.path.join(d, n), mmap_mode="r")
        self.tables = {}
        for table in meta["tables"]:
            ids = np.asarray(ld(f"{table}_ids.npy"))
            self.tables[table] = (ids, ld(f"{table}_vecs.npy"), ld(f"{table}_sq.npy"),
                                  {int(m): i for i, m in enumerate(ids)})
        self.chunks = None
        if "chunks" in meta:
            ids = np.asarray(ld("chunks_ids.npy"))
            self.chunks = (ids, np.asarray(ld("chunks_film.npy")), ld("chunks_vecs.npy"),
                           ld("chunks_norm.npy"), meta["chunks"]["kinds"],
                           {int(c): i for i, c in enumerate(ids)})

    @staticmethod
    def _q(q):
        return np.asarray(q, dtype=np.float32)

    def has(self, table):
        return table in self.tables

    def knn_l2(self, table, q, k):
        """[(movie_id, l2 distance)] closest first, like `embedding MATCH ? AND k = ?`."""
        ids, V, sq, _ = self.tables[table]
        q = self._q(q)
        n = len(ids)
        k = min(k, n)
        if k <= 0:
            return []
        score = sq - 2.0 * (V @ q)                      # |v-q|^2 - |q|^2: same order
        m = min(n, k + 32)                               # margin, then exact distances
        idx = np.argpartition(score, m - 1)[:m] if m < n else np.arange(n)
        d = np.sqrt(np.sum((np.asarray(V[idx]) - q) ** 2, axis=1))
        order = np.lexsort((ids[idx], d))[:k]
        return [(int(ids[idx[i]]), float(d[i])) for i in order]

    def dist_l2(self, table, movie_id, q):
        ids, V, _, pos = self.tables[table]
        i = pos.get(int(movie_id))
        if i is None:
            return None
        return float(np.sqrt(np.sum((np.asarray(V[i]) - self._q(q)) ** 2)))

    def knn_cos(self, kind, q, k):
        """[(chunk_id, film_id, cosine distance)] closest first, within one kind."""
        ids, film, V, norm, kinds, _ = self.chunks
        if kind not in kinds:
            return []
        a, b = kinds[kind]
        q = self._q(q)
        qn = float(np.sqrt(q @ q)) or 1.0
        n = b - a
        k = min(k, n)
        if k <= 0:
            return []
        dist = 1.0 - (V[a:b] @ q) / (np.asarray(norm[a:b]) * qn)
        idx = np.argpartition(dist, k - 1)[:k] if k < n else np.arange(n)
        order = np.lexsort((ids[a:b][idx], dist[idx]))
        return [(int(ids[a + idx[i]]), int(film[a + idx[i]]), float(dist[idx[i]])) for i in order]

    def dist_cos(self, chunk_id, q):
        ids, film, V, norm, kinds, pos = self.chunks
        i = pos.get(int(chunk_id))
        if i is None:
            return None
        q = self._q(q)
        return float(1.0 - (np.asarray(V[i]) @ q) / (float(norm[i]) * (float(np.sqrt(q @ q)) or 1.0)))


def check(db_path="movies.db", n=5):
    """Compare a few searches with sqlite-vec: same films, same order, same distances."""
    import random
    import sqlite_vec
    s = Store(db_path)
    conn = _connect(db_path)
    root, wiki_path, _ = _paths(db_path)
    conn.execute("ATTACH DATABASE ? AS wiki", (wiki_path,))
    rows = conn.execute("SELECT movie_id, embedding FROM movie_vectors ORDER BY random() LIMIT ?",
                        (n,)).fetchall()
    worst, same, total, ts, tm = 0.0, 0, 0, 0.0, 0.0
    for _, blob in rows:
        base = np.frombuffer(blob, dtype=np.float32)
        q = (base + np.random.normal(0, 0.02, base.shape).astype(np.float32)).tolist()   # a nearby query
        arg = sqlite_vec.serialize_float32(q)
        for table in s.tables:
            t = time.perf_counter()
            a = conn.execute(f"SELECT movie_id, distance FROM {table} WHERE embedding MATCH ? AND k = 100",
                             (arg,)).fetchall()
            ts += time.perf_counter() - t
            t = time.perf_counter()
            b = s.knn_l2(table, q, 100)
            tm += time.perf_counter() - t
            total += 1
            same += [m for m, _ in a] == [m for m, _ in b]
            worst = max(worst, max(abs(x[1] - y[1]) for x, y in zip(a, b)))
        if s.chunks:
            for kind in s.chunks[4]:
                t = time.perf_counter()
                a = conn.execute("SELECT chunk_id, distance FROM wiki.vec_chunks WHERE embedding MATCH ? "
                                 "AND k = 400 AND kind = ?", (arg, kind)).fetchall()
                ts += time.perf_counter() - t
                t = time.perf_counter()
                b = s.knn_cos(kind, q, 400)
                tm += time.perf_counter() - t
                total += 1
                same += [c for c, _ in a] == [c for c, _, _ in b]
                worst = max(worst, max(abs(x[1] - y[2]) for x, y in zip(a, b)))
    print(f"{same}/{total} searches returned exactly the same list; largest distance difference {worst:.2e}")
    print(f"time: sqlite-vec {ts:.2f} s, numpy {tm:.2f} s  ({ts / max(tm, 1e-9):.0f}x)")
    print("(a list that differs only where two distances tie to ~6 decimals is expected and harmless)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    db = sys.argv[2] if len(sys.argv) > 2 else "movies.db"
    if cmd == "build":
        build(db)
    elif cmd == "check":
        check(db)
    else:
        sys.exit(__doc__)
