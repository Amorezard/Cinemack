#!/usr/bin/env python3
"""v8.1 slice 4.6 (15 Nov 2026): the forecasts the hidden endpoint serves, and one pre-registered candidate.

1. CANDIDATE (written before the run; the factor study of 13 Nov said producer track record and the US rating
   carry value on 2016-2019):
     v1        v81-pf4bt-cqr-1 : pf4 "before + trailers" (the model of slice 4.5, unchanged)
     v2        v81-pf4bt-pr-cqr-1 : v1 + producer track record (prod_prev_n, prod_prev_ret, prod_prev_5y_n; earlier
               films only) at d90 and d7, + the US rating one-hot at d7 ONLY (a rating is usually assigned in the
               last weeks; at 90 days it often is not, and the data has no rating date - so it is not used at d90)
   Rule, per horizon (d90, d7): v2 replaces v1 ONLY IF all three hold
     a. test 2016-2019 MAE is lower and the paired-bootstrap 95% CI of the difference (2,000 resamples) excludes 0
     b. v2's ranges still pass the 4.5 rule on test (80% coverage 75-85% and 50% coverage 45-55%)
     c. late 2020-2023 MAE is not worse (the CI of the difference does not lie wholly above 0)
   Otherwise v1 is kept for that horizon. 2016-2019 is NOT blind (pf1-pf5, the factor study and slice 4.5 used
   it), so a pass here is weaker evidence than it looks; the report says so.
2. STORE: for every film released 2016 or later that has a budget and a release date, the chosen model's d90 and
   d7 forecast in the kit's prediction_response v1 shape, written to a NEW database (FORECAST_DB, default
   logs/forecast.db in the worktree). Films up to 2015 are the training data and get no forecast. For a released
   film the forecast is a back-test: what the model would have said that many days before release. Each response
   is checked by tools/v81_forecast.validate_response; one that fails is not stored.
   Films from 2020 on carry a warning with the measured coverage of their ranges (they were right less often
   than stated in slice 4.5).
Read-only on facts.db. Nothing here is shown anywhere: web/forecast_api.py serves the store only while
FORECAST_PANEL=1 (off by default).
    python tools/v81_forecast_store.py [--db logs/forecast.db]
"""
import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import boxoffice_asof as A  # noqa: E402
import performance as P  # noqa: E402
import v81_forecast as V  # noqa: E402

V1, V2 = V.MODEL_VERSION, "v81-pf4bt-pr-cqr-1"
PRODUCER = ["prod_prev_n", "prod_prev_ret", "prod_prev_5y_n"]
RATING = ["cert_G", "cert_PG", "cert_PG13", "cert_R", "cert_unknown"]
EXTRA = {"d90": PRODUCER, "d7": PRODUCER + RATING}          # fixed before the run (see the rule above)
BOOT = 2000
DDL = """create table if not exists forecasts(media_id text not null, horizon text not null, model_version text not null,
    as_of text not null, release_year integer, released integer not null, response_json text not null,
    created_at text not null, primary key (media_id, horizon));
create table if not exists forecast_meta(key text primary key, value text)"""


def boot_diff(err_a, err_b, n=BOOT, seed=0):
    """mean(err_a - err_b) with a 95% CI over films (paired)."""
    d = np.asarray(err_a) - np.asarray(err_b)
    rng = np.random.default_rng(seed)
    bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)])
    return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def decide(m1, m2, d_test, d_late):
    """The rule above. d_* = (mean, lo, hi) of |error v2| - |error v1| (negative = v2 better)."""
    a = d_test[0] < 0 and d_test[2] < 0
    b = 0.75 <= m2["cov80"] <= 0.85 and 0.45 <= m2["cov50"] <= 0.55
    c = not (d_late[1] > 0)
    return {"a_test_mae_better": bool(a), "b_ranges_pass": bool(b), "c_late_not_worse": bool(c), "use_v2": bool(a and b and c)}


