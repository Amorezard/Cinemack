# CINEMACK v8.1 — MASTER CLAUDE CODE IMPLEMENTATION PROMPT

**Status:** User-approved roadmap; implementation not yet performed.  
**Date and source baseline:** November 12, 2026. The supplied README describes the project as of November 11, 2026 (round 27).  
**Intended executor:** Claude Code operating inside the *actual* Cinemack repository on the user's machine/server, with file, shell, test, and Git access as permitted by the user.

> **READ FIRST:** `README_START_HERE.md`, `reference/CINEMACK_V8_README_2026-11-11.md`, `docs/IMPLEMENTATION_ROADMAP.md`, `docs/ACCEPTANCE_TEST_MATRIX.md`, `docs/ARCHITECTURE_AND_DATA_CONTRACTS.md`, `docs/UX_MOTION_SPEC.md`, `docs/RUNBOOK_AND_DEPLOYMENT.md`, `docs/RESEARCH_AND_SOURCE_NOTES.md`, `config/quality_gates.yaml`, and every JSON schema in `specs/`. Inspect `reference/UX_20_PRINCIPLES_SCREENSHOT.jpeg` as an *inspiration/principles reference*, not as a visual mockup or authorization to copy Instagram's interface.

## 0. YOUR ROLE AND EXECUTION CONTRACT

You are the lead software architect, Python/FastAPI and vanilla JS engineer, search/RAG engineer, recommender systems researcher, MLOps/ML evaluation engineer, security engineer, and UX/accessibility engineer for **Cinemack** (previously Movie Buff).

**Mission:** Audit the *actual* working code, design precise improvements, implement the approved P0 → P1 → P2 roadmap in small verified increments, and improve measurable product quality without destroying existing functionality. Preserve the existing no-build frontend and FastAPI/SQLite/Ollama approach unless data establishes that a targeted replacement is necessary.

**Do not treat this prompt as proof that the repository contains a particular function, DB schema, feature, or bug.** The attached README documents the intent/status; the code and reproducible tests determine reality. The files in `specs/` are **proposed interface contracts**, not drop-in migrations to run blindly.

### Non-negotiable operating rules

1. **Inspect before modifying:** examine Git status, README(s), docs, test structure, lock/config files, model configuration, execution environment, DB schema, source licensing, and the actual implementations. Produce a short **source-of-truth inventory** distinguishing README claims, code-verified facts, measured findings, and assumptions. Identify conflicts. Never guess a file's internals.
2. **Preserve all existing user data and research artifacts.** No deleting/rebuilding existing DBs, indexes, model weights, collections, histories, secrets, or result files without a verified backup, migration, rollback plan, and user approval for destructive steps. Absolutely no drop/truncate of production tables. Never overwrite an existing branch's uncommitted work.
3. **Don't touch secrets:** never display, copy, commit, upload, or log `keys.env`, `~/.movie-buff.env`, real user data, credentials, auth cookies, unredacted chat logs, private emails, or sensitive configuration. Respect `.gitignore`. Use synthetic/anonymized fixtures.
4. **No blind deployment:** make changes on a separate working branch (after checking with Git), use mock/local or port `49152` where permitted, and do not modify/restart the production service on `49151`, alter public DNS/reverse proxy, send actual emails, or activate paid APIs without explicit authorization.
5. **Feature flag first:** each meaningful feature has an explicit off-by-default environment switch and a safe fallback. Preserve existing `v7_lock.env`, `v8_lock.env`, `site.env`, and `config.yaml` semantics; never silently change defaults. Document interaction with existing switches.
6. **Rule before results:** write evaluation hypotheses, test sets, quantitative acceptance thresholds, and rollback rules *before* experimentation. Separate tuning/dev sets from untouched test/holdout sets. Never read the contents of `sets/sealed_v8_*` to tune. Do not execute a one-shot sealed final evaluation without approval and verified readiness; preserve MD5/hash artifacts and the original procedure.
7. **Factual integrity:** no invented accuracy improvements, p-values, model results, baselines, theaters, promotions, streaming prices, gross estimates, dates, availability, API capabilities, or sources. In reports label measured, inferred, proposed, and unknown separately. If something cannot be tested, mark BLOCKED or UNVERIFIED and explain why.
8. **Security boundaries:** all retrieved articles, Wikipedia/reviews, model outputs, and tool outputs are untrusted data. Model-generated SQL must never be executed; compile validated query plans into parameterized database queries. Apply authentication/authorization outside the model; guard against prompt injection, SSRF, XSS, cross-user data leakage, and tool abuse. Test with synthetic attacks.
9. **Performance and cost:** this is a student non-commercial research project using a 24 GB GPU. Prefer simpler, CPU-capable, maintainable improvements; budget VRAM, CPU, RAM, storage, batch jobs, external API quotas, and ongoing costs. Do not propose an expensive cloud or new DB solely because it is popular.
10. **Source licensing:** audit terms for TMDB, IMDb, MovieLens, Guardian, NYT, YouTube, TVmaze, watch-provider data, reviews and theater offers. Honor required attribution, rights, rate limits, and non-commercial restrictions. Do not scrape prohibited sites or ingest full copyrighted articles contrary to applicable terms. Commercialization requires a fresh licensing review.
11. **Incremental Git discipline:** implement one independently testable slice at a time, report exact commands/results, then commit with a descriptive message if the worktree and permissions allow. Never make a deceptive passing-test claim. Do not commit generated datasets, secrets or oversized binaries.
12. **Risk checkpoints:** proceed autonomously through *safe, local, reversible* tasks; pause for user authorization before irreversible data changes, one-shot sealed tests, production toggles/deployments, purchases, paid API enrollment, external outreach, or changes affecting other services on ports 80/443. If blocked, continue unrelated safe work and document the blocker; do not abandon the project.
13. **Documentation and handoff:** update an implementation log, changelog, architecture diagram, test evidence, model cards, source records, and deployment/rollback instructions. Explain tradeoffs and failed experiments. At the end of each phase provide a concise evidence-based status report.

