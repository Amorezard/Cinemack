#!/usr/bin/env python3
"""v8.1 slice 4.5 (13 Nov 2026): worldwide box-office forecast with 50% and 80% ranges, per horizon.

Decided with the user 13 Nov 12:24: the forecast is WORLDWIDE only; domestic and international are returned as
null with a warning (no territory figures in the data, audit F1).

Model (same family as pf4, nothing tuned on the test years):
  target     log(worldwide gross / production budget)  -> gross = budget x exp(prediction); multiple = gross / budget
             (a ratio of two reported figures; NOT profit or ROI)
  inputs     pf4 "before + trailers", checked by boxoffice_asof.check for the horizon; trailers recomputed with only
             videos published before the cutoff (boxoffice_asof.trailer_features)
  point      mean of 5 LightGBM fits (pf4 settings, seeds 7 11 13 17 19) trained on <= 2015
  ranges     conformalized quantile regression (pf5 v3 method): quantile models on <= 2011, widening from the
             conformity scores of 2012-2015, applied to 2016-2019 (test, NOT blind: pf1-pf5 used it) and 2020-2023 (late)
             for nominal 50% (quantiles .25/.75) and 80% (.10/.90)
Rules written before the first run (IMPLEMENTATION_PLAN_LOCKED.md, 12 Nov): on 2016-2019, 80% coverage inside 75-85%
and 50% coverage inside 45-55%, per horizon. Median widths are reported next to coverage (a wide range is easy).

Writes results/v81_forecast.txt, planning/cinemack-v8.1/box_office/forecast_eval.json, box_office_model_card.md and
results/v81_forecast_samples.json (20 example responses in the kit's prediction_response v1 shape, checked by
validate_response). Read-only on facts.db.
"""
import datetime
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import boxoffice_asof as A  # noqa: E402
import performance as P  # noqa: E402

MODEL_VERSION = "v81-pf4bt-cqr-1"
SEEDS = (7, 11, 13, 17, 19)
LEVELS = (0.5, 0.8)
OUT_DIR = os.path.join(HERE, "planning", "cinemack-v8.1", "box_office")
WARN_TERRITORY = "Domestic and international grosses are not estimated: the data has worldwide figures only."
WARN_RATIO = "Gross / budget is a ratio of two reported figures, not profit: marketing and theatre shares are unknown."


def _lgb(objective, alpha=None, seed=7):
    import lightgbm as lgb
    kw = dict(n_estimators=500, learning_rate=0.03, num_leaves=15, min_child_samples=30, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1)
    if objective == "quantile":
        return lgb.LGBMRegressor(objective="quantile", alpha=alpha, **kw)
    return lgb.LGBMRegressor(objective="huber", alpha=1.0, **kw)


def point_model(tr, cols, sets):
    y = np.array([r["actual"] for r in tr])
    out = [np.zeros(len(s)) for s in sets]
    for sd in SEEDS:
        m = _lgb("huber", seed=sd)
        m.fit(P.matrix(tr, cols), y)
        for i, s in enumerate(sets):
            if len(s):
                out[i] += m.predict(P.matrix(s, cols))
    return [o / len(SEEDS) for o in out]


def cqr(fit, cal, cols, level, sets):
    a = (1 - level) / 2
    ms = {}
    for q in (a, 1 - a):
        m = _lgb("quantile", alpha=q)
        m.fit(P.matrix(fit, cols), np.array([r["actual"] for r in fit]))
        ms[q] = m

    def band(rs):
        lo, hi = ms[a].predict(P.matrix(rs, cols)), ms[1 - a].predict(P.matrix(rs, cols))
        return np.minimum(lo, hi), np.maximum(lo, hi)
    lo, hi = band(cal)
    y = np.array([r["actual"] for r in cal])
    score = np.maximum(lo - y, y - hi)
    n = len(score)
    qhat = float(np.quantile(score, min(1.0, math.ceil((n + 1) * level) / n), method="higher"))
    res = {"qhat": qhat, "cal_raw_coverage": float(((y >= lo) & (y <= hi)).mean()), "n_cal": n}
    bands = []
    for rs in sets:
        if not rs:
            bands.append((np.array([]), np.array([])))
            continue
        lo, hi = band(rs)
        bands.append((lo - qhat, hi + qhat))
    return res, bands


