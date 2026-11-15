#!/usr/bin/env python3
"""v8.1 slice 5.2a (13 Nov 2026): time-ordered offline benchmark of film recommendations on MovieLens 32M.

Cinemack has 3 accounts and no watch history (audit F3), so the method is measured on MovieLens users, NOT on
Cinemack users; every number below says that. MovieLens data: F. M. Harper and J. A. Konstan, 2015, ACM TiiS 5(4),
GroupLens licence (citation required).

Split (global time cut, no future information in any model input):
  train     every rating before 2020-01-01        test   ratings 4+ given from 2020-01-01 to 2020-12-31
  users     >= 20 train ratings and >= 1 test positive; 3,000 sampled (seed 13); split in halves by user id:
            "select" (weights chosen there) and "report" (every number in the verdict)
  ranking   per user: the test positives + 200 films the user never rated (two protocols:
            uniform = any catalogue film with a train rating; popular = drawn in proportion to train popularity,
            which is harder for a popularity ranking). Sampled ranking, not the full catalogue: numbers are
            comparable between methods, not with papers ranking the whole catalogue.
Methods
  popularity        number of train ratings
  content           personal.profile / personal.match on the user's train likes (4+) and dislikes (<= 2)
  neighbours_train  item-item cosine on mean-centred TRAIN ratings, shrunk by co-raters (n / (n + 10)), top 50
  current_live      the site's rerank mix (PERSONAL_W 0.25 / 2 / 0.5 on min-max scaled content / neighbours /
                    popularity) with what the site uses: movies.db ml_neighbours (built from ALL ratings - leaks the
                    future) and TMDB vote counts of today (also the future). Optimistic by construction.
  current_timesafe  the same mix with neighbours_train and train popularity
  v2                the same three signals, weights chosen on the "select" half (grid), then a genre-diversity
                    re-rank (MMR, lambda 0.85, fixed before the run)
Metrics: NDCG@10 and Recall@10 (binary: test positives), catalogue coverage (distinct films in all top-10 lists /
films in all candidate lists), intra-list genre diversity (1 - mean pairwise Jaccard of genre sets), bootstrap 95% CI
over users for the NDCG difference v2 - current.
Rule (IMPLEMENTATION_PLAN_LOCKED.md, 12 Nov, before any run): RECS_V2 is a candidate only if, on the report half,
NDCG@10 is not lower than the current rerank (the CI of the difference is not wholly below 0) and coverage is not
lower. Compared against current_timesafe (the fair comparison) and current_live (shown, optimistic).

Read-only: the MovieLens zip and movies.db in the LIVE folder are opened read-only; facts.db is the worktree copy.
    python tools/v81_recs_benchmark.py [--zip PATH] [--users 3000]
"""
import argparse
import io
import json
import math
import os
import random
import sqlite3
import sys
import time
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402
import personal  # noqa: E402

CUT = 1577836800          # 2020-01-01 00:00 UTC
END = 1609459200          # 2021-01-01 00:00 UTC
SHRINK, TOPN, MIN_ITEM = 10.0, 50, 30
LIVE_W = (0.25, 2.0, 0.5)
MMR_LAMBDA = 0.85
GRID = [(c, n, p) for c in (0, 0.25, 0.5, 1, 2) for n in (0, 0.5, 1, 2, 4) for p in (0, 0.1, 0.25, 0.5, 1) if (c, n, p) != (0, 0, 0)]


def load(zip_path, known):
    import pandas as pd
    z = zipfile.ZipFile(zip_path)
    member = lambda name: next(n for n in z.namelist() if n.endswith("/" + name))
    links = pd.read_csv(z.open(member("links.csv")), dtype={"tmdbId": "float64"})
    links = links.dropna(subset=["tmdbId"])
    to_tmdb = dict(zip(links.movieId.astype(int), links.tmdbId.astype(int)))
    r = pd.read_csv(z.open(member("ratings.csv")), dtype={"userId": "int32", "movieId": "int32", "rating": "float32", "timestamp": "int64"})
    r["tmdb"] = r.movieId.map(to_tmdb)
    r = r.dropna(subset=["tmdb"])
    r["tmdb"] = r.tmdb.astype("int64")
    r = r[r.tmdb.isin(known)]
    return r


