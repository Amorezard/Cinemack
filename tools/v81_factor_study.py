#!/usr/bin/env python3
"""v8.1 slice 4.4 (13 Nov 2026): which groups of pre-release facts carry predictive value for box office?

Target: log(worldwide gross / production budget), as in pf1-pf5 (money table; NOT profit - marketing, theatre splits
and other costs are unknown). Same chronological split as pf4: train <= 2015, test 2016-2019, late 2020-2023.
Reference model: pf4 "before + trailers" (performance.BEFORE + TRAILER, LightGBM settings of performance.make_model).

Three kinds of evidence per group (kit section 6, "factor / correlation study"):
  1. unadjusted association  Spearman rho of each feature with the target on the training years, with coverage
  2. drop-group ablation     pf4 minus the group vs pf4: how much worse the test MAE gets without it
  3. add-group               pf4 plus a NEW group (producer and writer track records, US rating) vs pf4
Method fixed before the first run (13 Nov, recorded in IMPLEMENTATION_PLAN_LOCKED.md + this docstring):
  - every arm is fitted with 5 seeds (7, 11, 13, 17, 19) and the 5 predictions are averaged, so a difference is not
    one random seed's luck; the paired per-film absolute errors are bootstrapped (2,000 resamples, seed 0)
  - a group "carries value" if, after Holm correction over all groups tested in that direction, its two-sided
    bootstrap p-value is < 0.05 AND the effect points the right way (dropping it hurts / adding it helps)
  - 2016-2019 has been looked at by pf1-pf5: NOT blind. 2020-2023 is reported next to it as the less-seen check.
  - associations are never written as causes
New features use only films released BEFORE the film's own release date (as-of by construction); their values
(grosses, ratings) are today's values for those earlier films, which is noted in feature_provenance.csv.

Read-only on facts.db (opened mode=ro). Writes results/v81_factor_study.txt / .json, planning/cinemack-v8.1/
box_office/correlation_report.md and feature_provenance.csv.
    python tools/v81_factor_study.py [--seeds 5] [--boot 2000]
"""
import argparse
import bisect
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import performance as P  # noqa: E402

SEEDS = (7, 11, 13, 17, 19)
OUT_DIR = os.path.join(HERE, "planning", "cinemack-v8.1", "box_office")

# existing pf4 inputs, grouped by the kit's factor list (performance.py names)
DROP_GROUPS = {
    "budget": ["log_budget_real"],
    "release timing": ["month", "days_to_holiday", "horror_days_after_halloween"],
    "competition": ["same_weekend_n", "same_weekend_log_budget", "prev_10d_log_budget", "same_genre_14d_n",
                    "same_genre_prev_30d_n", "genre_month_ret"],
    "director track record": ["dir_prev_n", "dir_prev_ret", "dir_prev_rating"],
    "cast track record": ["cast_prev_n", "cast_prev_ret", "cast_prev_rating"],
    "source / author": ["source_adapted", "author_prev_365_n", "author_prev_365_ret", "author_prev_n"],
    "production companies": ["comp_n", "comp_log_budget"],
    "genre and film type": ["n_genres", "runtime", "lang_en", "us_made"] + [f"g_{g}" for g in P.GENRES],
    "franchise": ["sequel", "franchise_prev_n", "franchise_prev_ret", "franchise_gap_years", "legacy_sequel"],
    "trailers": list(P.TRAILER),
}
ADD_GROUPS = {
    "producer track record (new)": ["prod_prev_n", "prod_prev_ret", "prod_prev_5y_n"],
    "writer track record (new)": ["wri_prev_n", "wri_prev_ret", "wri_prev_5y_n"],
    "US rating (new)": ["cert_G", "cert_PG", "cert_PG13", "cert_R", "cert_unknown"],
}
MECHANISM = {
    "budget": ("bigger budgets buy stars, effects, wide releases and marketing", "the target divides by budget, so budget sits on both sides; studio, genre and era confound it"),
    "release timing": ("holiday and summer weeks have more moviegoers", "studios put their strongest films on the best dates (selection)"),
    "competition": ("films opening together split the audience", "big films avoid each other's dates, so heavy competition can mean confident studios"),
    "director track record": ("audiences and studios follow proven directors", "proven directors get bigger budgets and better material; only directors with earlier films have a record"),
    "cast track record": ("stars draw audiences", "stars choose and are chosen for promising projects; billing order varies"),
    "source / author": ("known books and authors bring an audience", "adaptations are chosen for being popular already"),
    "production companies": ("large studios distribute widely and market heavily", "company lists mix producers, financiers and distributors; distributor itself is not in the data"),
    "genre and film type": ("some genres travel and some are cheap to make", "genre goes with budget, rating and era"),
    "franchise": ("sequels inherit an audience", "only successful films get sequels (survivorship)"),
    "trailers": ("earlier and more trailers = more marketing", "trailer counts come from today's TMDB/YouTube lists; removed videos are missing"),
    "producer track record (new)": ("established producers attract talent and financing", "producers' earlier films are counted from today's credit lists"),
    "writer track record (new)": ("proven writers deliver stronger scripts", "writers of earlier hits get bigger projects"),
    "US rating (new)": ("PG/PG-13 reach families and teens; R limits the audience", "rating goes with genre and budget; ratings come from today's TMDB data"),
}


