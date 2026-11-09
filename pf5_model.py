#!/usr/bin/env python3
"""
pf5 (9 Nov 2026): pre-release inputs for the box-office model, each group tested against pf4 (BEFORE + trailers).
Reported only: nothing in the site or the per-film expectations changes (PERF=0 in the lock).

Rules (6 Nov plan, written before any pf5 data; the B and range rules restated 9 Nov ~13:00 before this version ran):
  main     pf5 BEFORE (the groups that pass) beats pf4 BEFORE + trailers on 2016-2019, bootstrap 95% CI of the MAE
           difference excluding 0; each group alone vs pf4 the same way. 2020-2023 reported, not tested.
  A        people in the news (film_people_news). Run 1 (11:57): no.
  C1       my first version (economy LEVELS + genre momentum + catalogue COUNTS). Run 1: worse - the counts track how
           complete the catalogue is by year. Kept only so run 1 is reproducible.
  C        AS PLANNED (6 Nov): genre momentum (median return of the same first genre, films released 15 to 3 months
           before - their runs are over), franchise fatigue (returns of the last one and last two films in the
           series), economy two months before release (unemployment, consumer sentiment, CPI inflation over 12
           months). No count features. NOT BLIND: 2016-2019 was seen once by C1.
  D        track records: star power (log of the top-3 cast's inflation-adjusted grosses in the 5 years before),
           recent form (mean return of the director's and top-3 cast's last earlier film), years since the
           director's / the lead's last hit (return > 2.5x), Oscar + Golden Globe nominations of their earlier films
           (award year before the release year).
  B        Guardian articles before release. Training coverage is too small for the main split (500 calls/day), so B
           gets its own test like BUZZ in pf3: 5-fold cross-validation over the 2016-2019 films that were fetched
           (all of them), each fold's model trained on every other film of all years. Claimed only if the CI of the
           MAE difference excludes 0. Articles count only when the title is in the headline or trail text.
  RANGE    10% and 90% quantile models on pf4's inputs: the share of 2016-2019 films whose actual return falls inside
           the 80% range must be 75-85% ("calibrated").
  LB       AFTER + Letterboxd mean rating (no rating count: audience size leaks the outcome) vs AFTER, reported only.

Checks printed so a bad feature cannot pass silently: the pf4 arm reproduces 1.200; a shuffled control of all new
features; coverage and mean of each feature by period (era drift).

    python pf5_model.py            # -> results/pf5_summary.txt, results/pf5_result.json
"""
import bisect
import datetime
import json
import math
import os
import re
import time

import numpy as np

import performance as P

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TXT = os.path.join(HERE, "results", "pf5_summary.txt")
OUT_JSON = os.path.join(HERE, "results", "pf5_result.json")
PF4_BEFORE_TRAILER_MAE = 1.200
NAN = float("nan")
HIT = math.log(2.5)
PA = ["pn_covered", "pn_log_pre", "pn_log_ratio", "pn_log_max_ratio", "pn_scandal", "pn_award"]
PC1 = ["mac_unrate", "mac_sentiment", "mac_cpi_yoy", "mom_genre", "mom_genre_n", "mom_all", "mom_all_n"]
PC = ["mac_unrate", "mac_sentiment", "mac_cpi_yoy", "mom_genre12", "fr_last", "fr_last2"]
PD = ["d_star_gross", "d_recent_form", "d_dir_since_hit", "d_lead_since_hit", "d_prior_noms"]
PB = ["b_log_n", "b_last28_share", "b_marketing"]
MARKETING = re.compile(r"\b(trailer|teaser|box office|tracking|projection|opening weekend|marketing|poster|first look)\b", re.I)


def _lg(v):
    return math.log(v) if v and v > 0 else NAN


