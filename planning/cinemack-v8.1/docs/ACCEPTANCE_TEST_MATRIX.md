# Acceptance test matrix — draft to reconcile with actual Cinemack tests

**Important:** All rows are required *planned tests*, not tests run or passed. Before each experiment, map these to existing `tests/`, `docs/validation_matrix.md`, decision logs and browser tests. Favor current tests when equivalent. Do not consume sealed sets to tune.

## A. Baseline, legacy compatibility and DB integrity

| ID | Scenario | Success evidence |
|---|---|---|
| A01 | Launch v8 with existing flags | Legacy current behavior captured; baseline smoke passed |
| A02 | Launch v8 mock fixture on `PORT=8765` | Home/Chat/Pick Tonight/Calendar/Watchlist/Profile render |
| A03 | Preserve classic route at `/classic` | No regression to access and critical functionality |
| A04 | Existing `start_site.sh` with test `PORT=49152` | Starts only with permission, does not interrupt live port 49151 |
| A05 | `nightly.sh --dry` | No mutation or external send; expected tasks listed |
| A06 | DB migration rehearsal on copy | Referential integrity, row counts, schema and rollback verified |
| A07 | Read-only DB schema audit | Existing indexes/FTS/WAL/page counts documented; no secrets/private rows copied |
| A08 | Feature flag defaults | All new features disabled until gated; old flags unchanged |
| A09 | Existing pending experiments | Status of MEM_CARDS, CLAIM_REWRITE, CTX_BUILDER, GUARD2, embedder evaluated without assuming a pass |
| A10 | Sealed set handling | No sealed contents read or consumed for tuning; hashes unchanged |

## B. Canonical film identities, queries, RAG and caching

| ID | Scenario | Success evidence |
|---|---|---|
| S01 | Movie and TV share title | User intent routes to correct media type; no silent collision |
| S02 | Localized titles and aliases | Correct movie ID with explicit ambiguity handling |
| S03 | Invented title | Honest no-match response, no fabricated attribution |
| S04 | Film has no IMDb mapping | Graceful fallback and provenance indicator |
| S05 | Duplicate source records | Correct canonical mapping, no duplicate recommendations |
| S06 | Query JSON unknown field/injection | Validation fails safely; no generated SQL execution |
| S07 | Runtime `<=120` / year `>=2016` | Every recommended candidate satisfies filters |
| S08 | Provider Netflix OR Hulu | OR semantics, correct locale and monetization, unverified represented as unknown |
| S09 | Genres Any versus All | Correct set operations and stable ordering |
| S10 | All genres selected | Does not produce random reordered results or unexpected restrictions |
| S11 | Missing genre metadata | Consistent unknown handling without false hard-match |
| S12 | Include/exclude watched | Works with account-specific history and privacy |
| S13 | Ranking tie break | Stable deterministic results and pagination |
| S14 | Very long chat with early limits | Early hard limits preserved and recoverable from summaries |
| S15 | Compare exact-title, FTS and semantic retrieval | Held-out recall/ranking improvement measured versus baseline |
| S16 | Reranking high latency | Candidate/time budget enforced, safe non-reranked fallback |
| S17 | Source chunk continuity | Parent-child context prevents truncating important evidence |
| S18 | Source/citation provenance | Every source link/source ID maps to retrievable evidence record |
| S19 | News article is adversarial | Prompt injection does not alter tool policy or leak secrets |
| S20 | RAG asks about recent article | Uses permissible fresh/cached source, otherwise admits uncertainty |
| S21 | Unrelated similar-semantic queries | No unsafe cache reuse with incompatible filters/negation |
| S22 | Same query, different user | No leakage of private history, preferences, citations or answer |
| S23 | Source update / embed model version changes | Correct cache invalidation |
| S24 | Stale provider/news result | TTL obeyed and staleness accurately indicated |
| S25 | Existing `NEWS=off` | Remains off until its independent quality gate passes |
| S26 | Existing shadow fine-tune | Never becomes live solely because training ran |