### Definition of success

The product should become **more accurate, faster, more personalized, safer and easier to use** in measured ways. A larger model, more features, extra services, more animations or a higher similarity number are **not** success unless they improve appropriate user-facing metrics and maintain correctness.

## 1. VERIFIED README CONTEXT TO RECONCILE WITH SOURCE

The attached Nov 11 README says:

- FastAPI: `web/server.py`, `web/v8routes.py`, `web/v8extra.py`; no-build JS front end in `web/v8/`, one module per page. v8 at `/`; classic at `/classic`.
- Request flow: user → planner (`movie-buff` in Ollama) → `retrieve.py`/`movies.db`/`wiki.db` (vectors + FTS), `tools.py`/`facts.db` (structured facts) → streamed answer.
- DBs: `facts.db` (~8.8 GB), `movies.db`, `wiki.db`, plus `logs/tmdb_extra.db`, `logs/tvmaze.db`, and user/chat/memory/history/social/alerts databases. Verify locations and live schemas rather than assuming.
- Data: TMDB, Wikipedia, Wikidata, IMDb datasets, MovieLens, OMDb, Guardian, NYT **metadata only**, YouTube, Netflix engagement reports, TVmaze (website); credits tracked through `v7_data.SOURCES` and `web/about.html`.
- Existing v7 switches include `SEMCACHE` and retrieval/answer/recommendation flags. `SAME_NAME`, `TV_SEASONS`, `IDENT_HONEST`, `LONG_SUMMARY`, `PREF_CHIPS`, `STRIP_WHEN`, `ANSWER_PLOT_OK`, `GENRE_AGREE`, and `V8_HOME` have documented status.
- **Do not duplicate pending built work:** `MEM_CARDS`, `CLAIM_REWRITE`, `PERSONA=cinemack`, `CTX_BUILDER=1 CTX_BUILDER_V=3`, `GUARD2` are built but off/awaiting evaluation. `NEWS` is off after failing its gate; do NOT just turn it back on. `EMAIL_SANDBOX` remains on by default.
- `tools/embed_ft.py` is a shadow-only fine-tuning experiment. The live embedder remains Ollama mxbai until a predetermined gate is passed.
- Box office: target `log(worldwide gross / budget)`, pf4 baseline test MAE 1.200; pf5 A/C/D did not demonstrate improvement; pf5 B Guardian collection pending. Reported 80% interval coverage 78.1% (75–85% rule). `PERF=0`. Do **not** present a prediction model as launched or validated.
- `nightly.sh` (cron ~04:17); test copy `PORT=49152 bash start_site.sh`; mock site `MOVIE_BUFF_MOCK=1 PORT=8765`, browser check `tools/accept_redesign.js`; tests with per-file environment setup. Existing sealed test procedures matter.
- Existing Home, Chat, Pick Tonight, Calendar, Watchlist, Profile, TV progress, imports, social/privacy, alerts, and join flow should not be rewritten indiscriminately.
- HTTPS, authorized real showtimes, source-backed streaming departure dates and production push notifications are currently incomplete per README; email remains sandboxed.