def fit_arm(uni, cols, score_sets):
    """Point predictions and 50 / 80% bands for each set in score_sets."""
    tr = [r for r in uni if r["actual"] is not None and r["year"] <= P.TRAIN_TO]
    fit = [r for r in tr if r["year"] <= 2011]
    cal = [r for r in tr if 2012 <= r["year"] <= 2015]
    points = V.point_model(tr, cols, score_sets)
    bands = [dict() for _ in score_sets]
    for lev in V.LEVELS:
        _info, bs = V.cqr(fit, cal, cols, lev, score_sets)
        for i, b in enumerate(bs):
            bands[i][lev] = b
    return points, bands


def late_warning(m):
    return ("Released after 2019: on 2020-2023 films the 80%% range held the real result %.0f%% of the time and the "
            "50%% range %.0f%%, and the model guessed too high on average." % (100 * m["cov80"], 100 * m["cov50"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("FORECAST_DB", os.path.join(HERE, "logs", "forecast.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_forecast_store"))
    a = ap.parse_args(argv)
    live = os.path.realpath(os.path.expanduser("~/movie-buff/rag"))
    if os.path.realpath(a.db).startswith(live + os.sep):
        print("refusing to write a forecast store inside the live folder")
        return 2
    t0 = time.time()
    c = P.conn(readonly=True)
    rows = P.build_table(c)
    import v81_factor_study as FS
    FS.add_new_features(c, rows)
    dates, nominal, bsrc = {}, {}, {}
    for fid, rd, usrd in c.execute("select tmdb_id, release_date, us_release_date from films"):
        d = P._date(usrd) or P._date(rd)
        if d:
            dates[fid] = d
    for fid, b, src in c.execute("select tmdb_id, budget, budget_src from money"):
        if b and b > 0:
            nominal[fid], bsrc[fid] = math.log(b), src
    videos = {}
    for fid, pub in c.execute("select tmdb_id, substr(published_at, 1, 10) from film_videos where site = 'YouTube' "
                              "and type in ('Trailer', 'Teaser') and published_at is not null"):
        videos.setdefault(fid, []).append(P._date(pub))
    for r in rows:
        r["_date"] = dates.get(r["tmdb_id"])
        r["_log_budget_nominal"] = nominal.get(r["tmdb_id"])
    today = datetime.date.today()
    known = [r for r in rows if r["_date"] is not None]
    uni0 = [r for r in known if r["actual"] is not None and r["year"] <= P.LATE[1]]
    new0 = [r for r in known if r["year"] > P.LATE[1] and r["_log_budget_nominal"] is not None]
    usable = lambda cols, uni: [k for k in cols if any(not (isinstance(r.get(k), float) and math.isnan(r[k])) for r in uni)]
    L = [f"Forecast store and candidate (v8.1 slice 4.6), {time.strftime('%Y-%m-%d %H:%M')}",
         f"v1 {V1} | v2 {V2} = v1 + producer track record (d90, d7) + US rating (d7 only)",
         "Rule per horizon: v2 only if test MAE lower with CI excluding 0 AND its ranges pass the 4.5 rule AND late MAE not worse.",
         "2016-2019 is NOT blind.", ""]
    res = {"v1": V1, "v2": V2, "horizons": {}, "stored": {}}
    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    db = sqlite3.connect(a.db)
    db.executescript(DDL)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n_bad = 0
    with db:
        db.execute("delete from forecasts")
    for h in ("d90", "d7"):
        uni = A.trailer_features(uni0 + new0, videos, h)
        lab = [r for r in uni if r["actual"] is not None and r["year"] <= P.LATE[1]]
        te = [r for r in lab if P.TEST[0] <= r["year"] <= P.TEST[1]]
        la = [r for r in lab if P.LATE[0] <= r["year"] <= P.LATE[1]]
        nw = [r for r in uni if r["year"] > P.LATE[1]]
        cols1 = A.check(usable(P.BEFORE + P.TRAILER, lab), h)
        cols2 = cols1 + EXTRA[h]                  # the extra inputs' timing is the stated assumption, not the registry's
        sets = [te, la, nw]
        p1, b1 = fit_arm(lab, cols1, sets)
        p2, b2 = fit_arm(lab, cols2, sets)
        yte, yla = np.array([r["actual"] for r in te]), np.array([r["actual"] for r in la])
        m = {"v1": {"test": V.metrics(yte, p1[0], b1[0]), "late": V.metrics(yla, p1[1], b1[1])},
             "v2": {"test": V.metrics(yte, p2[0], b2[0]), "late": V.metrics(yla, p2[1], b2[1])}}
        d_test = boot_diff(np.abs(p2[0] - yte), np.abs(p1[0] - yte))
        d_late = boot_diff(np.abs(p2[1] - yla), np.abs(p1[1] - yla))
        verdict = decide(m["v1"]["test"], m["v2"]["test"], d_test, d_late)
        use2 = verdict["use_v2"]
        res["horizons"][h] = {"metrics": m, "v2_minus_v1_test_mae": d_test, "v2_minus_v1_late_mae": d_late,
                              "rule": verdict, "chosen": V2 if use2 else V1, "extra_inputs": EXTRA[h]}
        L.append(f"[{h}] test {len(te):,} | late {len(la):,} | 2024+ films with a budget {len(nw):,}")
        for v in ("v1", "v2"):
            for part in ("test", "late"):
                x = m[v][part]
                L.append(f"  {v} {part}: MAE {x['mae_log']:.3f} (x{x['typical_miss_factor']:.2f}) bias {x['bias_log']:+.3f} | "
                         f"50% cov {x['cov50']:.1%} | 80% cov {x['cov80']:.1%} width x{x['width80_factor_median']:.1f}")
        L.append(f"  v2 - v1 MAE: test {d_test[0]:+.4f} [{d_test[1]:+.4f}, {d_test[2]:+.4f}] | late {d_late[0]:+.4f} [{d_late[1]:+.4f}, {d_late[2]:+.4f}]")
        L.append(f"  RULE a test better {verdict['a_test_mae_better']} | b ranges pass {verdict['b_ranges_pass']} | "
                 f"c late not worse {verdict['c_late_not_worse']} -> stored model: {V2 if use2 else V1}")
        pts, bds, ver = (p2, b2, V2) if use2 else (p1, b1, V1)
        m_late = m["v2" if use2 else "v1"]["late"]
        n_h = 0
        with db:
            for si, rs in enumerate(sets):
                for i, r in enumerate(rs):
                    if r["_log_budget_nominal"] is None:
                        continue
                    bi = {lev: (float(bds[si][lev][0][i]), float(bds[si][lev][1][i])) for lev in V.LEVELS}
                    d = V.response(r, h, float(pts[si][i]), bi, bsrc.get(r["tmdb_id"]))
                    d["model_version"] = ver
                    d["prediction_id"] = f"{ver}:{r['tmdb_id']}:{h}"
                    released = r["_date"] <= today
                    if r["year"] >= P.LATE[0]:
                        d["warnings"].append(late_warning(m_late))
                    if released:
                        d["warnings"].append("Back-test: this film is already released; the forecast uses only what was known "
                                             "that many days before its release.")
                    if V.validate_response(d):
                        n_bad += 1
                        continue
                    db.execute("insert or replace into forecasts values (?,?,?,?,?,?,?,?)",
                               (d["media_id"], h, ver, d["as_of"], r["year"], 1 if released else 0, json.dumps(d), now))
                    n_h += 1
        res["stored"][h] = n_h
        L.append(f"  stored {n_h:,} forecasts")
    with db:
        db.execute("insert or replace into forecast_meta values ('built_at', ?)", (now,))
        db.execute("insert or replace into forecast_meta values ('models', ?)",
                   (json.dumps({h: res["horizons"][h]["chosen"] for h in res["horizons"]}),))
    unrel = db.execute("select count(distinct media_id) from forecasts where released = 0").fetchone()[0]
    films = db.execute("select count(distinct media_id) from forecasts").fetchone()[0]
    db.close()
    res.update(invalid_not_stored=n_bad, films=films, unreleased=unrel)
    L += ["", f"store {a.db}: {films:,} films (of them not yet released: {unrel:,}); responses failing the checks and not stored: {n_bad}",
          f"{time.time() - t0:.0f} s"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + ".txt", "w") as fh:
        fh.write("\n".join(L) + "\n")
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