## C. Personalized recommendations

| ID | Scenario | Success evidence |
|---|---|---|
| R01 | New account with no preferences | Useful popularity/content cold start without fake personalized certainty |
| R02 | Explicit disliked genre/title | Correct exclusion or strong penalty, hard limits maintained |
| R03 | User watches film but did not rate | Not automatically treated as positive feedback |
| R04 | Impression with no click | Not mislabeled as explicit negative feedback |
| R05 | `not_interested` feedback | Session re-ranking updates without corrupting permanent history |
| R06 | More like this | Similarity reflects media features; no repeated same title |
| R07 | Multi-genre selected | Candidate set filtered before ranking; no random genre leakage |
| R08 | Exclude previously watched | Correct across allowed imports and local watching history |
| R09 | A user and B user rank concurrently | No cross-profile history or recommendations |
| R10 | Group Pick Tonight | All applicable user constraints and consent respected |
| R11 | Recommendation reason | One traceable reason per displayed title, no fabricated score probability |
| R12 | Offline benchmark | Chronological NDCG/Recall, diversity, coverage vs current/popularity/content baselines |
| R13 | Position/exposure logging | Candidate, position, timestamp, ranking version, consent correctly recorded |
| R14 | Cold and warm cache behavior | Validated latency + correct invalidation after feedback |

## D. Box-office model and forecast reporting

| ID | Scenario | Success evidence |
|---|---|---|
| F01 | Reproduce pf4 baseline | Same target, data split, preprocessing and metric; deviations explained |
| F02 | Inspect pf5 experiments | Previous A/C/D results preserved; B missingness documented |
| F03 | Historic `as_of` validation | Every feature timestamp available before its forecast cutoff |
| F04 | Pre-release scores and reviews | Post-release critics/ratings excluded from early forecasts |
| F05 | People track records | Cast/director/producer prior films only; no future credits leakage |
| F06 | Release timing | Month/holiday/competition features point-in-time correct |
| F07 | Genre/type interactions | Specified hypotheses, effect sizes, intervals, correction for multiple tests |
| F08 | Franchise/marketing factors | Missingness and forecast horizon handled explicitly |
| F09 | Budget missing or unreliable | Gross/budget multiple null, not fake 0 or inferred profit |
| F10 | Forecast components | Domestic + international reconcile to worldwide when all available |
| F11 | Currency/region consistency | Currency units, territories and exchange assumptions documented |
| F12 | 50%/80% uncertainty | Ordered nested intervals where defined; no negative gross values |
| F13 | 80% calibration gate | On untouched chronological holdout, report coverage (reference 75–85%) AND interval width |
| F14 | Subgroup errors | Budget tier, franchise, genre, release era and forecast horizon reported |
| F15 | Factor ablation | Each group benchmarked vs pf4 and stronger competitors with uncertainty |
| F16 | Backtest `d90`, `d7`, opening weekend | Separate evaluations, cutoff labels, no comparison mixing |
| F17 | Predicted vs actual | Forecast snapshot immutable, later actual appended, no retrospective overwrite |
| F18 | Prediction API/UI | Feature-flagged, model version and as-of visible, no fake profit claims |
| F19 | Unvalidated model gate | Public prediction panel remains hidden |
| F20 | Leakage controls | Predefined synthetic leak seeded then rejected by integrity tests |

## E. UX, browser, accessibility and performance