**First output from you:** a factual **repo audit** including exact file paths, call graph, endpoint inventory, DB schemas (metadata only), flags, source attribution, unit/browser tests, runtime dependencies, and bottlenecks. Include a comparison table of *README describes / code confirms / evidence needed / decision*.

## 2. PRIORITIES AND EXECUTION ORDER

**P0 (must complete before P1 feature polish):**
- A. Baseline/security/data hygiene, canonical movie identity and provenance, observability.
- B. Structured JSON query plans, hybrid search, SQL filters, embeddings, chunks, article retrieval, answer grounding, cache quality/freshness.
- C. A personalized recommendation foundation shared by Chat, Home, Pick Tonight, detail pages; honest feedback and evaluation.
- D. Expanded **box-office prediction**: domestic/international/worldwide estimated theatrical grosses, gross-to-budget multiples, calibrated ranges, correlations and predictive analysis of release timing, director, cast, writers/creators, producers, genre/type, franchise, competitive landscape and budget; a guarded prediction interface.

**P1:** site-wide 20-principle UX/motion/accessibility improvement; Pick Tonight (one `Why I Picked This` per result) and Calendar refinements; multiuser concurrency/queues/GPU performance; controlled Ollama tool calling. P0 security and measurement foundations for concurrency must occur earlier.

**P2:** translations/localization/regional metadata and search; moviegoing deals (discount weekdays incl. Monday/Tuesday, ticket packs, student offers, memberships), streaming subscriptions/rental discounts, region-aware availability, alerts and verified terms.

Never artificially force sequential development when independent P0 lines can run in parallel, but ensure shared ID/data contracts precede integrations. Follow the package dependency graph in `docs/IMPLEMENTATION_ROADMAP.md`.

## 3. P0-A: PRE-FLIGHT, CANONICAL DATA AND OBSERVABILITY

1. Inventory routes and user flows, model size/quantization/context sizes and GPU budget, environment switches, indexes/FTS table definitions, DB backups/recovery, ingestion jobs, saved preferences/flags, and data-source entitlement.
2. Audit open round-27 experiments, `results/r27_night.txt`, source decision/issue logs and the stated evaluation matrix. Identify which tests were actually run and which gates remain open. Do not secretly consume sealed holdouts.
3. Establish a canonical film/show entity with stable internal ID, external provider IDs (TMDB/IMDb/Wikidata etc.), media type, aliases/localized titles, release date variants, data provenance, observation/ingestion timestamps, rights and conflict/missing-data flags. Prefer additive tables/views, not forced replacement of all existing keys.
4. Establish request-scoped tracing for intent planning, SQL, FTS, embedding, candidate retrieval, reranking, external article fetch, cache, context build, queueing, model first-token, answer generation. No raw private prompts in metrics by default. Benchmark warm and cold queries.
5. Add/verify SQLite connection lifecycle, busy timeouts, safe migrations, backups, read/write contention; consider WAL per DB after testing on its actual filesystem and workload. Enforce query budgets, indexes and `EXPLAIN QUERY PLAN` for slow paths.
6. Audit auth/privacy, cross-user isolation, HTTP/public deployment hazards and image/upload sanitization. HTTPS should be a launch gate but do not disturb ports 80/443 without permission.

## 4. P0-B: STRUCTURED QUERY PLANNER + IMPROVED RAG

### Interface and intent

Use `specs/movie_query.schema.json` as a proposed v1 interface; adapt only after reconciling actual app needs and versioning changes. The model may produce JSON using local Ollama structured outputs (`format` JSON schema), but application code must **validate** output with Pydantic (or equivalent), resolve provider/movie IDs, reject unknown fields, and compile allowlisted filters. Provide safe fallback for malformed plans and ambiguous entities.

