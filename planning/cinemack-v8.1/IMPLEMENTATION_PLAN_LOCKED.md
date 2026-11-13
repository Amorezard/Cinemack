# Cinemack v8.1 - implementation plan (locked Thu 12 Nov 2026, after the audit)

Based on AUDIT_REPORT.md (commit 929bbbf). File names below exist in the source unless marked NEW.
Target date given by the user: Thu 19 Nov. Each slice ends as PASS, FAIL (kept off, documented) or BLOCKED (reason). Estimates are planning estimates.

## Ground rules for every slice
- Work only in the worktree `~/movie-buff/rag-v81` (branch `v8.1`, created from 929bbbf in round 41). The live folder `~/movie-buff/rag` and port 49151 are not touched. A slice reaches the live site only through a separate, explicitly approved round (`todo_LIVE_rNN.sh`).
- Data: the worktree gets its own `facts.db` copy (SQLite backup API, `FACTS_DB` variable). `movies.db`, `wiki.db` and `logs/tvmaze.db` are opened read-only from the live folder when a slice needs them; no v8.1 code writes to a live database. Per-person databases are never copied; tests use synthetic accounts.
- Every new behaviour has a NEW switch, default off. Existing switches keep their meaning.
- Rule first: each slice's pass rule is committed (this file or `docs/decision_log.md` on the branch) before its first run. Sealed sets are never read; no sealed run.
- Tests: `python -m unittest tests.<module>` per module, as today. New modules are named `tests/test_v81_*.py`.
- Rollback for every slice: switch off, or `git revert` on the branch; the worktree's database copy can be deleted and re-made.

## Order and status

| # | Slice | Kit | Needs GPU | Needs a yes | Planned |
|---|---|---|---|---|---|
| 0.1 | Audit, baseline, this plan | 0 | no | no | done 12 Nov |
| 0.2 | Worktree + branch, kit under `planning/cinemack-v8.1/`, reports committed, `facts.db` copy | 0 | no | given 12 Nov | round 41 |
| 0.3 | Fix the 6 uncollected tests (`tests/test_v7_oct30_fixes.py` -> TestCase); `nightly.sh --dry` recorded | 0 | no | no | round 41 / 42 |
| 4.1 | Reproduce pf4 / pf5 on the copy; compare with the committed result files | 4 | no | no | round 41 night |
| 4.2 | Box-office data inventory: coverage and missingness by year and feature group; what `facts` holds per source | 4 | no | no | round 41 night |
| 2.1 | Query plan: NEW `web/query_plan.py` (pydantic model from `specs/movie_query.schema.json`, unknown fields rejected, enums for intent / media / genre mode) + NEW `web/plan_sql.py` (compiles a validated plan to parameterised SQL over `films`, `certifications`, `watch_providers`; no string SQL from the model). Switch `QUERY_PLAN=1`. | 2 | no | no | 13 Nov |
| 2.2 | Filter conformance set: NEW `sets/v81_filters.json` (curated: runtime, year, genre any / all / exclude, all genres, certificate, services, missing metadata) and NEW `tools/filter_conformance.py` comparing `plan_sql`, `retrieve.passes`, `user_prefs.excluded`, `discovery.match_genres` on the facts copy. | 2 | no | no | 13 Nov |
| 1 | Identity: NEW additive view / table `media_identity` + `media_external_ids` in the COPY (TMDB id stays the key; IMDb, Wikidata, TVmaze ids; media type; provenance and fetched-at from existing columns); NEW `tools/identity_audit.py` (duplicates, missing IMDb, film/show same title). | 1 | no | no | 13-14 Nov |
| 4.3 | As-of feature table: NEW `boxoffice_asof.py` building one row per film per horizon (d90, d7) from `performance.build_table` inputs with an availability date per feature; leak probe test (a seeded post-release feature must be rejected). | 4 | no | no | 14 Nov |
| 4.4 | Factor study: release timing, director, cast, writers, producers / companies, genre and type, franchise, budget; unadjusted association, grouped ablation vs pf4 with bootstrap CI, Holm correction; NEW `boxoffice_factors.py` -> `correlation_report.md`, `feature_provenance.csv`. | 4 | no | no | 14-15 Nov (night) |
| 4.5 | Worldwide gross forecast + 50% / 80% conformal ranges per horizon; gross-to-budget multiple; Wikidata territory-gross coverage count; `forecast_eval.json`, `box_office_model_card.md`. Domestic / international stay `null` unless coverage supports them. | 4 | no | Wikidata fetch = ordinary; other sources = yes | 15-16 Nov |
| 4.6 | Forecast endpoint `/api/v8/forecast/{id}` + hidden panel, switch `FORECAST_PANEL=0`; response validated against `specs/prediction_response.schema.json`; no "profit" wording test. | 4 | no | live = yes | 16 Nov |
| 5.1 | Recommendation events: NEW table `rec_events` (in a NEW `logs/rec_events.db`, server-derived user, consent flag) + NEW `web/rec_events.py`; switch `REC_EVENTS=0`. | 5 | no | live = yes | 15 Nov |
| 5.2 | One ranking interface: NEW `web/recs.py` `rank(user, plan, k)` = candidates (content + MovieLens neighbours + popularity) -> `plan_sql` hard filters -> score -> diversity -> one reason code per title. Offline benchmark on MovieLens, time-ordered split: NDCG@10 / Recall@10 / coverage / diversity vs popularity, content-only and the current `personal.rerank`. Switch `RECS_V2=0`. | 5 | no | no | 15-17 Nov (night) |
| 3.1 | Retrieval baseline and fusion comparison on tuning sets (`stage_scores.py`, existing `fusion_sim.py`); chunk and embedder comparisons stay shadow. | 3 | yes | GPU time | when the GPU is free |
| 3.4 | Cache tests: two users, different filters, negation near-misses against `semcache.same_meaning` and `site_cache` keys (no model needed); versioned keys only if a test fails. | 3 | no | no | 16 Nov |
| 6.1 | Motion tokens (100 / 150 / 220 / 300 ms) and reduced-motion audit in `web/v8/app.css`; hit-area and focus checks added to `tools/accept_redesign.js`. | 6 | no | live = yes | 17 Nov |
| 6.2 | Pick Tonight: exactly one "Why I picked this" per title, fed by 5.2's reason code (`web/v8/pick.js`); browser test counts the blocks. | 6 | no | live = yes | 17 Nov |
| 6.3 | Calendar: selected day kept across week / month, tentative vs confirmed label (`web/v8/calendar.js`); existing calendar tests stay green. | 6 | no | live = yes | 18 Nov |
| 7.1 | Load measurement 1 / 2 / 4 / 8 users with `tools/chat_bench.py` on a test copy; fair queue only if the numbers ask for it. | 7 | yes | yes (shared GPU) | night, when allowed |
| 7.2 | Tool router: allow-listed typed tools over `query_plan` (lookup, search, recommend, forecast); rejection tests for SQL / URL / shell / other user's data. Switch `TOOL_ROUTER=0`. | 7 | tests no | no | 18 Nov |
| 8.1 | Strings: NEW `web/v8/i18n.js` + `web/v8/locales/en.json`, `es.json`; fallback to English; dates and numbers through `Intl`. Switch `I18N=0`. | 8 | no | live = yes | 18-19 Nov |
| 9.1 | Deals: table + validator from `specs/deal_offer.schema.json`, unknown-price state, expiry; NO real offers until a source is named. Switch `DEALS=0`. | 9 | no | data source = yes | 19 Nov |