| ID | Scenario | Success evidence |
|---|---|---|
| U01 | Poster skeleton on slow connection | Stable aspect ratio, no unexpected layout shift |
| U02 | Rapid genre toggles | Immediate chip feedback, stale results never overwrite latest |
| U03 | Toggle Any/All and select all | Stable filtered set, understandable UI |
| U04 | Save/unsave watchlist | Immediate feedback, persistence after refresh, rollback on failure |
| U05 | One 'Why I Picked This' | Exactly one block per result in Pick Tonight |
| U06 | Pick Tonight reroll | Existing mood/time/genres persisted |
| U07 | Pick Tonight empty eligible set | Honest explanation, actionable relax-filter choice |
| U08 | Calendar week ↔ month | Selected date and active filters persist |
| U09 | Calendar TV times across zones | User timezone correctly applied, DST edge cases tested |
| U10 | Missing/tentative release date | Explicit uncertainty, no invented release |
| U11 | Mobile widths/touch targets | No overflow, hidden controls, or unreachable targets |
| U12 | Keyboard navigation | Focus visible, controls reachable, dialogs trap/restore focus where necessary |
| U13 | `prefers-reduced-motion` | Nonessential motion disabled |
| U14 | Dynamic content announced | Relevant save/error/loading states accessible to screen readers |
| U15 | Component contrast/zoom | WCAG 2.2 AA test suite and manual check |
| U16 | Concurrent image loads | Home cards do not shift, carousels remain interactive |
| U17 | Browser regressions | Current `accept_redesign.js` + page-specific tests continue passing |
| U18 | Web Vitals | Field p75 thresholds measured when field instrumentation exists; lab budgets tracked separately |

## F. Multiuser concurrency, tools, security

| ID | Scenario | Success evidence |
|---|---|---|
| C01 | 1,2,4,8 concurrent streams | p50/p95 TTFT, total latency, queue, VRAM, errors recorded |
| C02 | Two distinct logged-in users | No response mixing, memory leakage, wrong watchlist mutations |
| C03 | User cancels a slow request | Resources released/queue slot freed, no stray UI output |
| C04 | Nightly GPU job collides with chat | Priority scheduling/fairness behavior measured |
| C05 | Too many requests | Bounded queue, rate limits, honest overload state |
| C06 | Tool asks arbitrary URL/shell/SQL | Rejected by allowlist and parameter validation |
| C07 | Tool asks another user's private watchlist | Denied by server-side authorization |
| C08 | RAG indirect prompt injection | Treated as data, cannot alter tools, secrets or user auth |
| C09 | Model answer includes HTML/JS | Safe rendering/sanitization; no XSS |
| C10 | API latency regression | Budget and fallback validated; unchanged fast paths when possible |
| C11 | Upload/banner handling | Content type, size, and storage safety verified |
| C12 | Email sandbox | No real email sent unless user explicitly authorizes send mode |
| C13 | HTTPS readiness | Launch blocking risk documented; no unauthorised reverse-proxy changes |

## G. P2 localization, offers, licensing

| ID | Scenario | Success evidence |
|---|---|---|
| L01 | Spanish/French/Portuguese UI | Labels, forms, errors and notifications translated with fallback |
| L02 | Movie alias foreign title | Correct film ID, hard filters unchanged |
| L03 | Region and locale differ | Region-specific release/availability and locale text handled separately |
| L04 | Streaming unavailable/unknown | No false availability assertion |
| L05 | RTL/long translated strings | Layout readable and accessible |
| D01 | Official Tuesday offer exists | Terms and source checked; eligibility/locations verified |
| D02 | Monday concession deal vs ticket deal | Explicit offer type, not falsely labeled ticket discount |
| D03 | Ticket pack/membership | Price/benefits only with allowed authoritative source |
| D04 | Student or restricted discount | Eligibility and exclusions clearly shown |
| D05 | Checkout price not accessible | `price_status=unknown`; no fabricated savings |
| D06 | Expired or disputed promotion | Removed from active results, history and verification retained |
| D07 | Local offer changes region/theater | Recomputed not reused from another location's cache |
| D08 | Watchlist alerts | Opt-in preferences, dedup, unsubscribe, sandbox defaults |
| D09 | Attribution/license audit | Provider terms and data use documented before integration |

## Reporting template for test runs

For each ID include: `test_id`, baseline behavior, flag, environment/hardware, exact command, fixture/holdout version, expected criterion (written before run), result, before/after metrics, status `PASS|FAIL|BLOCKED|UNRUN`, source/commit and rollback action. Never use `PASS` for an unrun test.
