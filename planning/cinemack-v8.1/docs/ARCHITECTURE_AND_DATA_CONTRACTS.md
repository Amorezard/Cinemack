# Proposed architecture and data contracts (adapt after auditing actual repository)

## Separation of concerns

```text
Browser (Home / Chat / Pick Tonight / Calendar / Watchlist / Profile)
              |
          FastAPI auth + request trace
              |
        Planner (typed intent + filter JSON)
              |
      Deterministic allowlisted router
              |
+-----------------------+-------------------+-----------------------+
| SQL / FTS / vector    | Articles / cache  | User preferences      |
| film facts & indexes  | source-grounded   | scoped & authorized   |
+-----------------------+-------------------+-----------------------+
              |                         |
       Evidence builder         Recommendation candidates
              |                         |
       Grounded generation        Hard filters -> rank -> reasons
              |                         |
           Streaming answer / typed movie cards

Box office path: point-in-time facts -> features -> validated forecast
                         -> API/experiment panel (off by default)

Cross-cutting: provenance, source permissions, authz, locale, monitoring,
cache versions, safe migrations, accessibility, backups and rollback.
```

The current README documents some of these pieces, but **this is a target boundary diagram**, not an assertion about actual deployed modules.

## Contract 1: Movie query (`specs/movie_query.schema.json`)

- `schema_version`: start at `1`, migrate with backward compatibility.
- `intent`: `identify`, `facts`, `compare`, `reception`, `recommend`, `availability`, `news`, `forecast`.
- `media_types`: movie, tv or both. Resolve entity type when ambiguous.
- `query_text`: clean semantic query; not a SQL fragment.
- `entity`: known internal/TMDB/IMDb keys; optional.
- `filters`: year, runtime, genre ANY/ALL/EXCLUDE, region, providers ANY, monetization types, watched exclusion, include/exclude title IDs.
- `retrieval.routes`: allowlisted `sql`, `fts`, `vector`, `articles`, `recommendations`; do not blindly run all routes.
- `response`: language, evidence and reason requirements.
- **Important:** filter values coming from JSON must be normalized against known provider IDs and query operation enums; two apparently equal query embeddings with different filters must not share a cache response.

Example: science-fiction movie after 2015, under 120 minutes, Netflix OR Hulu, US region → year_min=2016, runtime_max=120, providers_any=[...], media_types=[movie]. Runtime cap means `<= 120`, and 'after 2015' means `>= 2016`.

## Contract 2: Prediction (`specs/prediction_response.schema.json`)

Return `prediction_id`, `media_id`, `model_version`, prediction horizon, `as_of`, base currency, budget amount/source/cutoff, domestic/international/worldwide gross estimates, nominal 50% and 80% intervals, worldwide gross-to-budget multiple, metadata caveats. Nullable amounts are *unknown*, not zero. Never serialize an unearned 'profit' field.

Recommended invariants (validate programmatically):

1. If domestic, international, worldwide estimates are all present, `domestic + international ≈ worldwide` within an explicitly declared rounding tolerance.
2. For nonnegative gross, each interval is nonnegative and `lower <= point <= upper` when the point is asserted to lie within it; 50% interval lies inside 80% interval if nested method is used.
3. Gross/budget multiple calculated only when trustworthy strictly positive budget value exists and units match; if not, use null.
4. Features, predictions and model evaluations include `as_of` and feature-availability timestamps; test against leakage.
5. Calibrated interval coverage is a statistical property of evaluated sample groups, **not a guarantee** for a single movie.
6. Never present the gross-to-budget multiple as net profit/ROI.
7. Prediction horizon and observed post-release actuals cannot be silently mixed.
8. Distinguish `experimental`, `validated`, `disabled` statuses; public display requires feature flag and validation.

## Contract 3: Recommendation event (`specs/recommendation_event.schema.json`)

A recommendation exposure records user/session pseudonymous IDs where authorized, `media_id`, `placement`, candidate/ranking version, `position`, timestamps, what hard filters applied, and event type; optional strength for explicit ratings. The client must NOT be trusted to assert another user's ID. Server derives identity from session. Protect retention/consent. Do not store full private chat prompts simply to measure ranking.

Events differ: `impression` (shown), `click`, `watchlist_add`, `watchlist_remove`, `watch_started`, `watch_completed`, `rating_positive`, `rating_negative`, `not_interested`, `pick_tonight_selected`, `already_watched`, `more_like_this`. A watch-start is not proof of liking; a non-click is not explicit dislike.

## Contract 4: Deal offer (`specs/deal_offer.schema.json`)

- `offer_type`: discount_day, bundle, loyalty, subscription, student, matinee, streaming_rental, streaming_subscription, other.
- `provider`, optional `location_ids`, region and local eligible weekdays/date/time window.
- eligibility details, exclusions, premium format limits, taxes/fees caveat, advertised price or discount only if verified.
- official `source_url`, source provider, `last_verified_at`, `valid_from`, `valid_until`, `verification_status` and `price_status`.
- Distinguish offer terms from actual showtime/ticket availability, which may be **unknown**. Never invent prices.

## Recommended persistence design (not an instruction to create these tables blindly)

1. `media_identity` and `media_external_ids` as additive adapters; mapping quality and source precedence.
2. `source_observation` tracking license class/source URL/fetched-at/published-at/index version.
3. `search_trace` aggregated performance (redacted/no user PII by default).
4. `retrieval_cache` keyed by normalized intent, entities, filters, locale, region, visibility scope, dataset version and embedding version.
5. `recommendation_event` and `recommendation_model_version`, privacy controls.
6. `boxoffice_feature_snapshot`, `boxoffice_forecast`, `boxoffice_observed_actual`, `boxoffice_model_card`.
7. `deal_offer` and `deal_offer_verification` with expiration.

Before every DB change: find equivalent existing tables, migration compatibility, number of rows and read/write load; prefer not duplicating old data.

## Request flow invariants

- Sensitive scoped requests require server-side authz before SQL/cache/tool execution.
- Caches cannot cross user, scope, region, language, hard filters, or index/model version.
- All outside text is untrusted and never grants new tool capabilities.
- Every card ID points to the same canonical entity shown in answer text.
- Locale changes must not change hard query filters or known entity IDs.
- A stale source should never be represented as confirmed live availability.
- Recommendation reason text must be derived from ranking evidence, not fabricated by LLM.