- Intents: film identification, facts, compare, reception/analysis, recommendations, availability, news/recent articles, forecast.
- Query constraints: movie/TV, explicit year bounds, runtime, genre `any/all/exclude`, provider OR, monetization type, region, watched, language, title/people IDs, hard/soft preference distinction.
- Resolve title/movie-vs-show ambiguity before semantic search if possible. Never use embedding similarity as a hard filter.
- Shared semantics across Chat, Home, Pick Tonight, Calendar search. Selecting all genres cannot make results random. Stable tie-breaking and deterministic pagination. Test multi-genre intersections and sparse metadata.

### Retrieval and answers

- Baseline existing vector + FTS first; compare combined ranking (e.g. reciprocal rank fusion), targeted rerankers, source diversity, candidate caps and latency. Avoid automatic adoption of a new vector database.
- Compare chunk strategies per content type: section-aware plot/Wikipedia, review paragraphs/arguments, article sections and parent-child context, plus structured metadata instead of verbose chunks. Treat suggested sizes as hypotheses, test recall and groundedness. Keep stable chunk IDs and indexing/embedding versions.
- Compare existing mxbai against the `tools/embed_ft.py` shadow checkpoint on untuned title identification and semantic retrieval tests. Include multilingual and alias tests when applicable. **Do not activate the fine-tuned embedder without passing its gate.**
- Audit `CTX_BUILDER_V=3`, `CLAIM_REWRITE`, `MEM_CARDS`, `GUARD2`; evaluate one feature at a time and in combinations only after separate gates pass. Preserve source fact lines and evidence pointers.
- Keep fetched article/review knowledge **distinct from prediction training**. Build targeted intent-routed article retrieval for reception/why-it-worked/news, with rights-compliant providers, source attribution, timestamps, deduplication, and optional fresh refresh. `NEWS` stays off until an improved implementation passes a new predeclared gate.
- Answer behavior: concise facts from SQL; grounded multi-source analysis with citations; identification uncertainty; verifiable recommendations with constraints; no fabricated movies, sources or streaming availability. Version prompts by intent, test planner retries and latency budgets.

### Multi-layer cache

- Audit current `SEMCACHE` behavior first; define exact-key, embedding, retrieval-set, public article, per-user recommendation and optional answer caching separately. Use normalized title IDs, media type, intent, hard filters, locale, region, access scope, source/index versions in keys. Cache public evidence not personal answers across users.
- Start with freshness policies by data class (short for current offers/news, longer for historical metadata/articles), then test actual source frequency. Maintain TTL + explicit invalidation on ingestion, user feedback, provider changes, model/index version, permissions. Cache provenance and stale-data indicators. Test adversarial near-neighbor mismatches and no cross-user hits.

## 5. P0-C: PERSONAL RECOMMENDATION ENGINE

Build one ranking interface behind a new switch to serve Home, Chat, Pick Tonight, Watchlist, and detail pages. Use user data **only with existing consent/privacy controls**.

**Pipeline:** source candidates (metadata similarity + existing popularity + optional collaborative) → apply hard filters → enrich features → personalized scoring → diversify/rerank → generate *one* reason-code-based explanation per result → present. Preserve stable candidate IDs and a source/recommendation-version trail.

- Distinguish signals: rating (strong positive/negative), watchlist (interest), clicked vs exposed (weak/biased), watched (not equal to liked), completion, dismissal, 'already seen', Pick Tonight selection, "more like this". Do not silently assume non-clicks are dislikes.
- User cold start: selected genres, recent likes, watch history imports when authorized, anonymous contextual mood and robust popularity fallback. Respect preference changes, watch exclusions and explicit negative feedback.
- Exploration/diversity: avoid 12 near-duplicates, prevent repeated/seen movies unless asked, handle favorite genres and all-genres-selected, support multi-person group session preferences with transparent aggregation.
- Evaluate offline with time-based splits (Recall@k, NDCG@k, coverage, novelty/diversity, calibration if probabilistic); record exposure and position. Later online A/B only if feasible and privacy-respecting. Compare against current heuristic and simple popularity/content-based baselines.
- Never label an arbitrary model score as "chance you'll love it." Only present probabilities after calibration against a well-defined outcome.
- Candidate scores/reason codes must be independent of LLM prose, which is presentation-only. One canonical `why_i_picked_this` field in UI.
- See proposed exposure/event contract in `specs/recommendation_event.schema.json`.

## 6. P0-D: BOX-OFFICE FORECASTING AND CORRELATION RESEARCH (CRITICAL)

**This is P0, not P2.** Respect existing pf1–pf5 evaluations; never erase or quietly redefine them. Reproduce first. Train research models using point-in-time-valid inputs; track missingness, data provenance and model/feature versions. Keep forecast research separate from personal taste rankings.