def add_new_features(c, rows):
    """Producer / writer track records (earlier films only) and the US rating, added to each row in place."""
    ret = {fid: math.log(g / b) for fid, b, g in c.execute("select tmdb_id, budget, gross from money") if b and g and b >= 1e5 and g > 0}
    films_date = {}
    for fid, rd, usrd in c.execute("select tmdb_id, release_date, us_release_date from films"):
        d = P._date(usrd) or P._date(rd)
        if d:
            films_date[fid] = d
    hist = {"prod": {}, "wri": {}}
    for fid, pid, job, dep in c.execute("select tmdb_id, person_id, job, department from credits where kind = 'crew' "
                                        "and (job in ('Producer') or department = 'Writing')"):
        d = films_date.get(fid)
        if d is None:
            continue
        k = "prod" if job == "Producer" else "wri"
        hist[k].setdefault(pid, []).append((d, fid))
    for k in hist:
        for pid in hist[k]:
            hist[k][pid].sort()
    people = {"prod": {}, "wri": {}}
    for fid, pid, job, dep in c.execute("select tmdb_id, person_id, job, department from credits where kind = 'crew' "
                                        "and (job in ('Producer') or department = 'Writing')"):
        k = "prod" if job == "Producer" else "wri"
        people[k].setdefault(fid, set()).add(pid)
    certs = {}
    for fid, cert, rtype in c.execute("select tmdb_id, certification, release_type from certifications where region = 'US' "
                                      "order by release_type = 3 desc, release_type, rowid"):
        cert = (cert or "").strip().upper()
        if fid not in certs and cert in ("G", "PG", "PG-13", "R", "NC-17"):
            certs[fid] = cert
    for r in rows:
        fid = r["tmdb_id"]
        d = films_date.get(fid)
        for k in ("prod", "wri"):
            n = n5 = 0
            rets = []
            for pid in people[k].get(fid, ()):
                h = hist[k].get(pid, [])
                i = bisect.bisect_left(h, (d, -1)) if d else 0
                for dd, ofid in h[:i]:
                    if ofid == fid:
                        continue
                    n += 1
                    if d and (d - dd).days <= 5 * 365:
                        n5 += 1
                    if ofid in ret:
                        rets.append(ret[ofid])
            r[f"{k}_prev_n"] = float(n)
            r[f"{k}_prev_5y_n"] = float(n5)
            r[f"{k}_prev_ret"] = float(np.mean(rets)) if rets else np.nan
        cert = certs.get(fid)
        for name, val in (("cert_G", "G"), ("cert_PG", "PG"), ("cert_PG13", "PG-13"), ("cert_R", "R")):
            r[name] = 1.0 if cert == val else 0.0
        r["cert_unknown"] = 1.0 if cert is None else 0.0


def fit_avg(new, tr, cols, sets, seeds):
    ytr = np.array([r["actual"] for r in tr])
    out = [np.zeros(len(s)) for s in sets]
    for sd in seeds:
        m = new()
        m.set_params(random_state=sd)
        m.fit(P.matrix(tr, cols), ytr)
        for i, s in enumerate(sets):
            out[i] += m.predict(P.matrix(s, cols)) if len(s) else 0
    return [o / len(seeds) for o in out]


