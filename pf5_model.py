#!/usr/bin/env python3
"""
pf5 (9 Nov 2026): do PRE-RELEASE people-in-the-news (A) and the economy + genre momentum (C) improve the box-office
model? Reported only: nothing in the site or the per-film expectations changes (PERF=0 in the lock).

Rule (written 6 Nov 2026, before any pf5 data; restated here before this script was run):
  pf5 BEFORE beats pf4 BEFORE + trailers on 2016-2019 with the bootstrap 95% CI of the MAE difference excluding 0;
  each group alone vs pf4 is reported the same way. 2020-2023 is reported, not tested (pandemic years).
  B (Guardian articles about the film) is NOT tested here: training coverage is still tiny (66 / 9,346 on 9 Nov).

Features (all known before release):
  A  film_people_news (pf5_data.py people): director + top-5 cast named in news headlines in the 90 days before
     release vs their usual level (3 prior years): pn_covered, pn_log_pre, pn_log_ratio, pn_log_max_ratio,
     pn_scandal, pn_award. Films with no row are blank (NaN), not 0.
  C  FRED at release month - 2 (pf5_data.py macro): unemployment, consumer sentiment, CPI inflation over 12 months;
     genre momentum: median return of films of the same first genre released 120-480 days before (their runs are
     over by then), and of all films in that window; counts. Blank when fewer than 5 films.

Checks printed so a bad feature cannot pass silently:
  - the pf4 arm must reproduce pf4's BEFORE + trailers MAE (1.200 on 6 Nov) within 0.01, else the comparison is off
  - coverage and mean of each feature in train / test / late (era drift, e.g. headline volume changing by decade)
  - a SHUFFLED arm: the new features permuted across training films; it should not beat pf4 (if it does, the
    "gain" is noise or leakage)

    python pf5_model.py            # -> results/pf5_summary.txt, results/pf5_result.json
"""
import bisect
import datetime
import json
import math
import os
import sys
import time

import numpy as np

import performance as P

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_TXT = os.path.join(HERE, "results", "pf5_summary.txt")
OUT_JSON = os.path.join(HERE, "results", "pf5_result.json")
PF4_BEFORE_TRAILER_MAE = 1.200          # 6 Nov fit, results/performance_summary.txt
NAN = float("nan")
PA = ["pn_covered", "pn_log_pre", "pn_log_ratio", "pn_log_max_ratio", "pn_scandal", "pn_award"]
PC = ["mac_unrate", "mac_sentiment", "mac_cpi_yoy", "mom_genre", "mom_genre_n", "mom_all", "mom_all_n"]


def _lg(v):
    return math.log(v) if v and v > 0 else NAN


def film_info(c):
    out = {}
    for fid, rd, usrd, genres in c.execute("select tmdb_id, release_date, us_release_date, genres_json from films"):
        g = json.loads(genres or "[]")
        out[fid] = (P._date(usrd) or P._date(rd), g[0] if g else None)
    return out


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
    return len(have)