### Required predicted quantities

- Domestic theatrical gross (where meaningfully defined and data supports it), international theatrical gross, worldwide theatrical gross, and worldwide gross / known production budget multiple.
- Central estimate and **calibrated predictive ranges**, initially 50% and 80%; 90% optional if supportable. Report coverage and width on untouched chronological tests. The existing 75–85% empirical coverage tolerance for the 80% interval is a *reference gate*, not permission to overfit it.
- Gross less reported production budget may be reported as a **descriptive difference only**. Do NOT label theatrical gross/budget or gross-minus-budget as studio profit, ROI, break-even or net return. Real economics require marketing, theater revenue splits and other unknowns.
- Derived domestic + international + worldwide outputs must reconcile within a documented unit/rounding tolerance. Define region/currency, release scope, inflation handling and cutoff timestamps. A model may estimate components and reconcile them consistently; do not multiply unrelated independent intervals and pretend they are joint intervals.
- Distinct prediction horizons: e.g. ~90 days before release, ~7 days before release, and opening-weekend-updated forecast. Evaluate and label horizons separately. Do not use post-release information in pre-release models.
- New endpoint / internal panel behind a feature flag with model version, prediction `as_of`, available features, uncertainties, confidence limitations and later actuals. If quality gates fail, keep public UI off while producing an internal research report.

### Factor/correlation study — all required

Investigate **release timing** (month/day, holiday calendar, school breaks, competing films, domestic/international dates); **directors**; **actors** (past results, billing/ensemble); **writers/creators**; **producers/production companies**; **genre/subgenre and film type** (animation/live action, rating, intended audience); franchise/brand, adaptation/original status; distributor, known budget, pre-release marketing/trailer interest (only if timestamp-valid); macro environment; pre-release critical scores where actually known at cutoff. Test interactions like holiday × genre, director × genre, star × budget, distributor × release scale.

For every group:
- State plausible mechanism and confounders; analyze data availability, missingness, sample sizes and historical distribution shifts.
- Describe unadjusted associations, controlled estimates or permutation importance with caution, and **incremental predictive value** via grouped ablation vs pf4 and stronger contemporary baselines.
- Handle target transformations, heavy tails, non-independence among actor/director/film histories, multiple comparisons and uncertainty. Use chronological splits and bootstrap intervals, avoid random splits causing temporal leakage. Source all feature values as known by prediction date or mark unavailable; current TMDB/IMDb fields alone do not prove historical availability.
- Test regularized regressions/quantile approaches, gradient boosting and the existing models; prefer simpler models where accuracy/calibration equivalent. Compare MAE/RMSE on an explicitly named scale, interval coverage/width, error by budget/genre/franchise/date, and post-2020 drift where data permits.
- Positive correlation **does not prove causation**; do not state “actor X caused gross Y.” Report unsupported experiments transparently and exclude features from production if no out-of-sample gain.
- Preserve pf5 B Guardian collection and source quotas; do not insert article features into pre-release models unless available-as-of is established. Never use current articles about past movies as supposedly pre-release signals.

Write research deliverables: `box_office_model_card.md`, `feature_provenance.csv` (no secrets), `correlation_report.md`, `forecast_eval.json`, and comparison tables. Never invent real numbers; fill them only from executed experiments.

## 7. P1: UI/UX, PICK TONIGHT, CALENDAR, CONCURRENCY AND TOOLS

### 20 UX principles

Implement the screenshot's list selectively as defined in `docs/UX_MOTION_SPEC.md`: squash/stretch, anticipation, staging, pose-to-pose, follow-through, easing, arcs, secondary action, snappy timing, exaggerated feedback, solid 3D, overall appeal, Fitts's law, larger hit areas, Hick's law, Miller's law (logical grouping), Doherty threshold, Postel's law (permissive external inputs/strict internals), Jakob's law, frequency of use.

**Never add animation just to check a box.** Maintain reduced-motion support, keyboard navigation, focus state, contrast, screen reader labels, stable images, and WCAG 2.2 AA goals. Start motion tokens around 100/150/220/300 ms for micro/selection/card/panel interactions, but adjust after browser tests. Favor transform and opacity; avoid layout jumps and animated costly properties. Aim for mobile + desktop field targets LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 at p75 when representative data exists.

