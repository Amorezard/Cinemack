#!/usr/bin/env python3
"""v8.1 round 57 (20 Nov 2026): two forecast candidates, decided by rules written before the run.

Owner, 20 Nov 10:42: "Ramayana: Rise of a Legend" was forecast at $947M (80% range $262M-$2.19B) on a $260M budget,
and the site showed $147.8M worldwide; "I Play Rocky" showed $1.8M on a $27M budget. Why, and can it be more accurate?
Findings that shaped this run:
  - both figures were RUNNING TOTALS of films still in cinemas (the page now says so); they are not results.
  - the model knows a film's market only as "English or not" and "US-made or not", and learned mostly from Hollywood
    films, where a $260M budget means a global tentpole. Indian releases earn far less in dollars per budget dollar.
  - nothing after release is used, by design, so a film that is already out never gets a sharper forecast.
  - narrowing the ranges by hand would break their promise (8 in 10 / 5 in 10); they can only narrow with information.
  - Wikipedia pageviews before release cannot help this model: the pageview API starts in July 2015 and the model is
    trained on films up to 2015, so almost no training film has them. Not used here (stated, not tested).

CANDIDATE A, "market" (before release, both horizons): the stored model (v2) + where the film comes from:
    country_in    India among the production countries        lang_indic    Hindi, Telugu, Tamil, Malayalam, Kannada,
    country_cn    China among the production countries                      Marathi, Bengali or Punjabi original language
    country_other_only   no US / GB / CA / AU / NZ / IE country at all (a film made entirely outside them)
  RULE A (per horizon, the round-45 rule unchanged): use A only if (a) its 2016-2019 MAE is lower with a 95% CI
  excluding 0, (b) its ranges still hold 75-85% (80%) and 45-55% (50%) on 2016-2019, and (c) its 2020-2023 MAE is not
  worse (CI not wholly above 0). Also REPORTED (not part of the rule): the error on non-US, non-English films.
CANDIDATE B, "update after the first week" (new horizon, opening_weekend_update): the before-release inputs (with A if
  A passed at d7) + what the first reviews and ratings say: TMDB rating (20+ votes), IMDb rating, Rotten Tomatoes %
  and average, Metacritic, CinemaScore. Vote COUNTS stay out (they measure how many people saw it = the outcome).
  Timing assumption, stated: the stored ratings are today's values, used as a stand-in for the values one week after
  release (ratings move little after the first weeks; awards are left out because they come months later).
  RULE B: shown only if (a) its 2016-2019 MAE is lower than the d7 model's with a 95% CI excluding 0, (b) its ranges
  hold 75-85% / 45-55% on 2016-2019, (c) its 2020-2023 MAE is not worse than the d7 model's.
Writes a NEW store (default logs/forecast_v3.db in this folder) with d90 and d7 from the chosen model per horizon and,
if B passed, an opening_weekend_update forecast for every released film with ratings. The live store is never written
here; the round script swaps files only when this tool exits 0. Results: results/v81_forecast_v3.txt / .json.
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
import v81_forecast_store as S  # noqa: E402

VA, VB = "v81-pf4bt-pr-mkt-cqr-1", "v81-upd-reception-cqr-1"
MARKET = ["country_in", "country_cn", "country_other_only", "lang_indic"]
UPDATE = ["rating", "imdb_rating", "rt_pct", "rt_avg", "mc_score", "cinemascore"]
INDIC = {"hi", "te", "ta", "ml", "kn", "mr", "bn", "pa"}
ANGLO = {"US", "GB", "CA", "AU", "NZ", "IE"}
UPDATE_DAYS = 7


def add_market(c, rows):
    info = {}
    for fid, lang, countries in c.execute("select tmdb_id, original_language, countries_json from films"):
        try:
            cs = set(json.loads(countries or "[]"))
        except ValueError:
            cs = set()
        info[fid] = (lang, cs)
    for r in rows:
        lang, cs = info.get(r["tmdb_id"], (None, set()))
        r["country_in"] = 1.0 if "IN" in cs else 0.0
        r["country_cn"] = 1.0 if "CN" in cs else 0.0
        r["country_other_only"] = 1.0 if cs and not (cs & ANGLO) else 0.0
        r["lang_indic"] = 1.0 if lang in INDIC else 0.0


def subgroup_mae(rows, pred):
    idx = [i for i, r in enumerate(rows) if r.get("lang_en") == 0.0 and r.get("us_made") == 0.0]
    if not idx:
        return None
    y = np.array([rows[i]["actual"] for i in idx])
    return {"n": len(idx), "mae_log": float(np.mean(np.abs(np.asarray(pred)[idx] - y)))}


def fmt(x):
    return (f"MAE {x['mae_log']:.3f} (x{x['typical_miss_factor']:.2f}) bias {x['bias_log']:+.3f} | 50% cov {x['cov50']:.1%} | "
            f"80% cov {x['cov80']:.1%} width x{x['width80_factor_median']:.1f}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "logs", "forecast_v3.db"))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_forecast_v3"))
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
    add_market(c, rows)
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
    usable = lambda cols, uni: [k for k in cols if any(not (isinstance(r.get(k), float) and math.isnan(r[k])) for r in uni)]  # noqa: E731
    lab_all = [r for r in uni0 if r["year"] <= P.TRAIN_TO]
    L = [f"Forecast candidates A (market) and B (update after the first week), round 57, {time.strftime('%Y-%m-%d %H:%M')}",
         f"base = the stored model {S.V2}; A = base + {', '.join(MARKET)}; B = d7 inputs (+A if A passed at d7) + {', '.join(UPDATE)}",
         "Rules were written before this run (see the top of tools/v81_forecast_v3.py). 2016-2019 is NOT blind.",
         "training films (<= 2015) by market: India %d | China %d | made outside US/GB/CA/AU/NZ/IE only %d | Indian language %d | all %d"
         % tuple([sum(1 for r in lab_all if r[k] == 1.0) for k in MARKET[:1] + MARKET[1:2] + MARKET[2:3] + MARKET[3:]] + [len(lab_all)]),
         ""]
    res = {"A": VA, "B": VB, "horizons": {}, "stored": {}}
    chosen, store = {}, {}
    for h in ("d90", "d7"):
        uni = A.trailer_features(uni0 + new0, videos, h)
        lab = [r for r in uni if r["actual"] is not None and r["year"] <= P.LATE[1]]
        te = [r for r in lab if P.TEST[0] <= r["year"] <= P.TEST[1]]
        la = [r for r in lab if P.LATE[0] <= r["year"] <= P.LATE[1]]
        nw = [r for r in uni if r["year"] > P.LATE[1]]
        base = A.check(usable(P.BEFORE + P.TRAILER, lab), h) + S.EXTRA[h]
        cand = base + [k for k in MARKET if k not in base]
        sets = [te, la, nw]
        p1, b1 = S.fit_arm(lab, base, sets)
        p2, b2 = S.fit_arm(lab, cand, sets)
        yte, yla = np.array([r["actual"] for r in te]), np.array([r["actual"] for r in la])
        m = {"base": {"test": V.metrics(yte, p1[0], b1[0]), "late": V.metrics(yla, p1[1], b1[1])},
             "A": {"test": V.metrics(yte, p2[0], b2[0]), "late": V.metrics(yla, p2[1], b2[1])}}
        d_test = S.boot_diff(np.abs(p2[0] - yte), np.abs(p1[0] - yte))
        d_late = S.boot_diff(np.abs(p2[1] - yla), np.abs(p1[1] - yla))
        verdict = S.decide(m["base"]["test"], m["A"]["test"], d_test, d_late)
        useA = verdict["use_v2"]
        sub = {"base_test": subgroup_mae(te, p1[0]), "A_test": subgroup_mae(te, p2[0]),
               "base_late": subgroup_mae(la, p1[1]), "A_late": subgroup_mae(la, p2[1])}
        res["horizons"][h] = {"metrics": m, "A_minus_base_test_mae": d_test, "A_minus_base_late_mae": d_late,
                              "rule": verdict, "chosen": VA if useA else S.V2, "non_us_non_english": sub}
        L.append(f"[{h}] test {len(te):,} | late {len(la):,} | 2024+ with a budget {len(nw):,}")
        for v in ("base", "A"):
            for part in ("test", "late"):
                L.append(f"  {v:4} {part}: " + fmt(m[v][part]))
        L.append(f"  A - base MAE: test {d_test[0]:+.4f} [{d_test[1]:+.4f}, {d_test[2]:+.4f}] | late {d_late[0]:+.4f} "
                 f"[{d_late[1]:+.4f}, {d_late[2]:+.4f}]")
        L.append("  non-US non-English films (reported only): " + " | ".join(
            f"{k} n {v['n']} MAE {v['mae_log']:.3f}" for k, v in sub.items() if v))
        L.append(f"  RULE A: test better {verdict['a_test_mae_better']} | ranges pass {verdict['b_ranges_pass']} | "
                 f"late not worse {verdict['c_late_not_worse']} -> {h} uses {VA if useA else S.V2}")
        chosen[h] = (useA, cand if useA else base)
        store[h] = (sets, (p2, b2) if useA else (p1, b1), VA if useA else S.V2, m["A" if useA else "base"]["late"])
        if h == "d7":
            d7 = {"te": te, "la": la, "nw": nw, "pred": (p2 if useA else p1), "lab": lab}

    # ---- candidate B: after the first week
    useA7, cols7 = chosen["d7"]
    colsB = cols7 + [k for k in UPDATE if k not in cols7]
    lab = d7["lab"]
    te, la = d7["te"], d7["la"]
    nwB = [r for r in d7["nw"] if r["_date"] and (today - r["_date"]).days >= UPDATE_DAYS
           and any(not (isinstance(r.get(k), float) and math.isnan(r[k])) and r.get(k) is not None for k in UPDATE)]
    pB, bB = S.fit_arm(lab, colsB, [te, la] + ([nwB] if nwB else []))   # an empty set cannot be scored
    yte, yla = np.array([r["actual"] for r in te]), np.array([r["actual"] for r in la])
    mB = {"test": V.metrics(yte, pB[0], bB[0]), "late": V.metrics(yla, pB[1], bB[1])}
    m7 = {"test": V.metrics(yte, d7["pred"][0], store["d7"][1][1][0]), "late": V.metrics(yla, d7["pred"][1], store["d7"][1][1][1])}
    dB_test = S.boot_diff(np.abs(pB[0] - yte), np.abs(d7["pred"][0] - yte))
    dB_late = S.boot_diff(np.abs(pB[1] - yla), np.abs(d7["pred"][1] - yla))
    vB = S.decide(m7["test"], mB["test"], dB_test, dB_late)
    useB = vB["use_v2"]
    res["update"] = {"metrics": mB, "d7_metrics": m7, "B_minus_d7_test_mae": dB_test, "B_minus_d7_late_mae": dB_late,
                     "rule": vB, "shown": useB, "films_with_update": len(nwB) if useB else 0}
    L += ["", f"[update after the first week] inputs: d7 ({'with' if useA7 else 'without'} market) + {', '.join(UPDATE)}",
          "  d7   test: " + fmt(m7["test"]), "  B    test: " + fmt(mB["test"]),
          "  d7   late: " + fmt(m7["late"]), "  B    late: " + fmt(mB["late"]),
          f"  B - d7 MAE: test {dB_test[0]:+.4f} [{dB_test[1]:+.4f}, {dB_test[2]:+.4f}] | late {dB_late[0]:+.4f} [{dB_late[1]:+.4f}, {dB_late[2]:+.4f}]",
          f"  RULE B: test better {vB['a_test_mae_better']} | ranges pass {vB['b_ranges_pass']} | late not worse "
          f"{vB['c_late_not_worse']} -> {'SHOWN' if useB else 'not shown'} ({len(nwB):,} released 2024+ films have ratings)"]

    # ---- the new store
    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    if os.path.exists(a.db):
        os.remove(a.db)
    db = sqlite3.connect(a.db)
    db.executescript(S.DDL)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n_bad = 0
    with db:
        for h in ("d90", "d7"):
            sets, (pts, bds), ver, m_late = store[h]
            n_h = 0
            for si, rs in enumerate(sets):
                for i, r in enumerate(rs):
                    if r["_log_budget_nominal"] is None:
                        continue
                    bi = {lev: (float(bds[si][lev][0][i]), float(bds[si][lev][1][i])) for lev in V.LEVELS}
                    d = V.response(r, h, float(pts[si][i]), bi, bsrc.get(r["tmdb_id"]))
                    d["model_version"], d["prediction_id"] = ver, f"{ver}:{r['tmdb_id']}:{h}"
                    released = r["_date"] <= today
                    if r["year"] >= P.LATE[0]:
                        d["warnings"].append(S.late_warning(m_late))
                    if ver == VA:
                        d["source_notes"].append("Includes where the film was made and its original language (round 57).")
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
        if useB and nwB:
            n_u = 0
            for i, r in enumerate(nwB):
                if r["_log_budget_nominal"] is None:
                    continue
                bi = {lev: (float(bB[2][lev][0][i]), float(bB[2][lev][1][i])) for lev in V.LEVELS}
                d = V.response(r, "d7", float(pB[2][i]), bi, bsrc.get(r["tmdb_id"]))
                d["horizon"] = "opening_weekend_update"
                d["as_of"] = datetime.datetime.combine(r["_date"] + datetime.timedelta(days=UPDATE_DAYS),
                                                       datetime.time(12, 0)).isoformat() + "Z"
                d["model_version"], d["prediction_id"] = VB, f"{VB}:{r['tmdb_id']}:update"
                d["source_notes"].append("Updated after release with the first reviews and ratings (TMDB, IMDb, Rotten "
                                         "Tomatoes, Metacritic, CinemaScore); today's ratings stand in for week-one ratings.")
                d["warnings"].append(S.late_warning(mB["late"]).replace("Released after 2019", "Update after release"))
                if V.validate_response(d):
                    n_bad += 1
                    continue
                db.execute("insert or replace into forecasts values (?,?,?,?,?,?,?,?)",
                           (d["media_id"], "update", VB, d["as_of"], r["year"], 1, json.dumps(d), now))
                n_u += 1
            res["stored"]["update"] = n_u
        db.execute("insert or replace into forecast_meta values ('built_at', ?)", (now,))
        db.execute("insert or replace into forecast_meta values ('models', ?)",
                   (json.dumps({"d90": store["d90"][2], "d7": store["d7"][2], "update": VB if useB else None}),))
    films = db.execute("select count(distinct media_id) from forecasts").fetchone()[0]
    db.close()
    res.update(invalid_not_stored=n_bad, films=films)
    L += ["", f"store {a.db}: {films:,} films | stored {res['stored']} | failing the checks and not stored: {n_bad}",
          f"{time.time() - t0:.0f} s"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + ".txt", "w") as fh:
        fh.write("\n".join(L) + "\n")
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1, default=float)
    print("\n".join(L))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
