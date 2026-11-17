#!/usr/bin/env python3
"""v8.1 round 48 (16 Nov 2026): why the new film ranking failed twice, and one more candidate tested on new data.

WHAT WENT WRONG (read from the 14-16 Nov results, no new run needed)
  NDCG@10 of each signal alone, report half of 14 Nov:        uniform negatives   popular negatives
    content match (director, cast, genre, keywords)                 0.468               0.420
    MovieLens neighbours ("liked X -> also liked Y")                0.631               0.278
    popularity                                                      0.387               0.092
    current ranking (content 0.25, neighbours 2, popularity 0.5)    0.627               0.276
  - The two tests reward opposite things. Against random films, neighbours and popularity are strong (a random film is
    obscure). Against popular films, popularity is worse than a coin flip and neighbours lose most of their value
    (popular films are everybody's neighbours); only content keeps working (0.47 / 0.42).
  - On 15 Nov ONE weight set was picked by "highest average share of each test's best score". That picked content 2,
    neighbours 1, popularity 0: nearly the best on the popular test (0.43 of a possible 0.44) and well short on the
    uniform test (0.616 of 0.657) - below the current ranking's 0.627. It then failed the rule there, twice
    (-0.015 on 15 Nov, -0.011 on fresh users on 16 Nov).
  - The variety step was not the cause: without it the same weights did no better (-0.013).
  So the fault was the WAY the single set was chosen: it optimised a score the rule does not use. The rule asks to be
  no worse than the current ranking on BOTH tests; the popular test has 0.15 of room, the uniform test almost none.

THE CANDIDATE (written before this run)
  choice   on the 2020 data already seen (all 5,569 eligible users, both tests): for every weight set of the same grid,
           its NDCG@10 minus the current ranking's under each test; the set whose SMALLER margin is largest wins
           (ties: the lexicographically larger set). This is the rule's own question, asked directly.
  test     data never used so far: the year 2021. Train = ratings before 2021 (neighbours rebuilt from them), test =
           films liked in 2021, users with >= 20 earlier ratings who are NOT among the 5,569 users of the 2020 runs.
           Same candidates (liked films + 200 negatives), same two tests, same metrics, genre MMR 0.85.
  rule     unchanged: under BOTH tests the NDCG@10 difference to the current ranking has a bootstrap 95% CI that is
           not wholly below 0 AND catalogue coverage is not lower. Fewer than 500 evaluated users = UNRUN.
           PASS -> results/v81_recs_v3.json carries the weights and "rule": "PASS"; web/recs.py may then use them, and
           RECS_V2 still stays off until the user turns it on. FAIL -> the current ranking stays, and the write-up
           says that no single linear weight set beats it on both tests.
  honesty  the choice uses data seen three times; only the 2021 test is new. MovieLens users are not Cinemack users.
           Variety (genre spread inside a list) is reported, not ruled on.
Read-only except its two result files.
    python tools/v81_recs_v3.py [--zip PATH] [--test-users 3000]
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

Y2021, Y2022 = 1609459200, 1640995200        # 2021-01-01 and 2022-01-01, 00:00 UTC
MIN_USERS = 500
TEST_SEED = 31


def eligible(R, cut, end):
    """(train, {user: liked films in [cut, end)}, users with >= 20 train ratings and one such film)."""
    train = R[R.timestamp < cut]
    test = R[(R.timestamp >= cut) & (R.timestamp < end) & (R.rating >= 4)]
    ntr = train.groupby("userId").size()
    tpos = test.groupby("userId").tmdb.apply(set).to_dict()
    return train, tpos, sorted(u for u in tpos if ntr.get(u, 0) >= 20)


def collect(R, train, tpos, users, conn, negatives=200, t0=None, label=""):
    """{protocol: [(user, positives, candidates, scaled signals)]} and the current ranking's top 10 per user.
    Identical to the 14-16 Nov loops (same per-user random draws)."""
    pop = train.tmdb.value_counts().to_dict()
    NB = B.neighbours_train(train)
    pool = np.array(sorted(pop))
    pw = np.array([pop[f] for f in pool], dtype=float)
    pw /= pw.sum()
    uset = set(users)
    tr_by_user = {u: g for u, g in train[train.userId.isin(uset)].groupby("userId")}
    ever = R[R.userId.isin(uset)].groupby("userId").tmdb.apply(set).to_dict()
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
            while len(neg) < negatives and tries < 20:
                draw = urnd.choice(pool, size=negatives * 2, p=pw if proto == "popular" else None)
                neg |= {int(f) for f in draw if int(f) not in ever[u]}
                tries += 1
            neg = sorted(neg)[:negatives]
            cand = sorted(set(pos) | set(neg))
            raw = {"c": {f: personal.match(prof, f, conn)[0] for f in cand}, "nt": {f: min(nbt.get(f, 0.0), 2.0) for f in cand},
                   "pt": {f: math.log1p(pop.get(f, 0)) for f in cand}}
            s = {k: B.minmax(v) for k, v in raw.items()}
            rows[proto].append((u, pos, cand, s))
            current[proto][u] = S.order(cand, s, B.LIVE_W)[:10]
        if t0 is not None and n_u % 1000 == 0:
            print(f"  {label} {n_u:,}/{len(users):,} users, {time.time() - t0:.0f} s", flush=True)
    return rows, current


def margins(rows, current, grid=None):
    """{weights: {protocol: mean NDCG@10 minus the current ranking's}} on plain weighted orders (no variety step)."""
    out = {}
    base = {p: (float(np.mean([B.ndcg10(current[p][u], pos) for u, pos, _c, _s in rows[p]])) if rows[p] else 0.0) for p in S.PROTOS}
    for w in (grid or B.GRID):
        out[w] = {p: (float(np.mean([B.ndcg10(S.order(cand, s, w), pos) for _u, pos, cand, s in rows[p]])) - base[p]) if rows[p] else 0.0
                  for p in S.PROTOS}
    return out, base


def choose(m):
    """The weight set whose smaller margin over the current ranking is largest (ties: the larger tuple)."""
    return max(m, key=lambda w: (min(m[w].values()), w))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.expanduser("~/movie-buff/rag/data/movielens/ml-32m.zip"))
    ap.add_argument("--test-users", type=int, default=3000)
    ap.add_argument("--select-users", type=int, default=0, help="0 = all eligible 2020 users (tests use fewer)")
    ap.add_argument("--negatives", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_recs_v3"))
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
    # 1. choice on the 2020 data already seen
    train20, tpos20, elig20 = eligible(R, B.CUT, B.END)
    sel = elig20 if not a.select_users else sorted(random.Random(13).sample(elig20, min(a.select_users, len(elig20))))
    rows20, cur20 = collect(R, train20, tpos20, sel, conn, a.negatives, t0, "choice (2020)")
    m, base20 = margins(rows20, cur20)
    w = choose(m)
    L = ["A third candidate for the film ranking (v8.1 slice 5.2) on MovieLens 32M - MovieLens users, not Cinemack users", "",
         f"choice on the 2020 data already seen ({len(rows20['uniform']):,} users): the weight set with the largest SMALLER margin over the current ranking",
         f"  chosen: content {w[0]}, neighbours {w[1]}, popularity {w[2]} | margins there: uniform {m[w]['uniform']:+.4f}, popular {m[w]['popular']:+.4f}",
         f"  for comparison there: the 15 Nov set (2, 1, 0) uniform {m[(2, 1, 0)]['uniform']:+.4f}, popular {m[(2, 1, 0)]['popular']:+.4f}; "
         f"sets with both margins above 0: {sum(1 for x in m.values() if min(x.values()) > 0)} of {len(m)}", ""]
    res = {"weights": list(w), "choice": {"users": len(rows20["uniform"]), "margins": m[w], "current_ndcg": base20,
                                           "margins_2_1_0": m[(2, 1, 0)], "sets_positive_on_both": sum(1 for x in m.values() if min(x.values()) > 0)},
           "protocols": {}}
    # 2. one look at 2021, users never used
    train21, tpos21, elig21 = eligible(R, Y2021, Y2022)
    seen = set(elig20)
    new = [u for u in elig21 if u not in seen]
    users = sorted(random.Random(TEST_SEED).sample(new, min(a.test_users, len(new))))
    rows21, cur21 = collect(R, train21, tpos21, users, conn, a.negatives, t0, "test (2021)")
    L.append(f"test on 2021 (train = ratings before 2021): {len(elig21):,} eligible users, {len(new):,} never used before, {len(users):,} drawn (seed {TEST_SEED})")
    for p in S.PROTOS:
        e = S.evaluate(rows21[p], w, genres, cur21[p])
        res["protocols"][p] = e
        L.append(f"[{p} negatives] {e['n']:,} users: candidate NDCG@10 {e['ndcg_v2']:.3f} vs current {e['ndcg_current']:.3f} "
                 f"({e['diff'][0]:+.4f} [{e['diff'][1]:+.4f}, {e['diff'][2]:+.4f}]) | coverage {e['coverage_v2']:.3f} vs {e['coverage_current']:.3f} | "
                 f"variety {e['diversity_v2']:.3f} vs {e['diversity_current']:.3f} -> {'ok' if e['ok'] else 'NOT ok'}")
    enough = all(res["protocols"][p]["n"] >= MIN_USERS for p in S.PROTOS)
    res["rule"] = ("PASS" if all(res["protocols"][p]["ok"] for p in S.PROTOS) else "FAIL") if enough else "UNRUN"
    if not enough:
        L.append(f"UNRUN: fewer than {MIN_USERS} new users could be evaluated - no verdict")
    L += ["", f"RULE (both tests: NDCG CI not wholly below 0 AND coverage not lower): {res['rule']}",
          "The choice used data seen three times; only the 2021 test is new. A PASS lets web/recs.py use these weights; RECS_V2 stays off until switched on.",
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