def metrics(y, p, bands):
    e = p - y
    out = {"n": int(len(y)), "mae_log": float(np.abs(e).mean()), "rmse_log": float(np.sqrt((e ** 2).mean())),
           "median_ae_log": float(np.median(np.abs(e))), "typical_miss_factor": float(math.exp(np.abs(e).mean())),
           "bias_log": float(e.mean())}
    for lev, (lo, hi) in bands.items():
        inside = (y >= lo) & (y <= hi)
        out[f"cov{int(lev * 100)}"] = float(inside.mean())
        out[f"below{int(lev * 100)}"] = float((y < lo).mean())
        out[f"above{int(lev * 100)}"] = float((y > hi).mean())
        out[f"width{int(lev * 100)}_factor_median"] = float(math.exp(np.median(hi - lo)))
        out[f"point_outside{int(lev * 100)}"] = int(((p < lo) | (p > hi)).sum())
    return out


def response(r, horizon, pred, bands_i, budget_src):
    """One prediction in the kit's prediction_response v1 shape (worldwide only)."""
    b = float(math.exp(r["_log_budget_nominal"])) if r.get("_log_budget_nominal") is not None else None
    asof = datetime.datetime.combine(A.cutoff(r["_date"], horizon), datetime.time(12, 0)).isoformat() + "Z"
    ints = sorted(bands_i.items())
    ww = {"point": b * math.exp(pred) if b else None,
          "intervals": [{"nominal_coverage": lev, "lower": b * math.exp(lo) if b else None, "upper": b * math.exp(hi) if b else None}
                        for lev, (lo, hi) in ints]}
    mult = {"point": math.exp(pred), "intervals": [{"nominal_coverage": lev, "lower": math.exp(lo), "upper": math.exp(hi)}
                                                    for lev, (lo, hi) in ints]}
    empty = {"point": None, "intervals": []}
    return {"schema_version": 1, "prediction_id": f"{MODEL_VERSION}:{r['tmdb_id']}:{horizon}", "media_id": f"movie:{r['tmdb_id']}",
            "model_version": MODEL_VERSION, "status": "experimental", "horizon": horizon, "as_of": asof, "currency": "USD",
            "budget": {"amount": b, "known_as_of": None, "source_id": budget_src},
            "domestic_gross": empty, "international_gross": empty, "worldwide_gross": ww, "gross_to_budget_multiple": mult,
            "source_notes": ["Budget and worldwide gross: TMDB / Wikipedia / Wikidata as stored in facts.db (money table).",
                             "Trained on films released up to 2015; ranges calibrated on 2012-2015 (conformalized quantile regression)."],
            "warnings": [WARN_TERRITORY, WARN_RATIO, "Experimental research output; not validated for display."]}


