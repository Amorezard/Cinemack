# Box-office forecast model card (v8.1)

Filled by `tools/v81_forecast.py` from its own run (`forecast_eval.json`); nothing below is typed by hand except the fixed text.

## Identity and objective
- Model version / algorithm: `v81-pf4bt-cqr-1` - LightGBM (Huber loss, pf4 settings), mean of 5 seeds; ranges by conformalized quantile regression.
- Predictive target: worldwide theatrical gross through log(worldwide gross / production budget); gross = budget x exp(prediction); gross-to-budget multiple = exp(prediction). **Domestic and international: not estimated** (no territory data; decided with the user 13 Nov).
- Horizons: d90 (90 days before release), d7 (7 days before), d0 (release day = pf4's own timing, reference). Opening-weekend update: not built (no opening data).
- Units: nominal USD as stored (budget converted by the money pipeline); budget inflation-adjusted only as a model input.
- Intended use: research and experimental movie intelligence. Unintended use: profit / ROI claims, investment decisions, precise single-number estimates.

## Dataset and provenance
- Films with budget >= $100k and gross > 0, released 1960-2023 (universe). Train <= 2015 (8,740); ranges: fit <= 2011 (7,222), calibration 2012-2015 (1,518); test 2016-2019 (1,487, NOT blind); late 2020-2023 (787).
- Sources and licences: `feature_provenance.csv`. TMDB (API terms, attribution), Wikipedia (CC BY-SA), Wikidata (CC0).
- As-of rule: `boxoffice_asof.py`; trailers counted only if published before the cutoff. ASSUMPTION: the final release date, runtime and the competitors' final dates are treated as known at d90; studios move dates, and the data keeps only the final ones.
- Missing values stay missing (LightGBM handles NaN); there is no imputation.

## Evaluation (from the run)

| Horizon | Years | n | MAE log (typical miss) | RMSE log | median AE | 50% coverage / median width | 80% coverage / median width |
|---|---|---|---|---|---|---|---|
| d90 | test | 1,487 | 1.218 (x3.38) | 1.716 | 0.848 | 47.6% / x5.9 | 78.2% / x41.6 |
| d90 | late | 787 | 1.421 (x4.14) | 2.116 | 0.949 | 43.5% / x5.9 | 75.7% / x41.6 |
| d7 | test | 1,487 | 1.202 (x3.33) | 1.695 | 0.844 | 47.3% / x5.7 | 79.0% / x39.1 |
| d7 | late | 787 | 1.429 (x4.18) | 2.114 | 0.965 | 43.1% / x5.7 | 74.2% / x38.5 |
| d0 | test | 1,487 | 1.194 (x3.30) | 1.688 | 0.842 | 46.9% / x5.7 | 77.6% / x36.1 |
| d0 | late | 787 | 1.432 (x4.19) | 2.119 | 0.958 | 41.4% / x5.4 | 72.9% / x35.2 |

Rules (written 12 Nov, before any run): test 80% coverage 75-85% and 50% coverage 45-55%.
- d90: **PASS**
- d7: **PASS**
- d0: **reference (pf4 timing; not a pre-release horizon)**
- Example responses in the kit's `prediction_response` shape: 20 written, 0 failed the checks.

Width is the ratio upper / lower of the range: x20 means the upper end is twenty times the lower end. These ranges are honest about how uncertain a pre-release forecast is; they are too wide for most decisions.

## Feature groups

- budget (drop): gain +0.017 [+0.005, +0.029], Holm p 0.042 -> carries value
- release timing (drop): gain +0.000 [-0.003, +0.004], Holm p 1.000 -> no clear value
- competition (drop): gain +0.000 [-0.004, +0.005], Holm p 1.000 -> no clear value
- director track record (drop): gain +0.006 [-0.002, +0.015], Holm p 0.635 -> no clear value
- cast track record (drop): gain +0.015 [+0.005, +0.027], Holm p 0.028 -> carries value
- source / author (drop): gain +0.000 [-0.002, +0.003], Holm p 1.000 -> no clear value
- production companies (drop): gain -0.001 [-0.004, +0.001], Holm p 1.000 -> no clear value
- genre and film type (drop): gain +0.052 [+0.038, +0.067], Holm p 0.005 -> carries value
- franchise (drop): gain +0.014 [+0.007, +0.022], Holm p 0.005 -> carries value
- trailers (drop): gain +0.060 [+0.046, +0.074], Holm p 0.005 -> carries value
- producer track record (new) (add): gain +0.022 [+0.007, +0.039], Holm p 0.012 -> carries value
- writer track record (new) (add): gain +0.003 [-0.005, +0.010], Holm p 0.478 -> no clear value
- US rating (new) (add): gain +0.031 [+0.020, +0.042], Holm p 0.002 -> carries value
- Full table, mechanisms and confounders: `correlation_report.md`.

## Calibration and deployment gate
- Calibration: conformalized quantile regression, one widening per level from 2012-2015, applied unchanged to later years.
- Display: none. Status `experimental`; a hidden endpoint behind FORECAST_PANEL (off) is slice 4.6. Nothing is shown on the site unless the rule passes AND the user approves.
- Rollback: the model is a script output; deleting results files removes it. No live table is written.
- Known limits: no distributor, screen counts, opening weekend, marketing spend, school calendars or international dates; track records use today's values of earlier films; 2016-2019 has been seen by every earlier model.
- Updated: 2026-11-14