def neighbours_train(train, min_item=MIN_ITEM):
    """{item: [(neighbour, score)] top 50} from train ratings only."""
    from scipy import sparse
    cnt = train.tmdb.value_counts()
    items = np.array(sorted(cnt[cnt >= min_item].index))
    idx = {t: i for i, t in enumerate(items)}
    t = train[train.tmdb.isin(idx)]
    users = {u: i for i, u in enumerate(t.userId.unique())}
    ui = t.userId.map(users).to_numpy()
    ii = t.tmdb.map(idx).to_numpy()
    centred = (t.rating - t.groupby("userId").rating.transform("mean")).to_numpy(dtype=np.float32)
    X = sparse.csr_matrix((centred, (ui, ii)), shape=(len(users), len(items)))
    B = sparse.csr_matrix((np.ones(len(ui), dtype=np.float32), (ui, ii)), shape=(len(users), len(items)))
    S = (X.T @ X).toarray()
    C = (B.T @ B).toarray()
    norm = np.sqrt(np.maximum(np.diag(S), 1e-9))
    S = S / norm[:, None] / norm[None, :]
    S *= C / (C + SHRINK)
    np.fill_diagonal(S, 0)
    out = {}
    k = min(TOPN, len(items) - 1)
    top = np.argpartition(-S, k, axis=1)[:, :k] if k > 0 else np.zeros((len(items), 0), int)
    for i in range(len(items)):
        js = top[i][np.argsort(-S[i, top[i]])]
        out[int(items[i])] = [(int(items[j]), float(S[i, j])) for j in js if S[i, j] > 0]
    del S, C
    return out


def nb_score(nb, likes):
    s = {}
    for f in likes:
        for g, sc in nb.get(f, ()):
            s[g] = s.get(g, 0.0) + sc
    return s


def live_neighbours(path, likes):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    s = {}
    for f in likes:
        for nbid, sc in c.execute("select neighbour_id, score from ml_neighbours where tmdb_id = ? order by rank limit 30", (f,)):
            s[nbid] = s.get(nbid, 0.0) + sc
    c.close()
    return s


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    return {k: (v - lo) / (hi - lo) if hi > lo else 0.0 for k, v in d.items()}


def dcg(hits):
    return sum(1.0 / math.log2(i + 2) for i, h in enumerate(hits) if h)


def ndcg10(top, pos):
    ideal = dcg([True] * min(10, len(pos)))
    return dcg([f in pos for f in top[:10]]) / ideal if ideal else 0.0


def jacc_div(top, genres):
    gs = [genres.get(f, frozenset()) for f in top]
    pairs = [(a, b) for i, a in enumerate(gs) for b in gs[i + 1:]]
    if not pairs:
        return 0.0
    return 1 - float(np.mean([len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in pairs]))