def _month_back(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def add_macro_momentum(c, rows, info):
    ser = {}
    for s, mo, v in c.execute("select series, month, value from macro"):
        ser.setdefault(s, {})[mo] = v
    un, se, cpi = ser.get("UNRATE", {}), ser.get("UMCSENT", {}), ser.get("CPIAUCNS", {})
    dated = sorted(((info.get(r["tmdb_id"], (None, None))[0], r) for r in rows if r["actual"] is not None
                    and info.get(r["tmdb_id"], (None, None))[0]), key=lambda x: x[0])
    ds = [d for d, _r in dated]
    for r in rows:
        d, g0 = info.get(r["tmdb_id"], (None, None))
        if not d:
            r.update({k: NAN for k in PC})
            continue
        m2, m14 = _month_back(d, 2), _month_back(d, 14)
        r["mac_unrate"] = un.get(m2, NAN)
        r["mac_sentiment"] = se.get(m2, NAN)
        r["mac_cpi_yoy"] = (cpi[m2] / cpi[m14] - 1) if cpi.get(m2) and cpi.get(m14) else NAN
        lo, hi = d - datetime.timedelta(days=480), d - datetime.timedelta(days=120)

        i, j = bisect.bisect_left(ds, lo), bisect.bisect_right(ds, hi)
        win = [x for _d, x in dated[i:j] if x["tmdb_id"] != r["tmdb_id"]]
        same = [x["actual"] for x in win if info.get(x["tmdb_id"], (None, None))[1] == g0]
        allv = [x["actual"] for x in win]
        r["mom_genre"] = float(np.median(same)) if len(same) >= 5 else NAN
        r["mom_genre_n"] = float(len(same))
        r["mom_all"] = float(np.median(allv)) if len(allv) >= 5 else NAN
        r["mom_all_n"] = float(len(allv))


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


def main():
    t0 = time.time()
    c = P.conn(readonly=True) if "readonly" in P.conn.__code__.co_varnames else P.conn()
    rows = P.build_table(c)
    info = film_info(c)
    n_people = add_people(c, rows)
    add_macro_momentum(c, rows, info)
    uni = [r for r in rows if r["actual"] is not None and r["year"] <= P.LATE[1]]
    tr = [r for r in uni if r["year"] <= P.TRAIN_TO]
    te = [r for r in uni if P.TEST[0] <= r["year"] <= P.TEST[1]]
    la = [r for r in uni if P.LATE[0] <= r["year"] <= P.LATE[1]]
    usable = lambda cols: [k for k in cols if any(not (isinstance(r[k], float) and math.isnan(r[k])) for r in uni)]
    BEF, TR, A, C = usable(P.BEFORE), usable(P.TRAILER), usable(PA), usable(PC)
    base = BEF + TR
    ytr, yte, yla = (np.array([r["actual"] for r in s]) for s in (tr, te, la))
    kind, new = P.make_model()
    arms = {"pf4 before+trailers": base, "+A people": base + A, "+C economy+momentum": base + C, "pf5 (+A +C)": base + A + C}
    preds, imp = {}, {}
    for name, cols in arms.items():
        m = new()
        m.fit(P.matrix(tr, cols), ytr)
        preds[name] = (m.predict(P.matrix(te, cols)), m.predict(P.matrix(la, cols)) if la else np.array([]))
        if kind == "lightgbm":
            con, _b = P.contributions(kind, m, P.matrix(te, cols))
            imp[name] = sorted(((cols[j], float(np.abs(con[:, j]).mean())) for j in range(len(cols))), key=lambda x: -x[1])
    # shuffled control: the new features permuted across training (and test) films
    rng = np.random.default_rng(11)
    newc = A + C
    Xtr, Xte = P.matrix(tr, base + newc), P.matrix(te, base + newc)
    k0 = len(base)
    Xtr[:, k0:] = Xtr[rng.permutation(len(Xtr)), k0:]
    Xte[:, k0:] = Xte[rng.permutation(len(Xte)), k0:]
    m = new(); m.fit(Xtr, ytr)
    preds["shuffled control"] = (m.predict(Xte), np.array([]))
    err = {k: np.abs(v[0] - yte) for k, v in preds.items()}
    b0 = err["pf4 before+trailers"]
    res = {"version": "pf5-AC", "model": kind, "n_train": len(tr), "n_test": len(te), "n_late": len(la),
           "people_rows": n_people, "features": {"A": A, "C": C}, "test": {}, "late": {}, "diff": {}}
    L = [f"pf5 (A people in the news, C economy + genre momentum) - {datetime.datetime.now():%Y-%m-%d %H:%M}, {kind}",
         f"train <= {P.TRAIN_TO}: {len(tr):,} films; test {P.TEST[0]}-{P.TEST[1]}: {len(te):,}; late {P.LATE[0]}-{P.LATE[1]}: {len(la):,}",
         "", f"{'arm':24s} {'MAE test':>9s} {'rho':>6s} {'MAE late':>9s}   diff vs pf4 [95% CI]"]
    for k in preds:
        mae = float(err[k].mean()); rho = P.spearman(preds[k][0], yte)
        lmae = float(np.abs(preds[k][1] - yla).mean()) if len(preds[k][1]) else float("nan")
        d = P.boot_diff(err[k], b0) if k != "pf4 before+trailers" else None
        res["test"][k] = {"mae": round(mae, 4), "spearman": round(rho, 4)}
        res["late"][k] = round(lmae, 4)
        if d:
            res["diff"][k] = [round(x, 4) for x in d]
        L.append(f"{k:24s} {mae:9.3f} {rho:+6.3f} {lmae:9.3f}   " + (f"{d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]"
                                                               f"{'  <- better (CI excludes 0)' if d[2] < 0 else ''}" if d else "(reference)"))
    ok_ref = abs(res["test"]["pf4 before+trailers"]["mae"] - PF4_BEFORE_TRAILER_MAE) <= 0.01
    ok_shuf = res["diff"]["shuffled control"][2] >= 0
    rule = res["diff"]["pf5 (+A +C)"][2] < 0
    res["checks"] = {"reproduces_pf4": ok_ref, "shuffled_not_better": ok_shuf}
    res["rule_pf5_beats_pf4"] = rule
    L += ["", f"CHECK pf4 arm reproduces 6 Nov MAE {PF4_BEFORE_TRAILER_MAE} within 0.01: {'yes' if ok_ref else 'NO - comparison suspect'}",
          f"CHECK shuffled control does not beat pf4: {'yes' if ok_shuf else 'NO - gains may be noise/leakage'}",
          f"RULE pf5 (A + C) beats pf4 before + trailers on {P.TEST[0]}-{P.TEST[1]}: {'YES' if rule and ok_ref and ok_shuf else 'no'}",
          "B (Guardian) not tested: training coverage too small.", "", "feature coverage and means (train | test | late):"]
    L += describe([("train", tr), ("test", te), ("late", la)], A + C)
    if "pf5 (+A +C)" in imp:
        L += ["", "pf5 arm, mean |contribution| on the test films (new features marked *):"]
        L += [f"  {'*' if k in A + C else ' '} {k:24s} {v:.4f}" for k, v in imp["pf5 (+A +C)"][:20]]
    L.append(f"\n({time.time() - t0:.0f} s)")
    open(OUT_TXT, "w").write("\n".join(L) + "\n")
    json.dump(res, open(OUT_JSON, "w"), indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