## Rules written now (before any result)
- **4.1 reproduction:** PASS if the pf4 arm's test MAE printed by `pf5_model.py` on the copy is 1.200 to three decimals and the counts are 8,742 train / 1,487 test. Any other value is reported as a deviation with the cause looked for (the data changed after 6 Nov: round 27 money repairs, nightly updates).
- **2.2 filter conformance:** PASS only at 100% of the curated cases for `plan_sql`; each existing path's misses are listed, not hidden. Unknown metadata must fail a strict limit (the site's present rule).
- **4.3 leak probe:** PASS if the seeded leak is rejected and every feature in the as-of table has an availability date not after the cutoff.
- **4.4 factor groups:** a group "adds predictive value" only if the bootstrap 95% CI of the MAE difference vs pf4 (before + trailers) on 2016-2019 excludes 0 after Holm correction over the groups tested. 2016-2019 has been seen by pf1-pf5, so it is NOT blind; this is said in the report. Associations are never written as causes.
- **4.5 ranges:** 80% range coverage on 2016-2019 inside 75-85% AND the median width reported; 50% range coverage inside 45-55%. Method fixed before the run: conformalized quantile regression, quantile models on <= 2011, conformity scores on 2012-2015 (same as pf5 v3). Because pf5 v3 already looked at 2016-2019 coverage, 2020-2023 coverage is reported next to it as the less-seen check.
- **5.2 benchmark:** `RECS_V2` is a candidate only if NDCG@10 on the held-out last interactions is not lower than the current `personal.rerank` (bootstrap 95% CI of the difference does not lie wholly below 0) and catalogue coverage is not lower. Scores are never shown as probabilities.
- **Stop-loss:** a slice that makes an earlier gate worse stays off.

## Added 13 Nov ~12:00, before any of these ran
- **2.2 conformance, reference rule details:** the reference is plain Python from the rule text; certificate choice = theatrical (type 3) first, then ascending type, rows with no type LAST, then table order (this is how SQLite orders the site's query; the first local run on made-up data showed the reference had put no-type rows first). Paths compared: plan_sql (rule: 100%), site `user_prefs.excluded` with GENRE_MAIN off, Home `discovery.match_genres`, chat `retrieve.passes`. Differences in the last three are reported, not fixed in this slice.
- **4.4 factor study, method details:** every arm = mean of 5 LightGBM fits (seeds 7, 11, 13, 17, 19) with pf4's settings; paired bootstrap of per-film absolute errors (2,000 resamples); two-sided bootstrap p-value; Holm over the 10 drop-groups and, separately, over the 3 add-groups; a group "carries value" if Holm p < 0.05 and the gain is positive. New add-groups: producer track record, writer track record (earlier films only), US rating one-hot. Groups and their inputs are listed in `tools/v81_factor_study.py` (DROP_GROUPS / ADD_GROUPS) as committed before the run.
- **1.0 identity audit:** counts only (no table created yet); the additive identity table (slice 1.1) is designed from its numbers.

## Cannot be finished by 19 Nov (stated now)
- pf5 B (Guardian): fetch limit 500 calls a day; weeks, not days.
- Domestic / international forecasts: no data yet (audit F1); at best a coverage count from Wikidata this week.
- Field Web Vitals (p75 on real traffic): 3 accounts; only lab numbers are possible.
- Online recommendation test: no traffic; offline MovieLens benchmark only.
- Real deals and verified prices; translations checked by a native speaker; HTTPS.

## What needs the user's explicit yes (none assumed)
Live-site install or restart; anything on the shared GPU at load (7.1) or long model runs; sealed runs; real email; paid data; deleting or migrating a live database; ports 80/443.