def _month_back(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def load(c):
    info = {}
    for fid, rd, usrd, genres, coll, title in c.execute(
            "select tmdb_id, release_date, us_release_date, genres_json, collection_id, title from films"):
        g = json.loads(genres or "[]")
        info[fid] = {"date": P._date(usrd) or P._date(rd), "g0": g[0] if g else None, "coll": coll, "title": title}
    ret = {fid: math.log(g / b) for fid, b, g in c.execute("select tmdb_id, budget, gross from money")
           if b and g and b >= 1e5 and g > 0}
    greal = {}
    try:
        greal = {fid: g for fid, g in c.execute("select tmdb_id, gross_real from money_real") if g}
    except Exception:  # noqa: BLE001
        pass
    return info, ret, greal


def add_people(c, rows):
    have = {}
    try:
        for fid, people, cov, pre, base, ratio, mx, sc, aw in c.execute(
                "select tmdb_id, people, covered, pre_total, base90_total, ratio, max_person_ratio, scandal, award "
                "from film_people_news"):
            have[fid] = (people, cov, pre, base, ratio, mx, sc, aw)
    except Exception as e:  # noqa: BLE001
        print(f"film_people_news not readable: {e}")
    for r in rows:
        h = have.get(r["tmdb_id"])
        if not h or not h[0]:
            r.update({k: NAN for k in PA})
            continue
        people, cov, pre, base, ratio, mx, sc, aw = h
        r.update(pn_covered=float(cov or 0), pn_log_pre=math.log1p(pre or 0), pn_log_ratio=_lg(ratio),
                 pn_log_max_ratio=_lg(mx), pn_scandal=float(sc or 0), pn_award=float(aw or 0))


def add_context(c, rows, info, ret):
    """C1 (run 1, kept for reproducibility) and C as planned."""
    ser = {}
    for s, mo, v in c.execute("select series, month, value from macro"):
        ser.setdefault(s, {})[mo] = v
    un, se, cpi = ser.get("UNRATE", {}), ser.get("UMCSENT", {}), ser.get("CPIAUCNS", {})
    dated = sorted((info[f]["date"], f) for f in ret if f in info and info[f]["date"])
    ds = [d for d, _f in dated]
    by_coll = {}
    for d, f in dated:
        if info[f]["coll"]:
            by_coll.setdefault(info[f]["coll"], []).append((d, f))
    for r in rows:
        fid = r["tmdb_id"]
        d = info.get(fid, {}).get("date")
        if not d:
            r.update({k: NAN for k in set(PC1 + PC)})
            continue
        g0 = info[fid]["g0"]
        m2, m14 = _month_back(d, 2), _month_back(d, 14)
        r["mac_unrate"] = un.get(m2, NAN)
        r["mac_sentiment"] = se.get(m2, NAN)
        r["mac_cpi_yoy"] = (cpi[m2] / cpi[m14] - 1) if cpi.get(m2) and cpi.get(m14) else NAN
        # C1 window (run 1): 480-120 days, with counts
        i, j = bisect.bisect_left(ds, d - datetime.timedelta(days=480)), bisect.bisect_right(ds, d - datetime.timedelta(days=120))
        win = [f for _d, f in dated[i:j] if f != fid]
        same = [ret[f] for f in win if info[f]["g0"] == g0]
        r["mom_genre"] = float(np.median(same)) if len(same) >= 5 else NAN
        r["mom_genre_n"] = float(len(same))
        r["mom_all"] = float(np.median([ret[f] for f in win])) if len(win) >= 5 else NAN
        r["mom_all_n"] = float(len(win))
        # C as planned: 12 months, ending 3 months before release (runs over), no counts
        i, j = bisect.bisect_left(ds, d - datetime.timedelta(days=455)), bisect.bisect_right(ds, d - datetime.timedelta(days=90))
        same12 = [ret[f] for _d, f in dated[i:j] if f != fid and info[f]["g0"] == g0]
        r["mom_genre12"] = float(np.median(same12)) if len(same12) >= 5 else NAN
        earlier = [f for dd, f in by_coll.get(info[fid]["coll"], []) if dd < d] if info[fid]["coll"] else []
        r["fr_last"] = ret[earlier[-1]] if earlier else NAN
        r["fr_last2"] = float(np.mean([ret[f] for f in earlier[-2:]])) if len(earlier) >= 2 else NAN


def add_track(c, rows, info, ret, greal):
    by_person, crew = {}, {}
    for fid, pid, kind, job, ordn in c.execute("select tmdb_id, person_id, kind, job, ord from credits "
                                               "where (kind = 'crew' and job = 'Director') or (kind = 'cast' and ord < 3)"):
        if fid in info and info[fid]["date"]:
            by_person.setdefault(pid, []).append(fid)
            crew.setdefault(fid, {"dir": [], "cast": []})["dir" if kind == "crew" else "cast"].append((ordn or 0, pid))
    for pid in by_person:
        by_person[pid] = sorted(set(by_person[pid]), key=lambda f: info[f]["date"])
    noms = {}
    try:
        for fid, yr in c.execute("select tmdb_id, year from awards where (award like '%Academy Award%' or "
                                 "award like '%Golden Globe%') and status in ('nominated', 'won')"):
            noms.setdefault(fid, []).append(yr)
    except Exception as e:  # noqa: BLE001
        print(f"awards not readable: {e}")

    def earlier(pid, d):
        fs = by_person.get(pid, [])
        return fs[:bisect.bisect_left([info[f]["date"] for f in fs], d)]

    def since_hit(pid, d):
        hits = [info[f]["date"] for f in earlier(pid, d) if ret.get(f, -9) > HIT]
        return (d - hits[-1]).days / 365.25 if hits else NAN

    for r in rows:
        fid = r["tmdb_id"]
        d = info.get(fid, {}).get("date")
        cr = crew.get(fid)
        if not d or not cr:
            r.update({k: NAN for k in PD})
            continue
        dirs = [p for _o, p in cr["dir"]]
        cast = [p for _o, p in sorted(cr["cast"])][:3]
        five = d - datetime.timedelta(days=5 * 365)
        g = [greal[f] for p in cast for f in earlier(p, d) if info[f]["date"] >= five and f in greal]
        r["d_star_gross"] = math.log1p(sum(g)) if cast else NAN
        last = []
        for p in dirs + cast:
            e = [f for f in earlier(p, d) if f in ret]
            if e:
                last.append(ret[e[-1]])
        r["d_recent_form"] = float(np.mean(last)) if last else NAN
        r["d_dir_since_hit"] = min(since_hit(dirs[0], d), 40.0) if dirs else NAN
        r["d_lead_since_hit"] = min(since_hit(cast[0], d), 40.0) if cast else NAN
        n = 0
        for p in dirs + cast:
            for f in earlier(p, d):
                n += sum(1 for y in noms.get(f, []) if y and y < d.year)
        r["d_prior_noms"] = float(n)


def add_press(c, rows, info):
    done, arts = {}, {}
    try:
        done = {fid: st for fid, st in c.execute("select tmdb_id, status from film_press_done")}
        for fid, pub, head, trail in c.execute("select tmdb_id, pub_date, headline, trail from film_press"):
            arts.setdefault(fid, []).append((pub, head or "", trail or ""))
    except Exception as e:  # noqa: BLE001
        print(f"film_press not readable: {e}")
    for r in rows:
        fid = r["tmdb_id"]
        if done.get(fid) != "ok" or not info.get(fid, {}).get("date"):
            r.update({k: NAN for k in PB})
            continue
        t = (info[fid]["title"] or "").casefold()
        rel = [a for a in arts.get(fid, []) if t and (t in a[1].casefold() or t in a[2].casefold())]
        d = info[fid]["date"]
        last28 = [a for a in rel if a[0] and P._date(a[0][:10]) and (d - P._date(a[0][:10])).days <= 28]
        r["b_log_n"] = math.log1p(len(rel))
        r["b_last28_share"] = len(last28) / len(rel) if rel else 0.0
        r["b_marketing"] = float(sum(1 for a in rel if MARKETING.search(a[1] + " " + a[2])))


def describe(rows_by, cols):
    L = []
    for k in cols:
        parts = []
        for name, rs in rows_by:
            v = np.array([r[k] for r in rs], dtype=float)
            ok = ~np.isnan(v)
            parts.append(f"{name} {ok.sum():,}/{len(v):,} mean {np.nanmean(v) if ok.any() else float('nan'):.3f}")
        L.append(f"  {k:18s} " + " | ".join(parts))
    return L


def cv_press(uni, base, bcols, new, folds=5, seed=0):
    elig = [i for i, r in enumerate(uni) if P.TEST[0] <= r["year"] <= P.TEST[1] and not math.isnan(r["b_log_n"])]
    if len(elig) < 150:
        return None
    rng = np.random.default_rng(seed)
    order = rng.permutation(elig)
    e0, e1 = np.zeros(len(order)), np.zeros(len(order))
    for k in range(folds):
        fold = set(order[k::folds].tolist())
        tr = [r for i, r in enumerate(uni) if i not in fold]
        te = [uni[i] for i in order[k::folds]]
        ytr, yte = np.array([r["actual"] for r in tr]), np.array([r["actual"] for r in te])
        idx = np.arange(len(order))[k::folds]
        for cols, e in ((base, e0), (base + bcols, e1)):
            m = new()
            m.fit(P.matrix(tr, cols), ytr)
            e[idx] = np.abs(m.predict(P.matrix(te, cols)) - yte)
    d = P.boot_diff(e1, e0)
    return {"n": len(order), "mae_base": round(float(e0.mean()), 4), "mae_b": round(float(e1.mean()), 4),
            "diff": [round(x, 4) for x in d]}


def ranges(tr, te, cols):
    try:
        import lightgbm as lgb
    except ImportError:
        return None
    ytr, yte = np.array([r["actual"] for r in tr]), np.array([r["actual"] for r in te])
    q = {}
    for a in (0.1, 0.9):
        m = lgb.LGBMRegressor(objective="quantile", alpha=a, n_estimators=500, learning_rate=0.03, num_leaves=15,
                              min_child_samples=30, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              random_state=7, verbose=-1)
        m.fit(P.matrix(tr, cols), ytr)
        q[a] = m.predict(P.matrix(te, cols))
    lo, hi = np.minimum(q[0.1], q[0.9]), np.maximum(q[0.1], q[0.9])
    inside = (yte >= lo) & (yte <= hi)
    return {"coverage": round(float(inside.mean()), 4), "below": round(float((yte < lo).mean()), 4),
            "above": round(float((yte > hi).mean()), 4), "median_width_factor": round(float(np.exp(np.median(hi - lo))), 2)}


def main():
    t0 = time.time()
    c = P.conn(readonly=True)
    rows = P.build_table(c)
    info, ret, greal = load(c)
    add_people(c, rows)
    add_context(c, rows, info, ret)
    add_track(c, rows, info, ret, greal)
    add_press(c, rows, info)
    uni = [r for r in rows if r["actual"] is not None and r["year"] <= P.LATE[1]]
    tr = [r for r in uni if r["year"] <= P.TRAIN_TO]
    te = [r for r in uni if P.TEST[0] <= r["year"] <= P.TEST[1]]
    la = [r for r in uni if P.LATE[0] <= r["year"] <= P.LATE[1]]
    usable = lambda cols: [k for k in cols if any(not (isinstance(r.get(k), float) and math.isnan(r[k])) for r in uni)]
    BEF, TR, A, C1, C, D, B = (usable(x) for x in (P.BEFORE, P.TRAILER, PA, PC1, PC, PD, PB))
    base = BEF + TR
    ytr, yte, yla = (np.array([r["actual"] for r in s]) for s in (tr, te, la))
    kind, new = P.make_model()
    arms = {"pf4 before+trailers": base, "+A people (run 1)": base + A, "+C1 first version (run 1)": base + C1,
            "+C as planned (not blind)": base + C, "+D track records": base + D}
    preds, imp = {}, {}
    for name, cols in arms.items():
        m = new()
        m.fit(P.matrix(tr, cols), ytr)
        preds[name] = (m.predict(P.matrix(te, cols)), m.predict(P.matrix(la, cols)) if la else np.array([]))
        if kind == "lightgbm":
            con, _b = P.contributions(kind, m, P.matrix(te, cols))
            imp[name] = sorted(((cols[j], float(np.abs(con[:, j]).mean())) for j in range(len(cols))), key=lambda x: -x[1])
    err = {k: np.abs(v[0] - yte) for k, v in preds.items()}
    b0 = err["pf4 before+trailers"]
    passed = [g for g, k in (("A", "+A people (run 1)"), ("C", "+C as planned (not blind)"), ("D", "+D track records"))
              if P.boot_diff(err[k], b0)[2] < 0]
    combo = base + sum(({"A": A, "C": C, "D": D}[g] for g in passed), [])
    if passed:
        m = new(); m.fit(P.matrix(tr, combo), ytr)
        name = f"pf5 (+{' +'.join(passed)})"
        preds[name] = (m.predict(P.matrix(te, combo)), m.predict(P.matrix(la, combo)) if la else np.array([]))
        err[name] = np.abs(preds[name][0] - yte)
    rng = np.random.default_rng(11)
    newc = A + C + D
    Xtr, Xte = P.matrix(tr, base + newc), P.matrix(te, base + newc)
    k0 = len(base)
    Xtr[:, k0:] = Xtr[rng.permutation(len(Xtr)), k0:]
    Xte[:, k0:] = Xte[rng.permutation(len(Xte)), k0:]
    m = new(); m.fit(Xtr, ytr)
    preds["shuffled control (A+C+D)"] = (m.predict(Xte), np.array([]))
    err["shuffled control (A+C+D)"] = np.abs(preds["shuffled control (A+C+D)"][0] - yte)
    res = {"version": "pf5-ACDB", "model": kind, "n_train": len(tr), "n_test": len(te), "n_late": len(la),
           "features": {"A": A, "C1": C1, "C": C, "D": D, "B": B}, "test": {}, "late": {}, "diff": {}, "passed": passed}
    L = [f"pf5 - {datetime.datetime.now():%Y-%m-%d %H:%M}, {kind}",
         f"train <= {P.TRAIN_TO}: {len(tr):,} films; test {P.TEST[0]}-{P.TEST[1]}: {len(te):,}; late {P.LATE[0]}-{P.LATE[1]}: {len(la):,}",
         "", f"{'arm':30s} {'MAE test':>9s} {'rho':>6s} {'MAE late':>9s}   diff vs pf4 [95% CI]"]
    for k in preds:
        mae = float(err[k].mean()); rho = P.spearman(preds[k][0], yte)
        lmae = float(np.abs(preds[k][1] - yla).mean()) if len(preds[k][1]) else float("nan")
        d = P.boot_diff(err[k], b0) if k != "pf4 before+trailers" else None
        res["test"][k] = {"mae": round(mae, 4), "spearman": round(rho, 4)}
        res["late"][k] = round(lmae, 4)
        if d:
            res["diff"][k] = [round(x, 4) for x in d]
        L.append(f"{k:30s} {mae:9.3f} {rho:+6.3f} {lmae:9.3f}   " + (
            f"{d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]{'  <- better (CI excludes 0)' if d[2] < 0 else ''}" if d else "(reference)"))
    ok_ref = abs(res["test"]["pf4 before+trailers"]["mae"] - PF4_BEFORE_TRAILER_MAE) <= 0.01
    ok_shuf = res["diff"]["shuffled control (A+C+D)"][2] >= 0
    res["checks"] = {"reproduces_pf4": ok_ref, "shuffled_not_better": ok_shuf}
    res["rule_pf5_beats_pf4"] = bool(passed) and ok_ref and ok_shuf
    L += ["", f"CHECK pf4 arm reproduces {PF4_BEFORE_TRAILER_MAE} within 0.01: {'yes' if ok_ref else 'NO - comparison suspect'}",
          f"CHECK shuffled control does not beat pf4: {'yes' if ok_shuf else 'NO - gains may be noise/leakage'}",
          f"groups that pass alone: {', '.join(passed) or 'none'}",
          f"RULE pf5 beats pf4 on {P.TEST[0]}-{P.TEST[1]}: {'YES (' + ', '.join(passed) + ')' if res['rule_pf5_beats_pf4'] else 'no'}"]
    b = cv_press(uni, base, B, new) if B else None
    res["B_cv"] = b
    L += ["", "B Guardian articles, 5-fold CV over the fetched 2016-2019 films (own test, like BUZZ):"]
    L.append(f"  n {b['n']:,}; MAE pf4 {b['mae_base']:.3f} -> +B {b['mae_b']:.3f}; diff {b['diff'][0]:+.3f} "
             f"[{b['diff'][1]:+.3f}, {b['diff'][2]:+.3f}] -> {'helps (CI excludes 0)' if b['diff'][2] < 0 else 'no'}"
             if b else "  not enough fetched films")
    rg = ranges(tr, te, base)
    res["range"] = rg
    L += ["", "80% range (10% and 90% quantile models on pf4's inputs), 2016-2019:"]
    L.append(f"  coverage {rg['coverage']:.1%} (below {rg['below']:.1%}, above {rg['above']:.1%}); median width x{rg['median_width_factor']}"
             f" -> {'calibrated (75-85%)' if 0.75 <= rg['coverage'] <= 0.85 else 'NOT calibrated'}" if rg else "  lightgbm missing")
    aft = [k for k in P.AFTER if k in {kk for kk in usable(P.AFTER)}]
    if usable(["lb_rating"]):
        ea = []
        for cols in (aft, aft + ["lb_rating"]):
            m = new(); m.fit(P.matrix(tr, cols), ytr); ea.append(np.abs(m.predict(P.matrix(te, cols)) - yte))
        dl = P.boot_diff(ea[1], ea[0])
        res["lb_rating_only"] = [round(x, 4) for x in dl]
        L += ["", f"Letterboxd rating only (no count), AFTER + lb_rating - AFTER: {dl[0]:+.3f} [{dl[1]:+.3f}, {dl[2]:+.3f}] (reported only)"]
    L += ["", "feature coverage and means (train | test | late):"]
    L += describe([("train", tr), ("test", te), ("late", la)], A + C + D + B)
    for name in [k for k in imp if k.startswith(("+C as", "+D"))]:
        L += ["", f"{name}: mean |contribution| on the test films (new features *):"]
        L += [f"  {'*' if k in C + D else ' '} {k:24s} {v:.4f}" for k, v in imp[name][:12]]
    L.append(f"\n({time.time() - t0:.0f} s)")
    open(OUT_TXT, "w").write("\n".join(L) + "\n")
    json.dump(res, open(OUT_JSON, "w"), indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