def validate_response(d):
    """The kit schema's rules, checked without jsonschema (not installed on the server). Returns a list of problems."""
    p = []
    req = ["schema_version", "prediction_id", "media_id", "model_version", "status", "horizon", "as_of", "currency", "budget",
           "domestic_gross", "international_gross", "worldwide_gross", "gross_to_budget_multiple", "source_notes", "warnings"]
    if set(d) != set(req):
        p.append(f"keys {sorted(set(d) ^ set(req))}")
    if d.get("schema_version") != 1 or d.get("status") not in ("experimental", "validated", "disabled") \
            or d.get("horizon") not in ("d90", "d7", "opening_weekend_update"):
        p.append("enum field")
    if not (isinstance(d.get("currency"), str) and len(d["currency"]) == 3 and d["currency"].isupper()):
        p.append("currency")
    for k in ("domestic_gross", "international_gross", "worldwide_gross", "gross_to_budget_multiple"):
        v = d.get(k) or {}
        if set(v) != {"point", "intervals"} or len(v.get("intervals", [])) > 5:
            p.append(f"{k} shape")
            continue
        if v["point"] is not None and (not isinstance(v["point"], (int, float)) or v["point"] < 0 or math.isnan(v["point"])):
            p.append(f"{k} point")
        for it in v["intervals"]:
            if set(it) != {"nominal_coverage", "lower", "upper"} or not 0 < it["nominal_coverage"] < 1:
                p.append(f"{k} interval shape")
            elif it["lower"] is not None and it["upper"] is not None and not (0 <= it["lower"] <= it["upper"]):
                p.append(f"{k} interval order")
    for k in ("source_notes", "warnings"):
        if any(not isinstance(s, str) or len(s) > 500 for s in d.get(k, [])):
            p.append(k)
    text = json.dumps(d).lower()
    if "profit" in text.replace("not profit", "") or " roi" in text.replace("or roi", ""):
        p.append("profit / ROI wording")
    return p


