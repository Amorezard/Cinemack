#!/usr/bin/env python3
"""
D12 (v7, 27 Oct): Jackson's metrics - mean reciprocal rank and each retriever's own hit rates, from a stage_scores.py
run already on disk (no model calls). Reported only (no rule).

    python metrics.py results/stage_scores_<date>_tune_<tag>.json [more.json ...]

For every question with an expected film: the rank of that film in each search list the fused search used
(question vector, guessed-plot vector, generated-description list, TMDB keywords, Wikipedia keywords, Wikipedia
passage vectors; top 50 kept in the trace), in the fused pool, after the re-ranker and in the final answer. Prints,
per list: hit@1, hit@10, hit@50 and MRR (1/rank, 0 when missing), and how often a list found the film when every
other list missed it (its unique contribution).
"""
import json
import os
import sqlite3
import sys
import unicodedata
import re

LISTS = [("question vector", "pool_q"), ("guessed plot", "pool_hyde"), ("generated descriptions", "pool_wiki_q"),
         ("TMDB keywords", "pool_keyword"), ("Wikipedia keywords", "pool_wiki_keyword"),
         ("Wikipedia passages", "pool_wiki")]
STAGES = [("fused pool", "pool"), ("re-ranker in", "rerank_in"), ("re-ranker out", "rerank_out"), ("final", "final")]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.casefold()).strip()


def titles(ids, movies="movies.db"):
    if not ids or not os.path.exists(movies):
        return {}
    c = sqlite3.connect(f"file:{movies}?mode=ro", uri=True)
    out = {}
    ids = list(ids)
    for s in range(0, len(ids), 500):
        part = ids[s:s + 500]
        for i, t, y in c.execute(f"select id, title, release_year from movies where id in ({','.join('?' * len(part))})",
                                 part):
            out[i] = (norm(t), str(y or "")[:4])
    return out


def rank(lst, want, years, names):
    for i, x in enumerate(lst or [], 1):
        fid = x[0] if isinstance(x, (list, tuple)) else x
        t, y = names.get(fid, (None, None)) if not isinstance(x, (list, tuple)) else (norm(x[1]), str(x[2] or "")[:4])
        if t in want and (not years or not y or y in years):
            return i
    return None


def main(paths):
    rows = []
    for p in paths:
        rows += [r for r in json.load(open(p)).get("rows", []) if r.get("expect")]
    ids = set()
    for r in rows:
        tr = r["result"].get("trace") or {}
        for _n, k in LISTS:
            ids.update(x if not isinstance(x, (list, tuple)) else x[0] for x in tr.get(k) or [])
    names = titles(ids)
    res = {n: [] for n, _k in LISTS + STAGES}
    unique = {n: 0 for n, _k in LISTS}
    for r in rows:
        tr = r["result"].get("trace") or {}
        exp = r["expect"] if isinstance(r["expect"], list) else [r["expect"]]
        want = {norm(e) for e in exp}
        years = {str(r.get("expect_year"))[:4]} if r.get("expect_year") else set()
        found = {}
        for n, k in LISTS + STAGES:
            if k in tr:
                rk = rank(tr.get(k), want, years, names)
                res[n].append(rk)
                found[n] = rk
        hits50 = [n for n, _k in LISTS if found.get(n)]
        if len(hits50) == 1:
            unique[hits50[0]] += 1
    print(f"{len(rows)} questions with an expected film ({', '.join(os.path.basename(p) for p in paths)})")
    print(f"{'list / stage':26s} {'ran':>4s} {'hit@1':>6s} {'hit@10':>7s} {'hit@50':>7s} {'MRR':>6s} {'only one':>9s}")
    for n, _k in LISTS + STAGES:
        rs = res[n]
        if not rs:
            continue
        k = len(rs)
        h = lambda c: sum(1 for x in rs if x and x <= c) / k  # noqa: E731
        mrr = sum(1 / x for x in rs if x) / k
        print(f"{n:26s} {k:4d} {h(1):6.2f} {h(10):7.2f} {h(50):7.2f} {mrr:6.3f} {unique.get(n, '') if n in unique else '':>9}")
    print("'only one' = questions where this list was the only one with the film in its top 50.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
