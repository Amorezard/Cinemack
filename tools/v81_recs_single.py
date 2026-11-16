#!/usr/bin/env python3
"""v8.1 slice 5.2 (15 Nov 2026): ONE weight set for the new film ranking (web/recs.py), checked under both protocols.

Why: the 14 Nov benchmark (tools/v81_recs_benchmark.py) let v2 choose its weights separately for each test protocol
and it chose differently (content 2 / neighbours 4 with uniform negatives; content 2 / neighbours 0.5 with popular
negatives). A site can only run one set.

Same data, split, users, candidates and metrics as the 14 Nov benchmark (its functions are imported, not copied).
Written before this run:
  choice   on the SELECT half only: for every weight set of the benchmark's grid, NDCG@10 under each protocol divided
           by the best NDCG any grid set reaches under that protocol; the set with the highest mean of the two
           ratios wins (ties: the lexicographically larger set, as before)
  rule     on the REPORT half, the single set (+ genre MMR, lambda 0.85) against current_timesafe must hold under
           BOTH protocols: NDCG@10 difference's bootstrap 95% CI not wholly below 0 AND catalogue coverage not lower.
           PASS -> the weights are written to results/v81_recs_single.json and web/recs.py will use them;
           FAIL -> web/recs.py keeps reporting "fallback" and RECS_V2 cannot be enabled.
  caveat   the report half was already looked at once on 14 Nov (per-protocol weights), so this is a second look
           at the same users, and they are MovieLens users, not Cinemack users. Diversity is reported, not ruled on.
Read-only on everything except its two result files.
    python tools/v81_recs_single.py [--zip PATH] [--users 3000]
"""
import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import facts_db as F  # noqa: E402
import personal  # noqa: E402
import v81_recs_benchmark as B  # noqa: E402

PROTOS = ("uniform", "popular")


def half(u):
    return "select" if (u * 2654435761) % 2**32 < 2**31 else "report"


def order(cand, s, w):
    return sorted(cand, key=lambda f: (-(w[0] * s["c"][f] + w[1] * s["nt"][f] + w[2] * s["pt"][f]), f))


def choose(sel):
    """sel = {proto: [(u, pos, cand, s)]} on the select half -> (weights, table of the mean ratio per set)."""
    nd = {p: {w: (float(np.mean([B.ndcg10(order(cand, s, w), pos) for _u, pos, cand, s in sel[p]])) if sel[p] else 0.0)
              for w in B.GRID} for p in PROTOS}
    best = {p: max(nd[p].values()) or 1.0 for p in PROTOS}
    ratio = {w: float(np.mean([nd[p][w] / best[p] for p in PROTOS])) for w in B.GRID}
    w = max(B.GRID, key=lambda x: (ratio[x], x))
    return w, {"mean_ratio": ratio[w], "ndcg_select": {p: nd[p][w] for p in PROTOS}, "best_select": best}


