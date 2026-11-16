#!/usr/bin/env python3
"""v8.1 round 46 (16 Nov 2026): the film ranking's single weight set on MovieLens users it has never seen.

15 Nov: with ONE weight set (content 2, neighbours 1, popularity 0; chosen on the select half) the new ranking
passed with popular negatives and failed with uniform negatives (NDCG@10 0.611 vs 0.627) on the report half. That
half had by then been looked at twice. This run repeats the test once on FRESH users, with nothing left to choose.

Written before this run:
  users     3,000 eligible users (>= 20 ratings before 2020 and a liked film in 2020) drawn with seed 29 from those
            NOT among the 3,000 of the 14 / 15 Nov runs (seed 13). Same data, split, candidates and metrics.
  arm       weights (2, 1, 0) exactly as chosen on 15 Nov + genre MMR 0.85, against current_timesafe. No selection
            step, so every fresh user counts.
  rule      as on 15 Nov: under BOTH protocols the NDCG@10 difference's bootstrap 95% CI is not wholly below 0 AND
            catalogue coverage is not lower. PASS -> results/v81_recs_single.json may be replaced by this result
            in a later, separate step (this tool never writes that file). FAIL -> the ranking stays off, and this
            weight set is not tested again.
  reported, not ruled on: the same weights WITHOUT the variety step (does MMR cost the accuracy?), and variety.
MovieLens users, not Cinemack users. Read-only except its two result files.
    python tools/v81_recs_fresh.py [--zip PATH] [--users 3000]
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
import v81_recs_single as S  # noqa: E402

W = (2.0, 1.0, 0.0)          # fixed on 15 Nov; nothing is chosen here
SEEN_SEED, FRESH_SEED = 13, 29
MIN_USERS = 500              # fewer evaluated users than this under either protocol = no verdict (UNRUN)


def fresh_users(elig, n, seen_n=3000):
    """n users drawn with FRESH_SEED from the eligible users outside the earlier runs' sample."""
    elig = sorted(elig)
    seen = set(random.Random(SEEN_SEED).sample(elig, min(seen_n, len(elig))))
    rest = [u for u in elig if u not in seen]
    return sorted(random.Random(FRESH_SEED).sample(rest, min(n, len(rest)))), len(seen), len(rest)


def no_mmr(rows, w, current):
    """NDCG@10 of the plain weighted order (no variety step) minus current, with a bootstrap CI."""
    d = np.array([B.ndcg10(S.order(cand, s, w), pos) - B.ndcg10(current[u], pos) for u, pos, cand, s in rows])
    if not len(d):
        return [0.0, 0.0, 0.0]
    rng = np.random.default_rng(0)
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
    return [float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.expanduser("~/movie-buff/rag/data/movielens/ml-32m.zip"))
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--negatives", type=int, default=200)
    ap.add_argument("--seen", type=int, default=3000, help="size of the earlier runs' sample (3,000; tests use less)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_recs_fresh"))
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
    elig = [u for u in tpos if ntr.get(u, 0) >= 20]
    users, n_seen, n_rest = fresh_users(elig, a.users, a.seen)
    pool = np.array(sorted(pop))
    pw = np.array([pop[f] for f in pool], dtype=float)
    pw /= pw.sum()
    tr_by_user = {u: g for u, g in train[train.userId.isin(users)].groupby("userId")}
    ever = R[R.userId.isin(users)].groupby("userId").tmdb.apply(set).to_dict()
    rows = {p: [] for p in S.PROTOS}
    current = {p: {} for p in S.PROTOS}
    for n_u, u in enumerate(users):
        hist = tr_by_user[u].sort_values("timestamp")
        likes = hist[hist.rating >= 4].tmdb.tolist()[-50:]
        dis = hist[hist.rating <= 2].tmdb.tolist()[-20:]
        pos = tpos[u] - set(hist.tmdb)
        if not pos or not likes:
            continue
        prof = personal.profile(likes, dis, conn)
        nbt = B.nb_score(NB, likes)
        urnd = np.random.default_rng(u)
        for proto in S.PROTOS:
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
            current[proto][u] = S.order(cand, s, B.LIVE_W)[:10]
        if n_u % 500 == 0:
            print(f"  {n_u:,}/{len(users):,} users, {time.time() - t0:.0f} s", flush=True)
    res = {"weights": list(W), "users_drawn": len(users), "earlier_sample": n_seen, "eligible_outside_it": n_rest, "protocols": {}}
    L = ["The film ranking's single weight set on fresh MovieLens users (v8.1 slice 5.2) - MovieLens users, not Cinemack users",
         f"weights fixed on 15 Nov: content {W[0]}, neighbours {W[1]}, popularity {W[2]}; {len(users):,} users drawn (seed {FRESH_SEED}) "
         f"from {n_rest:,} eligible users outside the earlier sample of {n_seen:,}", ""]
    for p in S.PROTOS:
        e = S.evaluate(rows[p], W, genres, current[p])
        e["no_mmr_diff"] = no_mmr(rows[p], W, current[p])
        res["protocols"][p] = e
        L.append(f"[{p} negatives] {e['n']:,} users: v2 NDCG@10 {e['ndcg_v2']:.3f} vs current {e['ndcg_current']:.3f} "
                 f"({e['diff'][0]:+.4f} [{e['diff'][1]:+.4f}, {e['diff'][2]:+.4f}]) | coverage {e['coverage_v2']:.3f} vs {e['coverage_current']:.3f} | "
                 f"variety {e['diversity_v2']:.3f} vs {e['diversity_current']:.3f} -> {'ok' if e['ok'] else 'NOT ok'}")
        L.append(f"    reported only - same weights without the variety step: {e['no_mmr_diff'][0]:+.4f} "
                 f"[{e['no_mmr_diff'][1]:+.4f}, {e['no_mmr_diff'][2]:+.4f}] vs current")
    enough = all(res["protocols"][p]["n"] >= MIN_USERS for p in S.PROTOS)
    res["rule"] = ("PASS" if all(res["protocols"][p]["ok"] for p in S.PROTOS) else "FAIL") if enough else "UNRUN"
    if not enough:
        L.append(f"UNRUN: fewer than {MIN_USERS} fresh users could be evaluated - no verdict")
    L += ["", f"RULE (both protocols: NDCG CI not wholly below 0 AND coverage not lower): {res['rule']}",
          "One look at fresh users with nothing chosen on them. A FAIL ends this weight set; a PASS still needs a separate step to enable anything.",
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
