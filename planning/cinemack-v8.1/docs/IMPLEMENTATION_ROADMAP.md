# Cinemack v8.1 — execution roadmap / dependency graph

**Source:** approved user roadmap + Nov 11 2026 README; **not** a claim of repository audit.

## Safe dependency sequence

```text
0 Audit & baseline ──> 1 Canonical IDs + provenance ──> 2 JSON planner + hard filters
      │                           │                         │
      │                           ├──> 5 Forecast research  │
      │                           │                         ├──> 3 RAG/cache/article retrieval
      │                           └──> 6 Rec events/engine <┘
      │                                                 │
      ├──────────────────────> 8 Concurrency foundations │
      │                                                 v
      └──────────────────────────────────────────> 7 Pick Tonight/Calendar/UX
                                                       │
                                                       v
                                              9 Localization + 10 Deals
```

P0 packages 4–6 can be investigated in parallel after prerequisites; P1 concurrency profiling starts in Phase 0 even if tuned later. No blanket DB migration, no blind sealed-set run.

| ID | Priority | Implementation slice | Inputs to inspect | Outputs | Safety / test gate |
|---|---|---|---|---|---|
| 0.1 | P0 | Repository/tree/flags/test audit | Root README, `docs/`, lock env, `web/`, `retrieve.py`, `tools.py`, `performance.py`, pf5 files, nightly | Repo inventory, feature status, dependency plan | Read-only; preserve dirty worktree |
| 0.2 | P0 | Baseline acceptance + performance | Mock site, `tests/`, `tools/accept_redesign.js`, eval scripts, Ollama | Baseline metrics by task/load | No sealed or production runs without approval |
| 0.3 | P0 | Security/privacy/backup check | Auth, chat/memory/social DB connections, keys/permissions, HTTPS topology | Threat model, backup/restore drill using copies | No real user export or credential logging |
| 1 | P0 | Canonical media/entity records | TMDB/IMDb/Wikidata ID mappings, film/TV models | Versioned identity/provenance adapter | No existing IDs broken, migrations additive |
| 2.1 | P0 | JSON planner schema + validation | Actual planner, `specs/movie_query.schema.json` | Pydantic schema, normalized query planner, legacy adapter | Invalid plans rejected; no raw SQL |
| 2.2 | P0 | Shared hard filter/search semantics | FTS, movie/TV SQL, UI filters, recommendation routes | Unified AND/OR filters, stable ranking | 100% curated filter conformity |
| 3.1 | P0 | Retrieval benchmarking | `retrieve.py`, index definitions, text sources | Baseline + hybrid fusion/rerank comparison | Held-out improvement, acceptable p95 |
| 3.2 | P0 | Chunk/index/embedding experiment | `movies.db`, `wiki.db`, `tools/embed_ft.py` | Shadow index experiments, findings | No live swap unless gate won |
| 3.3 | P0 | Article retrieval and evidence | Existing Guardian/NYT/ingesters, `NEWS=OFF` | Versioned article adapter, provenance and TTLs | No news switch enabled without new gate |
| 3.4 | P0 | Semantic caching + answer context | Existing `SEMCACHE`, `CTX_BUILDER_V=3`, claim/guard switches | Cache versioning/invalidation, prompt/evidence tests | No cross-intent/user hit; grounded answer tests |
| 4.1 | P0 | Box-office baseline preservation | `performance.py`, `pf5_data.py`, `pf5_model.py`, result files | Reproduced pf4/pf5, missing test list | No holdout contamination |
| 4.2 | P0 | Point-in-time feature engineering | Release history, people/genre/budget/media sources | As-of features, provenance and missingness | Leakage probes pass |
| 4.3 | P0 | Factor/correlation report | Historical films/training splits | Release/director/cast/creator/producer/type analyses | Confidence intervals; no causal claim |
| 4.4 | P0 | Gross forecast + interval calibration | Models, budget, region, temporal targets | Reconciled gross predictions, gross/budget intervals, model card | Holdout accuracy and 80% calibration/width |
| 4.5 | P0 | Forecast API/panel | Current movie pages, `PERF=0` | Off-by-default experimental UI | Hidden unless gate and authorization pass |
| 5.1 | P0 | Recommendation event semantics | History/watchlist/imports and privacy | Exposure/choice/ratings contract | Consent/isolation tests |
| 5.2 | P0 | Candidate gen / ranking / explanations | Existing rec functions, current Home/Pick Tonight | Content-based + popularity baseline, score API | Chronological offline benchmark and filter pass |
| 5.3 | P0 | Hybrid/feedback improvements | Feature store, user feedback | Reason codes, diversity, retrain process | Model win vs baseline; no score-as-probability |
| 6.1 | P1 | Site-wide UX tokens & patterns | `web/v8`, existing styles, screenshot reference | Motion/easing, focus and layout standards | WCAG/browsers/reduced motion |
| 6.2 | P1 | Pick Tonight | Current UI, ranking API, tests | One explanation per title, better reveal/group fit | No duplicated reasons; hard limits pass |
| 6.3 | P1 | Calendar | Current week/month, TVmaze/timezone logic | Date/state preservation, hierarchy, mobile | Existing calendar features stay functional |
| 7.1 | P1 | Chat concurrency | Ollama, GPU, FastAPI concurrency, nightly | Fair queues, cancellation, load report | No cross-stream bleed; p95 within budget |
| 7.2 | P1 | Tool router | JSON planner, Ollama native tool support | Allowlisted typed tool service | Auth/timeout/prompt-injection tests |
| 8.1 | P2 | Site internationalization | Strings/assets/messages, locales, movie aliases | Language packs, fallback, RTL readiness | E2E locale tests |
| 8.2 | P2 | Moviegoing promos and pricing semantics | Theater source terms, region/showtime feeds | Verified weekday/bundle/member offers | Never infer unverified checkout prices |
| 8.3 | P2 | Streaming offers & alerts | TMDB/watch providers, current alerts | Region-aware availability and optional alerts | Licenses/attribution/TTL and opt-in |

## Required outputs at each package

- Exact source locations touched (after repo inspection); implemented API/schema version; rollback.
- A predeclared rule and before/after measurement when quantifiable.
- Unit, integration, adversarial and UX coverage relevant to the change.
- `decision_log`/issue log update, provenance and `git diff --stat`.
- Feature flag off-by-default until relevant acceptance and approval.
- Low-cost estimates for storage/API calls/GPU demand when relevant.

## Never reorder these dependencies

- Do not deploy recommendation-driven UI before validated hard filters.
- Do not promote a forecast without point-in-time features and calibrated held-out ranges.
- Do not enable news answering solely because articles were fetched.
- Do not expose per-user memories in shared caches or instrumentation.
- Do not ship deals without verified official terms and an explicit unknown-price state.
- Do not use an untouched sealed set in tuning, including indirect prompt selection.

## User approvals required

Explicit user authorization for production restarts/deployments, paid API/data agreements, emails/push to real users, irreversible DB migration, running the one-shot sealed evaluation, external data distribution, and operations on shared services/reverse proxy. Ordinary local reversible code/tests can proceed per the master prompt.