def mmr(scores, cand, genres, lam=MMR_LAMBDA, k=10):
    left = sorted(cand, key=lambda f: (-scores[f], f))[:60]
    out = []
    while left and len(out) < k:
        def val(f):
            if not out:
                return scores[f]
            g = genres.get(f, frozenset())
            sim = max((len(g & genres.get(o, frozenset())) / len(g | genres.get(o, frozenset()))) if (g | genres.get(o, frozenset())) else 0
                      for o in out)
            return lam * scores[f] - (1 - lam) * sim
        best = max(left, key=lambda f: (val(f), -f))
        out.append(best)
        left.remove(best)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.expanduser("~/movie-buff/rag/data/movielens/ml-32m.zip"))
    ap.add_argument("--movies", default=os.path.expanduser("~/movie-buff/rag/movies.db"))
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--negatives", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_recs_benchmark"))
    a = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(f"file:{F.PATH}?mode=ro", uri=True)
    vc, genres = {}, {}
    for fid, v, g in conn.execute("select tmdb_id, vote_count, genres_json from films"):
        vc[fid] = v or 0
        try:
            genres[fid] = frozenset(json.loads(g or "[]"))
        except ValueError:
            genres[fid] = frozenset()
    R = load(a.zip, set(vc))
    train, test = R[R.timestamp < CUT], R[(R.timestamp >= CUT) & (R.timestamp < END) & (R.rating >= 4)]
    L = [f"Recommendation benchmark (v8.1 slice 5.2a) on MovieLens 32M - measures the METHOD on MovieLens users, not Cinemack users",
         f"ratings mapped to facts.db films: {len(R):,} | train (< 2020): {len(train):,} | test positives (2020): {len(test):,}"]
    pop = train.tmdb.value_counts().to_dict()
    t1 = time.time()
    NB = neighbours_train(train)
    L.append(f"train-only neighbours: {len(NB):,} films with >= {MIN_ITEM} train ratings ({time.time() - t1:.0f} s)")
    ntr = train.groupby("userId").size()
    tpos = test.groupby("userId").tmdb.apply(set).to_dict()
    elig = sorted(u for u in tpos if ntr.get(u, 0) >= 20)
    rnd = random.Random(13)
    users = sorted(rnd.sample(elig, min(a.users, len(elig))))
    L.append(f"eligible users: {len(elig):,}; sampled {len(users):,} (seed 13)")
    pool = np.array(sorted(pop))
    pw = np.array([pop[f] for f in pool], dtype=float)
    pw /= pw.sum()
    tr_by_user = {u: g for u, g in train[train.userId.isin(users)].groupby("userId")}
    ever = R[R.userId.isin(users)].groupby("userId").tmdb.apply(set).to_dict()
    per = {"uniform": {}, "popular": {}}
    methods = ("popularity", "content", "neighbours_train", "current_live", "current_timesafe")
    comps_store = {"uniform": [], "popular": []}
    for n_u, u in enumerate(users):
        hist = tr_by_user[u].sort_values("timestamp")
        likes = hist[hist.rating >= 4].tmdb.tolist()[-50:]
        dis = hist[hist.rating <= 2].tmdb.tolist()[-20:]
        pos = tpos[u] - set(hist.tmdb)
        if not pos or not likes:
            continue
        prof = personal.profile(likes, dis, conn)
        nbt = nb_score(NB, likes)
        nbl = live_neighbours(a.movies, likes)
        urnd = np.random.default_rng(u)
        for proto in ("uniform", "popular"):
            neg = set()
            tries = 0
            while len(neg) < a.negatives and tries < 20:
                draw = urnd.choice(pool, size=a.negatives * 2, p=pw if proto == "popular" else None)
                neg |= {int(f) for f in draw if int(f) not in ever[u]}
                tries += 1
            neg = sorted(neg)[:a.negatives]
            cand = sorted(set(pos) | set(neg))
            c_ = {f: personal.match(prof, f, conn)[0] for f in cand}
            raw = {"c": c_, "nt": {f: min(nbt.get(f, 0.0), 2.0) for f in cand}, "nl": {f: min(nbl.get(f, 0.0), 2.0) for f in cand},
                   "pt": {f: math.log1p(pop.get(f, 0)) for f in cand}, "pl": {f: math.log1p(vc.get(f, 0)) for f in cand}}
            s = {k: minmax(v) for k, v in raw.items()}
            sc = {"popularity": raw["pt"], "content": raw["c"], "neighbours_train": raw["nt"],
                  "current_live": {f: LIVE_W[0] * s["c"][f] + LIVE_W[1] * s["nl"][f] + LIVE_W[2] * s["pl"][f] for f in cand},
                  "current_timesafe": {f: LIVE_W[0] * s["c"][f] + LIVE_W[1] * s["nt"][f] + LIVE_W[2] * s["pt"][f] for f in cand}}
            per[proto][u] = {m: sorted(cand, key=lambda f: (-sc[m][f], f))[:10] for m in methods}
            comps_store[proto].append((u, pos, cand, s))
        if n_u % 500 == 0:
            print(f"  {n_u:,}/{len(users):,} users, {time.time() - t0:.0f} s", flush=True)
    half = lambda u: "select" if (u * 2654435761) % 2**32 < 2**31 else "report"
    res = {"n_users": len(users), "protocols": {}}
    for proto in ("uniform", "popular"):
        rows = comps_store[proto]
        sel = [r for r in rows if half(r[0]) == "select"]
        rep = [r for r in rows if half(r[0]) == "report"]

        def ndcg_w(rs, w):
            return np.mean([ndcg10(sorted(cand, key=lambda f: (-(w[0] * s["c"][f] + w[1] * s["nt"][f] + w[2] * s["pt"][f]), f)), pos)
                            for _u, pos, cand, s in rs]) if rs else 0.0
        best = max(GRID, key=lambda w: (ndcg_w(sel, w), w))
        for u, pos, cand, s in rows:
            sc = {f: best[0] * s["c"][f] + best[1] * s["nt"][f] + best[2] * s["pt"][f] for f in cand}
            per[proto][u]["v2"] = mmr(sc, cand, genres)
        rep_users = {r[0]: (r[1], r[2]) for r in rep}
        out = {"weights_v2": best, "n_report": len(rep_users), "methods": {}}
        nd = {}
        for m in methods + ("v2",):
            vals, rec, shown, divs = [], [], set(), []
            allc = set()
            for u, (pos, cand) in rep_users.items():
                top = per[proto][u][m]
                vals.append(ndcg10(top, pos))
                rec.append(len(set(top) & pos) / min(10, len(pos)))
                shown |= set(top)
                allc |= set(cand)
                divs.append(jacc_div(top, genres))
            nd[m] = np.array(vals)
            out["methods"][m] = {"ndcg10": float(np.mean(vals)), "recall10": float(np.mean(rec)),
                                 "coverage": len(shown) / max(1, len(allc)), "diversity": float(np.mean(divs))}
        rng = np.random.default_rng(0)
        for ref in ("current_timesafe", "current_live"):
            d = nd["v2"] - nd[ref]
            bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
            out[f"v2_minus_{ref}"] = [float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
        ci = out["v2_minus_current_timesafe"]
        cov_ok = out["methods"]["v2"]["coverage"] >= out["methods"]["current_timesafe"]["coverage"]
        out["rule"] = "PASS (candidate)" if ci[2] >= 0 and cov_ok else "FAIL"
        res["protocols"][proto] = out
        L += ["", f"[{proto} negatives] report half: {len(rep_users):,} users; v2 weights chosen on the select half: "
                  f"content {best[0]}, neighbours {best[1]}, popularity {best[2]}",
              f"  {'method':18s} {'NDCG@10':>8s} {'Recall@10':>9s} {'coverage':>9s} {'diversity':>9s}"]
        for m, v in out["methods"].items():
            L.append(f"  {m:18s} {v['ndcg10']:8.3f} {v['recall10']:9.3f} {v['coverage']:9.3f} {v['diversity']:9.3f}")
        for ref in ("current_timesafe", "current_live"):
            d = out[f"v2_minus_{ref}"]
            L.append(f"  v2 - {ref}: NDCG {d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}]")
        L.append(f"  RULE (vs current_timesafe: NDCG CI not wholly below 0 AND coverage not lower): {out['rule']}")
    L.append(f"\n{time.time() - t0:.0f} s")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out + ".txt", "w").write("\n".join(L) + "\n")
    json.dump(res, open(a.out + ".json", "w"), indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