def boot(err_a, err_b, n, seed=0):
    """MAE(a) - MAE(b), 95% CI, two-sided bootstrap p-value (share of resamples on the other side of 0, doubled)."""
    rng = np.random.default_rng(seed)
    d = err_a - err_b
    bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)])
    p = 2 * min((bs <= 0).mean(), (bs >= 0).mean())
    return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)), float(min(1.0, max(p, 1.0 / n)))


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj, run = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = run
    return adj


def spearman(x, y):
    ok = ~np.isnan(x)
    if ok.sum() < 30 or np.nanstd(x[ok]) == 0:
        return float("nan"), int(ok.sum())
    return P.spearman(x[ok], y[ok]), int(ok.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    seeds = SEEDS[:a.seeds]
    t0 = time.time()
    c = P.conn(readonly=True)
    rows = P.build_table(c)
    add_new_features(c, rows)
    uni = [r for r in rows if r["actual"] is not None and r["year"] <= P.LATE[1]]
    tr = [r for r in uni if r["year"] <= P.TRAIN_TO]
    te = [r for r in uni if P.TEST[0] <= r["year"] <= P.TEST[1]]
    la = [r for r in uni if P.LATE[0] <= r["year"] <= P.LATE[1]]
    usable = lambda cols: [k for k in cols if any(not (isinstance(r.get(k), float) and math.isnan(r[k])) for r in uni)]
    base = usable(P.BEFORE + P.TRAILER)
    yte = np.array([r["actual"] for r in te])
    yla = np.array([r["actual"] for r in la])
    kind, new = P.make_model()
    L = [f"Box-office factor study (v8.1 slice 4.4), {time.strftime('%Y-%m-%d %H:%M')}, model {kind}, {len(seeds)} seeds averaged, "
         f"{a.boot} bootstrap resamples",
         f"target log(worldwide gross / budget); train <= {P.TRAIN_TO}: {len(tr):,} | test {P.TEST[0]}-{P.TEST[1]}: {len(te):,} "
         f"(NOT blind) | late {P.LATE[0]}-{P.LATE[1]}: {len(la):,}", ""]
    res = {"model": kind, "seeds": list(seeds), "n_train": len(tr), "n_test": len(te), "n_late": len(la), "groups": {}}
    p_full = fit_avg(new, tr, base, [te, la], seeds)
    e_full = (np.abs(p_full[0] - yte), np.abs(p_full[1] - yla))
    res["pf4_seed_avg_mae"] = {"test": round(float(e_full[0].mean()), 4), "late": round(float(e_full[1].mean()), 4)}
    L.append(f"pf4 before + trailers ({len(base)} inputs), {len(seeds)}-seed average: test MAE {e_full[0].mean():.3f} | late {e_full[1].mean():.3f}"
             f"   (pf4's single-seed 1.196 on this copy, 1.200 on 6 Nov)")
    rows_out = []
    for direction, groups in (("drop", DROP_GROUPS), ("add", ADD_GROUPS)):
        for g, cols in groups.items():
            cols = [k for k in cols if k in usable(cols)]
            if not cols:
                rows_out.append((direction, g, None))
                continue
            arm = [k for k in base if k not in cols] if direction == "drop" else base + [k for k in cols if k not in base]
            p = fit_avg(new, tr, arm, [te, la], seeds)
            e = (np.abs(p[0] - yte), np.abs(p[1] - yla))
            # positive "gain" = the group helps: for drop, MAE(without) - MAE(full); for add, MAE(full) - MAE(with)
            dt = boot(e[0], e_full[0], a.boot) if direction == "drop" else boot(e_full[0], e[0], a.boot)
            dl = boot(e[1], e_full[1], a.boot) if direction == "drop" else boot(e_full[1], e[1], a.boot)
            assoc = []
            ytr = np.array([r["actual"] for r in tr])
            for k in cols:
                x = np.array([np.nan if r.get(k) is None else float(r[k]) for r in tr])
                rho, n = spearman(x, ytr)
                assoc.append((k, rho, n))
            rows_out.append((direction, g, {"cols": cols, "test": dt, "late": dl, "assoc": assoc,
                                            "coverage_test": [int(sum(1 for r in te if not (isinstance(r.get(k), float) and math.isnan(r[k])))) for k in cols]}))
    for direction in ("drop", "add"):
        got = [(g, d) for dd, g, d in rows_out if dd == direction and d]
        adj = holm([d["test"][3] for _g, d in got])
        L += ["", ("DROP each existing group from pf4 (gain = how much worse the test MAE gets without it)" if direction == "drop"
                   else "ADD a new group to pf4 (gain = how much the test MAE improves with it)"),
              f"  {'group':30s} {'inputs':>6s} {'gain test [95% CI]':>28s} {'p Holm':>7s} {'gain late [95% CI]':>28s}  verdict"]
        for (g, d), pa in zip(got, adj):
            gt, gl = d["test"], d["late"]
            ok = pa < 0.05 and gt[0] > 0
            verdict = "carries value" if ok else ("hurts (CI below 0)" if pa < 0.05 and gt[0] < 0 else "no clear value")
            d["p_holm"], d["verdict"] = pa, verdict
            res["groups"][g] = {"direction": direction, "inputs": d["cols"], "gain_test": gt[:3], "p_test": gt[3], "p_holm": pa,
                                "gain_late": gl[:3], "verdict": verdict, "coverage_test": d["coverage_test"],
                                "assoc_train": [(k, None if math.isnan(r) else round(r, 3), n) for k, r, n in d["assoc"]]}
            L.append(f"  {g:30s} {len(d['cols']):6d} {gt[0]:+8.3f} [{gt[1]:+.3f}, {gt[2]:+.3f}] {pa:7.3f} "
                     f"{gl[0]:+8.3f} [{gl[1]:+.3f}, {gl[2]:+.3f}]  {verdict}")
        for dd, g, d in rows_out:
            if dd == direction and d is None:
                L.append(f"  {g:30s} no data on this server - not tested")
    L += ["", "Unadjusted association with the target on the training years (Spearman rho, films with a value):"]
    for dd, g, d in rows_out:
        if d:
            L.append(f"  {g}: " + "; ".join(f"{k} {'n/a' if math.isnan(r) else f'{r:+.2f}'} (n {n:,})" for k, r, n in d["assoc"]))
    # error by segment for the pf4 average model (test years)
    L += ["", f"pf4 ({len(seeds)}-seed average) test error by segment (2016-2019):"]
    seg = {}
    bud = np.array([r["log_budget_real"] for r in te])
    q = np.quantile(bud, [1 / 3, 2 / 3])
    for i, r in enumerate(te):
        keys = ["budget " + ("low" if bud[i] <= q[0] else "mid" if bud[i] <= q[1] else "high"),
                "franchise" if r.get("sequel") or (r.get("franchise_prev_n") or 0) > 0 else "not franchise"]
        keys += [f"genre {g}" for g in P.GENRES if r.get(f"g_{g}")]
        for k in keys:
            seg.setdefault(k, []).append(e_full[0][i])
    for k in sorted(seg, key=lambda s: (not s.startswith("budget"), not s.startswith("franchise") and not s.startswith("not"), s)):
        if len(seg[k]) >= 30:
            L.append(f"  {k:26s} n {len(seg[k]):5,d}  MAE {np.mean(seg[k]):.3f}  (x{math.exp(np.mean(seg[k])):.2f} typical miss)")
    res["segments"] = {k: {"n": len(v), "mae": round(float(np.mean(v)), 4)} for k, v in seg.items()}
    L.append(f"\n{time.time() - t0:.0f} s")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    open(os.path.join(HERE, "results", "v81_factor_study.txt"), "w").write("\n".join(L) + "\n")
    json.dump(res, open(os.path.join(HERE, "results", "v81_factor_study.json"), "w"), indent=1, default=float)
    write_report(res, L)
    write_provenance(base)
    print("\n".join(L))


def write_report(res, L):
    os.makedirs(OUT_DIR, exist_ok=True)
    out = ["# Box-office correlation report (v8.1 slice 4.4)", "",
           "Generated by `tools/v81_factor_study.py` from `results/v81_factor_study.json`. Numbers come only from that run.",
           "Target: log(worldwide gross / production budget). This is a ratio of two reported figures, NOT profit or ROI "
           "(marketing, theatre revenue splits and other costs are unknown).",
           "Associations below are not causes. 2016-2019 has been used by pf1-pf5 (not blind); 2020-2023 is the less-seen check.", "",
           "| Group | Test | Mechanism (plausible) | Main confounders | Gain 2016-19 [95% CI] | Holm p | Gain 2020-23 [95% CI] | Verdict |",
           "|---|---|---|---|---|---|---|---|"]
    for g, d in res["groups"].items():
        mech, conf = MECHANISM.get(g, ("", ""))
        gt, gl = d["gain_test"], d["gain_late"]
        out.append(f"| {g} | {d['direction']} | {mech} | {conf} | {gt[0]:+.3f} [{gt[1]:+.3f}, {gt[2]:+.3f}] | {d['p_holm']:.3f} | "
                   f"{gl[0]:+.3f} [{gl[1]:+.3f}, {gl[2]:+.3f}] | {d['verdict']} |")
    out += ["", "Gain is in MAE of log(gross/budget): +0.010 means the typical miss is about 1% smaller with the group.",
            "", "## Full run output", "", "```", *L, "```", "",
            "## Limits", "- Distributor, screens, opening weekend, school-holiday calendars and international release dates are not in the data.",
            "- Domestic / international splits do not exist in the data (audit F1); this study is worldwide only.",
            "- Track-record features use earlier films' grosses and ratings as they are recorded today.",
            "- Interactions (holiday x genre, director x genre) are left to the tree model; no separate interaction test was run."]
    open(os.path.join(OUT_DIR, "correlation_report.md"), "w").write("\n".join(out) + "\n")


def write_provenance(base):
    import csv
    src = {
        "log_budget_real": ("money + money_real", "https://www.themoviedb.org / Wikipedia / Wikidata", "TMDB API terms; CC BY-SA; CC0", "budget reported around release", "ok (budget is set before release; later corrections possible)"),
        "year": ("films", "https://www.themoviedb.org", "TMDB API terms", "release date", "ok"),
        "month": ("films", "https://www.themoviedb.org", "TMDB API terms", "release date", "ok"),
        "runtime": ("films", "https://www.themoviedb.org", "TMDB API terms", "final cut, known at release", "ok"),
        "trailer_n_pre": ("film_videos", "https://www.themoviedb.org", "TMDB API terms", "videos published before release", "partial (removed videos missing; counts from today's list)"),
        "trailer_lead_days": ("film_videos", "https://www.themoviedb.org", "TMDB API terms", "videos published before release", "partial"),
    }
    rows = []
    groups = {k: g for g, cols in {**DROP_GROUPS, **ADD_GROUPS}.items() for k in cols}
    for k in base + [k for cols in ADD_GROUPS.values() for k in cols]:
        s = src.get(k)
        if s is None:
            if k.startswith(("dir_", "cast_", "prod_", "wri_", "author_", "franchise_", "comp_")):
                s = ("credits + money + films", "https://www.themoviedb.org", "TMDB API terms; money sources as above",
                     "earlier films (release date before this film's)", "ok by construction; earlier films' values are today's")
            elif k.startswith("cert_"):
                s = ("certifications", "https://www.themoviedb.org", "TMDB API terms", "rating assigned before release",
                     "plausible (re-ratings and missing ratings possible)")
            elif k.startswith("g_") or k in ("n_genres", "lang_en", "us_made"):
                s = ("films", "https://www.themoviedb.org", "TMDB API terms", "set before release", "ok (genre lists edited later)")
            else:
                s = ("films + money (release calendar)", "https://www.themoviedb.org", "TMDB API terms",
                     "other films' release dates and budgets", "ok for dates; budgets of competitors as recorded today")
        rows.append({"feature_name": k, "feature_group": groups.get(k, "other (year trend)"), "source_id": s[0],
                     "source_url": s[1], "source_license": s[2], "media_id": "", "forecast_horizon": "release date (pf4)",
                     "forecast_as_of": "release date", "source_published_at": "", "source_fetched_at": "facts.db copy of 12 Nov 2026",
                     "feature_known_as_of": s[3], "missing_reason": "NaN when no earlier film / no data",
                     "leakage_check_status": s[4], "notes": ""})
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "feature_provenance.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