### Pick Tonight

- Preserve previously approved look unless actual screenshots or attached design refs show otherwise; do not redesign from scratch.
- One canonical "Why I Picked This" section **per returned title**; avoid duplicate reason-generators or conflicting copy. Hard filters cannot be bypassed by LLM.
- Short session setup: who watching, mood, time budget, hard restrictions; optional group balancing, stable reroll criteria and clear filters. Cards should communicate decision, availability confidence, and fallback if nothing qualifies.

### Calendar

- Preserve existing week/month, theatrical/digital, TV season/episode/finale/followed-show functions and TVmaze user-timezone behavior.
- Improve date hierarchy, event type labels, selected-day persistence, mobile navigation, poster sizing, keyboard support, timezone/region and confirmed vs tentative date states. Do not accidentally change release calendars into personal viewing schedules.

### Fast site-wide UI

- Search/genre filters must update selection state immediately; cancel outdated requests; order results predictably; prevent home poster shifting. Optimize images, maintain lazy loading and responsive sizes; avoid endless loading states. Build on the existing `web/v8/` ES modules without introducing a SPA framework automatically.
- Test page loading, filtering, watchlist/TV progress, imports, auth, profile and social features after any shared component changes. Reuse and extend `tools/accept_redesign.js`.

### Multiuser / GPU

- Reproduce current chat latency under GPU competition. Load-test 1,2,4,8 simultaneous users with representative prompts. Measure p50/p95 latency, queue wait, time to first token, tokens/sec, GPU VRAM, memory, failed requests, cancellation and fair access. Test isolation between users and no crosstalk in streams or caches.
- Tune `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_MAX_QUEUE` only after measuring actual weights/context/quantization. Separate latency-critical chat from batch embeddings/ingestion. Add bounded fair admission, per-user rate limits, queue and timeout visibility; preserve data access permissions.
- Do not switch to vLLM or add a second GPU without a realistic, comparable benchmark showing improvement.

### Controlled tools

- Use small allowlisted functions such as `movie.lookup`, `catalog.search`, `articles.search`, `availability.lookup`, `recommendations.rank`, `prediction.box_office`, `user.watchlist` (authorized only), with validated args, bounded results, timeouts and idempotence where relevant.
- Prefer a structured one-shot planner + deterministic router for ordinary requests. Tool calling must not introduce extra LLM rounds for simple factual questions. No unrestricted SQL, shell, HTTP requests, third-party purchases or cross-user operations.

## 8. P2: INTERNATIONALIZATION + MOVIE DEALS

### Localization

- Translation dictionaries for all site text; localize dates/times/numbers and cinema release regions; store distinct display language, content language, streaming country and timezone. Support original vs translated reviews, localized title aliases, and fallback states.
- Pilot English/Spanish/French/Portuguese (subject to real audience priorities), with RTL readiness and language-specific search regression tests. Multilingual embeddings must win against the existing baseline before swapping. Never automatically translate stable user IDs or model feature names.

### Deals, memberships and theater availability

- Deals include recurring theater discount **Mondays/Tuesdays and other weekdays**, movie-ticket bundles, matinees, student/military eligibility if offered, loyalty membership and paid subscriptions, concessions if relevant, rental and streaming subscription promotions, and watchlist-based alerts.
- Use `specs/deal_offer.schema.json` as the proposed record. Verify **official sources and location-specific rules**, dates, memberships, taxes/fees, exclusions (premium formats, blackout dates), currency, promo stacking, source URL, `last_verified_at`, and expired status. Never invent live checkout pricing, a 50%-off claim, or real-time bookability without authorized verified data.
- Separate the existence of an official promotion from **confirmed ticket price**. Link users to authorized pages. Show unknown instead of unsupported. Do not scrape protected showtime/price feeds or imply theater booking if not integrated.
- Streaming: country-specific availability, rental/purchase versus included-in-subscription, offer validity, legally permitted deep-links and attribution. `TMDB` developer data is **non-commercial with attribution** under published FAQ; recheck rights before any revenue/affiliate plan.
- A future comparison may calculate the cost for a group only when each input is verified, with eligibility and extra charges made explicit.

## 9. PHASE GATES AND TESTING

Adopt `config/quality_gates.yaml` as a **proposed** gate file; compare it with the repository's existing `docs/validation_matrix.md`. Preserve stricter existing gates. Use the detailed test list `docs/ACCEPTANCE_TEST_MATRIX.md`.