def evaluate(rep, w, genres, current):
    """rep = [(u, pos, cand, s)] on the report half; current = {u: top10 of current_timesafe}."""
    v2, cur, shown2, shown1, allc, div2, div1 = [], [], set(), set(), set(), [], []
    for u, pos, cand, s in rep:
        sc = {f: w[0] * s["c"][f] + w[1] * s["nt"][f] + w[2] * s["pt"][f] for f in cand}
        top2, top1 = B.mmr(sc, cand, genres), current[u]
        v2.append(B.ndcg10(top2, pos))
        cur.append(B.ndcg10(top1, pos))
        shown2 |= set(top2)
        shown1 |= set(top1)
        allc |= set(cand)
        div2.append(B.jacc_div(top2, genres))
        div1.append(B.jacc_div(top1, genres))
    d = np.array(v2) - np.array(cur)
    rng = np.random.default_rng(0)
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)] if len(d) else [0.0]
    out = {"n": len(rep), "ndcg_v2": float(np.mean(v2)) if v2 else 0.0, "ndcg_current": float(np.mean(cur)) if cur else 0.0,
           "diff": [float(d.mean()) if len(d) else 0.0, float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
           "coverage_v2": len(shown2) / max(1, len(allc)), "coverage_current": len(shown1) / max(1, len(allc)),
           "diversity_v2": float(np.mean(div2)) if div2 else 0.0, "diversity_current": float(np.mean(div1)) if div1 else 0.0}
    out["ok"] = bool(out["diff"][2] >= 0 and out["coverage_v2"] >= out["coverage_current"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.expanduser("~/movie-buff/rag/data/movielens/ml-32m.zip"))
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--negatives", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_recs_single"))
    a = ap.parse_args(argv)
    t0 = time.time()
    conn = sqlite3.connect(f"file:{F.PATH}?mode=ro", uri=True)
    vc, genres = {}, {}
    for fid, v, g in conn.execute("select tmdb_id, vote_count, genres_json from films"):
        vc[fid] = v or 0
        try:
            genres[fid] = frozenset(json.loads(g or "[]"))
        except ValueError:
            genres[fid] = frozenset()
    R = B.load(a.zip, set(vc))
    train = R[R.timestamp < B.CUT]
    test = R[(R.timestamp >= B.CUT) & (R.timestamp < B.END) & (R.rating >= 4)]
    pop = train.tmdb.value_counts().to_dict()
    NB = B.neighbours_train(train)
    ntr = train.groupby("userId").size()
    tpos = test.groupby("userId").tmdb.apply(set).to_dict()
    elig = sorted(u for u in tpos if ntr.get(u, 0) >= 20)
    users = sorted(random.Random(13).sample(elig, min(a.users, len(elig))))
    pool = np.array(sorted(pop))
    pw = np.array([pop[f] for f in pool], dtype=float)
    pw /= pw.sum()
    tr_by_user = {u: g for u, g in train[train.userId.isin(users)].groupby("userId")}
    ever = R[R.userId.isin(users)].groupby("userId").tmdb.apply(set).to_dict()
    rows = {p: [] for p in PROTOS}
    current = {p: {} for p in PROTOS}
    for n_u, u in enumerate(users):                    # identical to the 14 Nov loop (same seeds, same draws)
        hist = tr_by_user[u].sort_values("timestamp")
        likes = hist[hist.rating >= 4].tmdb.tolist()[-50:]
        dis = hist[hist.rating <= 2].tmdb.tolist()[-20:]
        pos = tpos[u] - set(hist.tmdb)
        if not pos or not likes:
            continue
        prof = personal.profile(likes, dis, conn)
        nbt = B.nb_score(NB, likes)
        urnd = np.random.default_rng(u)
        for proto in PROTOS:
            neg, tries = set(), 0
            while len(neg) < a.negatives and tries < 20:
                draw = urnd.choice(pool, size=a.negatives * 2, p=pw if proto == "popular" else None)
                neg |= {int(f) for f in draw if int(f) not in ever[u]}
                tries += 1
            neg = sorted(neg)[:a.negatives]
            cand = sorted(set(pos) | set(neg))
            raw = {"c": {f: personal.match(prof, f, conn)[0] for f in cand}, "nt": {f: min(nbt.get(f, 0.0), 2.0) for f in cand},
                   "pt": {f: math.log1p(pop.get(f, 0)) for f in cand}}
            s = {k: B.minmax(v) for k, v in raw.items()}
            rows[proto].append((u, pos, cand, s))
            current[proto][u] = order(cand, s, B.LIVE_W)[:10]
        if n_u % 500 == 0:
            print(f"  {n_u:,}/{len(users):,} users, {time.time() - t0:.0f} s", flush=True)
    sel = {p: [r for r in rows[p] if half(r[0]) == "select"] for p in PROTOS}
    w, how = choose(sel)
    res = {"weights": list(w), "choice": how, "protocols": {}, "grid": len(B.GRID), "users": len(users)}
    L = ["One weight set for the film ranking (v8.1 slice 5.2) on MovieLens 32M - MovieLens users, not Cinemack users",
         f"chosen on the select half: content {w[0]}, neighbours {w[1]}, popularity {w[2]} "
         f"(mean share of each protocol's best NDCG: {how['mean_ratio']:.3f})", ""]
    for p in PROTOS:
        rep = [r for r in rows[p] if half(r[0]) == "report"]
        e = evaluate(rep, w, genres, current[p])
        res["protocols"][p] = e
        L.append(f"[{p} negatives] report half {e['n']:,} users: v2 NDCG@10 {e['ndcg_v2']:.3f} vs current {e['ndcg_current']:.3f} "
                 f"({e['diff'][0]:+.4f} [{e['diff'][1]:+.4f}, {e['diff'][2]:+.4f}]) | coverage {e['coverage_v2']:.3f} vs {e['coverage_current']:.3f} | "
                 f"variety {e['diversity_v2']:.3f} vs {e['diversity_current']:.3f} -> {'ok' if e['ok'] else 'NOT ok'}")
    res["rule"] = "PASS" if all(res["protocols"][p]["ok"] for p in PROTOS) else "FAIL"
    L += ["", f"RULE (both protocols: NDCG CI not wholly below 0 AND coverage not lower): {res['rule']}",
          "Second look at the same report half as 14 Nov. Variety (genre spread inside a list) is reported, not part of the rule.",
          f"{time.time() - t0:.0f} s"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + ".txt", "w") as fh:
        fh.write("\n".join(L) + "\n")
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