def main():
    t0 = time.time()
    c = P.conn(readonly=True)
    rows = P.build_table(c)
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
    uni0 = [r for r in rows if r["actual"] is not None and r["year"] <= P.LATE[1] and r["_date"] is not None]
    usable = lambda cols, uni: [k for k in cols if any(not (isinstance(r.get(k), float) and math.isnan(r[k])) for r in uni)]
    L = [f"Worldwide box-office forecast (v8.1 slice 4.5), {time.strftime('%Y-%m-%d %H:%M')}, model {MODEL_VERSION}",
         "target log(worldwide gross / budget); point: 5-seed LightGBM on <= 2015; ranges: CQR fit <= 2011, calibrated 2012-2015",
         "2016-2019 = test (NOT blind); 2020-2023 = late (less seen). Rule: test 80% coverage 75-85% AND 50% coverage 45-55%.", ""]
    ev = {"model_version": MODEL_VERSION, "target": "log(worldwide gross / production budget)", "territories": "worldwide only",
          "horizons": {}, "rules": {"cov80": [0.75, 0.85], "cov50": [0.45, 0.55]}}
    samples = []
    for h in ("d90", "d7", "d0"):
        uni = A.trailer_features(uni0, videos, h)
        cols = A.check(usable(P.BEFORE + P.TRAILER, uni), h)
        tr = [r for r in uni if r["year"] <= P.TRAIN_TO]
        te = [r for r in uni if P.TEST[0] <= r["year"] <= P.TEST[1]]
        la = [r for r in uni if P.LATE[0] <= r["year"] <= P.LATE[1]]
        fit = [r for r in uni if r["year"] <= 2011]
        cal = [r for r in uni if 2012 <= r["year"] <= 2015]
        p_te, p_la = point_model(tr, cols, [te, la])
        cal_info, bands_te, bands_la = {}, {}, {}
        for lev in LEVELS:
            info, (bt, bl) = cqr(fit, cal, cols, lev, [te, la])
            cal_info[lev], bands_te[lev], bands_la[lev] = info, bt, bl
        yte, yla = np.array([r["actual"] for r in te]), np.array([r["actual"] for r in la])
        m_te, m_la = metrics(yte, p_te, bands_te), metrics(yla, p_la, bands_la)
        ok80 = 0.75 <= m_te["cov80"] <= 0.85
        ok50 = 0.45 <= m_te["cov50"] <= 0.55
        verdict = "PASS" if ok80 and ok50 else "FAIL"
        if h == "d0":
            verdict = "reference (pf4 timing; not a pre-release horizon)"
        ev["horizons"][h] = {"inputs": cols, "n_train": len(tr), "n_fit": len(fit), "n_cal": len(cal), "test": m_te, "late": m_la,
                             "calibration": {str(k): v for k, v in cal_info.items()},
                             "trailer_coverage_test": int(sum(1 for r in te if not math.isnan(r["trailer_n_pre"]) and r["trailer_n_pre"] > 0)),
                             "rule": verdict}
        L.append(f"[{h}] {len(cols)} inputs | train {len(tr):,} | test {len(te):,} | late {len(la):,} | "
                 f"films with a trailer before the cutoff (test): {ev['horizons'][h]['trailer_coverage_test']:,}")
        for name, m in (("test 2016-19", m_te), ("late 2020-23", m_la)):
            L.append(f"  {name}: MAE {m['mae_log']:.3f} (x{m['typical_miss_factor']:.2f}) RMSE {m['rmse_log']:.3f} median {m['median_ae_log']:.3f} "
                     f"bias {m['bias_log']:+.3f} | 50%: cov {m['cov50']:.1%} width x{m['width50_factor_median']:.1f} | "
                     f"80%: cov {m['cov80']:.1%} (below {m['below80']:.1%}, above {m['above80']:.1%}) width x{m['width80_factor_median']:.1f}")
        L.append(f"  calibration (2012-15): raw 50% {cal_info[0.5]['cal_raw_coverage']:.1%} -> widen {cal_info[0.5]['qhat']:+.3f}; "
                 f"raw 80% {cal_info[0.8]['cal_raw_coverage']:.1%} -> widen {cal_info[0.8]['qhat']:+.3f} log units")
        L.append(f"  RULE {verdict}")
        if h != "d0":
            for i in range(0, len(te), max(1, len(te) // 10))[:10]:
                bi = {lev: (bands_te[lev][0][i], bands_te[lev][1][i]) for lev in LEVELS}
                samples.append(response(te[i], h, float(p_te[i]), bi, bsrc.get(te[i]["tmdb_id"])))
    bad = {s["prediction_id"]: validate_response(s) for s in samples}
    bad = {k: v for k, v in bad.items() if v}
    L += ["", f"example responses: {len(samples)}, failing the response checks: {len(bad)} {list(bad.items())[:3]}",
          f"\n{time.time() - t0:.0f} s"]
    ev["responses_checked"], ev["responses_invalid"] = len(samples), len(bad)
    os.makedirs(OUT_DIR, exist_ok=True)
    open(os.path.join(HERE, "results", "v81_forecast.txt"), "w").write("\n".join(L) + "\n")
    json.dump(ev, open(os.path.join(OUT_DIR, "forecast_eval.json"), "w"), indent=1, default=float)
    json.dump(samples, open(os.path.join(HERE, "results", "v81_forecast_samples.json"), "w"), indent=1)
    write_card(ev)
    print("\n".join(L))


def write_card(ev):
    H = ev["horizons"]

    def row(h, part):
        m = H[h][part]
        return (f"| {h} | {part} | {m['n']:,} | {m['mae_log']:.3f} (x{m['typical_miss_factor']:.2f}) | {m['rmse_log']:.3f} | "
                f"{m['median_ae_log']:.3f} | {m['cov50']:.1%} / x{m['width50_factor_median']:.1f} | "
                f"{m['cov80']:.1%} / x{m['width80_factor_median']:.1f} |")
    fs = os.path.join(HERE, "results", "v81_factor_study.json")
    factors = json.load(open(fs)) if os.path.exists(fs) else None
    out = ["# Box-office forecast model card (v8.1)", "",
           "Filled by `tools/v81_forecast.py` from its own run (`forecast_eval.json`); nothing below is typed by hand except the fixed text.", "",
           "## Identity and objective",
           f"- Model version / algorithm: `{MODEL_VERSION}` - LightGBM (Huber loss, pf4 settings), mean of 5 seeds; ranges by conformalized quantile regression.",
           "- Predictive target: worldwide theatrical gross through log(worldwide gross / production budget); gross = budget x exp(prediction); "
           "gross-to-budget multiple = exp(prediction). **Domestic and international: not estimated** (no territory data; decided with the user 13 Nov).",
           "- Horizons: d90 (90 days before release), d7 (7 days before), d0 (release day = pf4's own timing, reference). Opening-weekend update: not built (no opening data).",
           "- Units: nominal USD as stored (budget converted by the money pipeline); budget inflation-adjusted only as a model input.",
           "- Intended use: research and experimental movie intelligence. Unintended use: profit / ROI claims, investment decisions, precise single-number estimates.", "",
           "## Dataset and provenance",
           f"- Films with budget >= $100k and gross > 0, released 1960-2023 (universe). Train <= 2015 ({H['d7']['n_train']:,}); "
           f"ranges: fit <= 2011 ({H['d7']['n_fit']:,}), calibration 2012-2015 ({H['d7']['n_cal']:,}); test 2016-2019 ({H['d7']['test']['n']:,}, NOT blind); late 2020-2023 ({H['d7']['late']['n']:,}).",
           "- Sources and licences: `feature_provenance.csv`. TMDB (API terms, attribution), Wikipedia (CC BY-SA), Wikidata (CC0).",
           "- As-of rule: `boxoffice_asof.py`; trailers counted only if published before the cutoff. ASSUMPTION: the final release date, runtime and the "
           "competitors' final dates are treated as known at d90; studios move dates, and the data keeps only the final ones.",
           "- Missing values stay missing (LightGBM handles NaN); there is no imputation.", "",
           "## Evaluation (from the run)", "",
           "| Horizon | Years | n | MAE log (typical miss) | RMSE log | median AE | 50% coverage / median width | 80% coverage / median width |",
           "|---|---|---|---|---|---|---|---|"]
    for h in ("d90", "d7", "d0"):
        out += [row(h, "test"), row(h, "late")]
    out += ["", "Rules (written 12 Nov, before any run): test 80% coverage 75-85% and 50% coverage 45-55%.",
            *[f"- {h}: **{H[h]['rule']}**" for h in ("d90", "d7", "d0")],
            f"- Example responses in the kit's `prediction_response` shape: {ev['responses_checked']} written, {ev['responses_invalid']} failed the checks.",
            "", "Width is the ratio upper / lower of the range: x20 means the upper end is twenty times the lower end. "
            "These ranges are honest about how uncertain a pre-release forecast is; they are too wide for most decisions.", "",
            "## Feature groups", ""]
    if factors:
        for g, d in factors.get("groups", {}).items():
            out.append(f"- {g} ({d['direction']}): gain {d['gain_test'][0]:+.3f} [{d['gain_test'][1]:+.3f}, {d['gain_test'][2]:+.3f}], "
                       f"Holm p {d['p_holm']:.3f} -> {d['verdict']}")
        out.append("- Full table, mechanisms and confounders: `correlation_report.md`.")
    else:
        out.append("- Factor study results not available when this card was written; see `correlation_report.md`.")
    out += ["", "## Calibration and deployment gate",
            "- Calibration: conformalized quantile regression, one widening per level from 2012-2015, applied unchanged to later years.",
            "- Display: none. Status `experimental`; a hidden endpoint behind FORECAST_PANEL (off) is slice 4.6. Nothing is shown on the site unless the rule passes AND the user approves.",
            "- Rollback: the model is a script output; deleting results files removes it. No live table is written.",
            "- Known limits: no distributor, screen counts, opening weekend, marketing spend, school calendars or international dates; "
            "track records use today's values of earlier films; 2016-2019 has been seen by every earlier model.",
            f"- Updated: {time.strftime('%Y-%m-%d')}"]
    open(os.path.join(OUT_DIR, "box_office_model_card.md"), "w").write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