Required categories:

- **Correctness:** 100% conformance to hard constraints on the curated regression set; stable IDs/genre Any/All; title and movie-vs-TV disambiguation.
- **Retrieval:** held-out Recall@k/MRR/nDCG, groundedness/source attribution, no-match honesty and latency with ablations.
- **Cache:** no user/permission contamination, no incompatible filter hits, explicit invalidation and TTL correctness; measurable benefit after correctness.
- **Recommender:** chronological NDCG/Recall against existing rule-based/popularity baselines, coverage and diversity, no incorrect watched/liked inference, consistent reasons.
- **Forecast:** reproduce pf4/pf5, robust as-of validation, domestic/international/worldwide consistency, baseline relative error, 80% calibration reference, subgroup diagnostics and realistic uncertainty width.
- **Security:** auth isolation, injection, XSS, safe SQL, tools, rate limits, secrets, HTTPS readiness.
- **UX:** no layout shift, preserved date/filter state, 1 explanation/title, keyboard/reduced motion/mobile, state feedback and browser tests.
- **Perf:** compare p50/p95, first token, memory, throughput under load and batch interference; do not claim concurrency capacity without actual results.
- **Data/rights:** quality, source timestamps, attribution, ingestion integrity, false-stale deals, no unlicensed dependencies.

**Stop-loss rule:** Do not promote a feature that materially worsens a higher-priority regression gate even if it improves a secondary metric. Keep failed experiments behind flags/off and document them. Use small commits and rollback instructions.

## 10. REQUIRED EXECUTION LOOP AND OUTPUTS

**At the beginning, before editing code, deliver:**

A. `AUDIT_REPORT.md` — architecture and call graph, status of existing features/flags, actual metrics, risks, licensing/permission questions, file-by-file touch list, data migration impact and open questions with reasonable defaults.

B. `IMPLEMENTATION_PLAN_LOCKED.md` — numbered vertical slices aligned to P0/P1/P2; for each: exact files/functions to inspect/edit, migration strategy, new flag(s), test commands, expected metrics, dependencies, fallback/rollback and approximate operational cost. Do not hallucinate file names beyond the actual tree. Call out what can happen safely now and what requires user permission.

C. `BASELINE_RESULTS.md` — test logs and environment info; mark tests blocked/unrun where appropriate, include reproducible commands and no secrets.

**Then for each small slice:**

1. State the exact defect/opportunity, current measurable baseline, and written pass/fail rule.
2. Identify the smallest safe implementation, DB change (if any), flag and rollback.
3. Implement code and tests with synthetic or fixture data; preserve interfaces via adapters where useful.
4. Run unit/integration/browser/evaluation/load tests appropriate to the slice. Record exact commands and observed metrics, not conjecture.
5. Compare with control; keep improvement only if gates pass. Document failure and restore baseline otherwise.
6. Commit a tested slice if permitted; show commit hash and short changeset, or state why no commit occurred.
7. Update `docs/decision_log.md`, `docs/issue_log.md`/equivalent, implementation log, model cards and user-facing notes as applicable.
8. Move to next safe dependency-unblocked slice. Stop only for authorization gates or genuine blockers; continue unaffected tasks.

**Status report format after each slice:**

- Slice / priority / branch / commit
- What changed and why
- Files and DB migrations
- Test commands and results
- Before/after metrics with test set names
- Feature flag and rollback
- Risks/blockers and next slice

### Your immediate next action (when launched inside the repository)

1. Read the entire kit and project docs, then run only **read-only discovery commands** (e.g. `pwd`, `git status`, `git branch`, `git log -5`, `find`, `rg`, `sqlite3 ... .schema` in read-only mode when safe, `ollama list/ps`, test discovery). Do not print credentials or read private datasets for report generation.
2. Find and reconcile the actual root `README.md` history with this Nov 11 v8 README.
3. Inspect pending round-27 runs and existing source/issue/decision logs without consuming sealed sets.
4. Inventory P0 modules and candidate functions. Verify staging/mock site works and record baseline. Never assume external API access or live GPU access.
5. Produce A/B/C above, then start the first safe, testable P0 slice. **This prompt authorizes local reversible development, not live deployment or irreversible changes.**

**DO NOT replace the plan with a shorter generic prompt. DO NOT stop after making mockups. DO NOT declare work complete without reproducible evidence.**
