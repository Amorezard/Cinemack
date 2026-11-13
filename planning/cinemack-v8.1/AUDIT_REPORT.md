# Cinemack v8.1 - audit report (package 0)

Written Thu 12 Nov 2026 from round 40 (read-only; 19:35 EST) and the source snapshot it returned.
Labels: **MEASURED** = seen in round 40's output; **CODE** = read in the source at commit 929bbbf;
**README** = stated in docs/README.md (11 Nov) and not checked here; **UNKNOWN** = not checked yet.
Nothing in this report was changed on the server. Raw outputs: `audit_raw/`.

## 1. State of the repository (MEASURED)
- Folder `~/movie-buff/rag`, branch `v8`, head `929bbbf` "Round 39" (12 Nov 10:18), 69 commits, tag `v8.0-lock` on the round-35 commit. No other worktree, no stash.
- One tracked file has uncommitted edits: `docs/daily_progress.md`. 300 untracked files, almost all `archive/pre_r*` backups and `docs/audit` screenshots.
- 854 tracked files; 645 copied into the snapshot (64 MB). Left out: sealed sets, results named sealed/final, runs/, logs/, databases, 100 files over 1.5 MB (old `results/stage_scores_*.json`, screenshots).
- All seven `sets/sealed_v8_*.json` match `results/sealed_v8_sets.md5` (hash compare only; contents not read).
- The live site answered: `/v8/app.css` 200 in 15 ms, `/api/v8/build` 200, `/classic` 200, `/v8/` 307. No Traceback or Error line in the last 400 lines of the site window.

## 2. Machine (MEASURED)
- 64 cores (Xeon Gold 6346), 377 GB memory, 2.5 TB free disk. Python 3.12.3, node 18.19.1, SQLite 3.45.1, Ollama 0.32.14.
- One NVIDIA A10, 23 GB. During the audit another person's job (`vasp_std`) held 16.2 GB at 99-100%. The `movie-buff` model is 17 GB, so it cannot sit fully on the card while that job runs (the 10 Nov measurement, first token ~38 s, was taken in the same situation).
- Ollama on this machine is shared: 20 models, several belonging to other projects. Any change to `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS` or `OLLAMA_MAX_QUEUE` affects those users and needs the owner's yes. No `OLLAMA_*` variable is set in the project shell.
- `tools/node_modules` is empty on the server, so the browser checks (`tools/accept_redesign.js`, axe-core) cannot run there; they run on the laptop or in a workspace.
- Installed and relevant: lightgbm 4.7.0, scikit-learn 1.9.1, numpy 2.5.3, pandas 3.0.5, scipy 1.18.1, pydantic 2.13.5, sqlite-vec 0.1.9, sentence-transformers 6.1.0, torch 2.14.0, fastapi 0.141.1. Not installed: pytest, jsonschema, shap, statsmodels.

## 3. What the kit's README says, what the code and the server show

| Topic | README (11 Nov) | Code / measured (12 Nov) | Decision |
|---|---|---|---|
| Live switches | MEM_CARDS, GUARD2 "built, off" | `site.env` has MEM_CARDS=1 (round 33), GUARD2=1 (round 34, sealed gate recorded as FAILED), plus REFILL=1, TV_TASTE=1, GENRE_MAIN=2 not in the README | README is three rounds old; `site.env` is the source of truth. Do not rebuild these features. |
| Off switches | CLAIM_REWRITE, PERSONA, CTX_BUILDER_V=3 off | Not in any lock file; v7 lock has CTX_BUILDER=0; decision log: persona and context v3 FAILED their rules on 11 Nov | Stay off. |
| NEWS, PERF | off | `config.yaml` NEWS: 0, PERF: 0; v7 lock PERF=0 | Stay off. |
| Embedder | mxbai live, fine-tune shadow | `tools/embed_ft.py` writes only `logs/embed_ft/`; issue log / status notes: fine-tune not run (GPU out of memory) | Shadow only; gate unrun. |
| Databases | facts.db ~8.8 GB | facts.db 9,385 MB (WAL, 114 tables), movies.db 404 MB (33,037 films, sqlite-vec), wiki.db 2,107 MB (173,210 chunks), wiki_pfts.db 154 MB, logs/tvmaze.db 218 MB | Appendix D. |
| People's data | per-person databases | 3 accounts, 15 saved chats, 20 film preferences, 97 stated preferences, 0 watch events, 1 review, 57 like-vectors | See finding F3. |
| Box office | target log(gross/budget), pf4 MAE 1.200 | `performance.py` (LightGBM), `pf5_model.py`; `results/performance_summary.txt` says 1.200 on 1,487 test films, 8,742 training films | Reproduction UNRUN (round 41). |
| Box-office money data | - | `money(tmdb_id, budget, gross, roi, ...)` holds ONE gross per film (worldwide). No domestic or international column anywhere in the definitions | See finding F1. |
| Tests | `python -m unittest tests.test_v8_r27` | 28 modules, 27 OK (255 tests), 18 s | See finding F2. |
| Nightly | cron 04:17 | one cron line for the project (MEASURED); `nightly.sh --dry` not run yet | Run the dry mode in round 41 after reading the script (CODE: dry mode only echoes). |
| Answer cache | SEMCACHE on | `web/semcache.py`: single-turn questions only, cosine >= 0.95 plus same names / numbers / content words, rows matched on a settings tag (`utag`); `web/site_cache.py` keys include a code / settings fingerprint | Two-user and different-filter reuse tests: UNRUN (package 3). |
| Planner | planner -> retrieval | `retrieve.understand()` (model call) -> `_clean_plan()`; filters also parsed by rules in `retrieve.parse_filters()`; `passes()` applies them; site limits in `web/user_prefs.excluded()` | Two filter paths exist (finding F4). |
| Recommendations | Home, Pick Tonight | `personal.py` (content-based taste profile + MovieLens neighbours), `web/discovery.because_you_liked()`, `web/tvdata.pick` (TV, no model), `rec_clean.py`, `like_filter.py` | One shared ranking interface does not exist yet (package 5). |
| Thread safety | - | `web/dbsafe.py` wraps shared SQLite connections with a lock per connection (10 Nov) | Keep; load test UNRUN. |
| HTTPS | not done | not checked | UNKNOWN; ports 80/443 belong to another service. |

## 4. Findings that change the plan

**F1. Domestic and international grosses are not in the data.** The kit asks for domestic, international and worldwide forecasts that reconcile. The server has one worldwide gross per film (TMDB revenue, Wikipedia infobox, Wikidata; conflicts kept). Without a licensed or openly reusable source of territory grosses, the domestic and international outputs are BLOCKED and must be returned as unknown (null), which the kit's own contract allows. Round 41 counts what the `facts` table holds per source to confirm. One open route exists: Wikidata (CC0) stores box-office figures with a "valid in place" qualifier, and `fetch_wikidata.py` line 114 currently throws the place-qualified (single-country) figures away to keep worldwide ones. Slice 4.5 measures how many films have a United States / North America figure there before anything is built on it. Scraping Box Office Mojo or The Numbers is not allowed by their terms; a paid source needs the user's decision.

**F2. Six tests never run.** `tests/test_v7_oct30_fixes.py` contains 6 pytest-style functions (module-level `setup_module`), so `python -m unittest` collects nothing ("NO TESTS RAN", exit 5 on Python 3.12). Every earlier round's loop only looked for the word FAILED, so this went unseen. pytest is not installed. Fix (small, safe): wrap them in a `unittest.TestCase` on the v8.1 branch.

**F3. There is almost no usage data of Cinemack's own.** 3 accounts and 0 watch events cannot train or test a recommender. The offline benchmark the kit requires (time-ordered Recall@10 / NDCG@10 against popularity and content baselines) has to use MovieLens histories (already on the server: `ml32_stats`, `ml_neighbours` 82,200 rows, tag genome), and the report has to say that it measures the method on MovieLens users, not on Cinemack users. Collaborative filtering from Cinemack's own events is BLOCKED on volume.

**F4. Hard limits are enforced in more than one place.** Chat: `retrieve.parse_filters` + `passes` + `backfill`; website: `user_prefs.excluded` / `filter_hits` / `refill`; Home: `discovery.genre_filter` / `match_genres`; TV picks: `tvdata.pick`. Package 2 (one validated query plan compiled to parameterised SQL) should sit in front of these as an adapter and be compared against each on a curated set before anything is replaced.

**F5. The GPU decides what can be measured.** With `vasp_std` on the card, model-based runs take 58-109 s per answer (10 Nov). CPU-only work (box office, recommendation benchmark, filter conformance, cache tests, front-end tests on the mock site) is not affected and is scheduled first.

**F6. The live folder is also the development folder today.** Every round so far edited `~/movie-buff/rag`, which the site serves from. v8.1 work moves to a second worktree (`~/movie-buff/rag-v81`, branch `v8.1`) with its own copy of `facts.db` (set through the existing `FACTS_DB` variable, CODE: `facts_db.py`), so nothing a v8.1 run writes can reach the live data.

## 5. Risks and permission questions
- Shared GPU and shared Ollama (above). Load tests at 1/2/4/8 users load other people's jobs too: ask before running, and run at night.
- No HTTPS: passwords and session cookies travel in clear text on port 49151. A launch blocker in the kit; fixing it needs the server owner.
- Guardian key allows 500 calls a day; pf5 B training coverage was 953 of 9,346 films on 11 Nov (decision log). It cannot finish this week.
- Licences to re-check before packages 8-9: TMDB (non-commercial, attribution), TVmaze (CC BY-SA), watch-provider data (JustWatch attribution through TMDB), any theatre-offer source.
- Judge agreement with the second rater was 16/30; claims in v8.1 reports rest on deterministic checks, as in v8.

## 6. Open questions with defaults
1. Territory grosses (F1): default = report worldwide only, domestic / international `null` with status `unavailable`.
2. Load test on the shared GPU: default = not run until the user says when.
3. Languages for package 8: default = Spanish first (strings only), others after.
4. Deals (package 9): default = schema, unknown-price states and tests only; no real offers entered until an official source and its terms are named.

## Appendix A. Endpoints found in the code (82)

| Path | Method | File:line | Function |
|---|---|---|---|
| `/` | GET | web/server.py:212 | index |
| `/about` | GET | web/server.py:229 | about |
| `/api/account/delete` | POST | web/server.py:493 | account_delete |
| `/api/chat` | POST | web/server.py:640 | chat |
| `/api/chats` | GET | web/server.py:369 | chat_list |
| `/api/chats/{conv_id}` | DELETE | web/server.py:388 | chat_delete |
| `/api/chats/{conv_id}` | GET | web/server.py:377 | chat_open |
| `/api/feedback` | POST | web/server.py:292 | feedback |
| `/api/film` | POST | web/server.py:316 | film_pref |
| `/api/film/{fid}` | GET | web/server.py:340 | film_detail |
| `/api/health` | GET | web/server.py:629 | health |
| `/api/import/letterboxd` | POST | web/server.py:562 | import_letterboxd |
| `/api/login` | POST | web/server.py:467 | login |
| `/api/logout` | POST | web/server.py:481 | logout |
| `/api/me` | GET | web/server.py:428 | me |
| `/api/memory` | DELETE | web/server.py:621 | memory_clear |
| `/api/memory` | GET | web/server.py:588 | memory_list |
| `/api/memory/card/{conv_id}` | DELETE | web/server.py:614 | memory_card_delete |
| `/api/memory/{note_id}` | DELETE | web/server.py:607 | memory_delete |
| `/api/memory/{note_id}` | PATCH | web/server.py:600 | memory_edit |
| `/api/onboarding` | GET | web/server.py:525 | onboarding_get |
| `/api/onboarding` | POST | web/server.py:543 | onboarding_save |
| `/api/settings` | POST | web/server.py:514 | settings_save |
| `/api/signup` | POST | web/server.py:442 | signup |
| `/api/stats` | GET | web/server.py:246 | stats |
| `/api/taste` | GET | web/server.py:358 | taste |
| `/api/v8/alerts` | GET | web/v8routes.py:220 | v8_alerts |
| `/api/v8/alerts/mute` | POST | web/v8extra.py:669 | v8_mute |
| `/api/v8/alerts/seen` | POST | web/v8routes.py:226 | v8_alerts_seen |
| `/api/v8/banner` | DELETE | web/v8extra.py:759 | v8_banner_del |
| `/api/v8/banner` | GET | web/v8extra.py:765 | v8_banner_get |
| `/api/v8/banner` | POST | web/v8extra.py:750 | v8_banner_up |
| `/api/v8/build` | GET | web/v8routes.py:148 | v8_build |
| `/api/v8/calendar` | GET | web/v8routes.py:212 | v8_calendar |
| `/api/v8/chats/rename` | POST | web/v8extra.py:685 | v8_rename |
| `/api/v8/chips` | GET | web/v8extra.py:635 | v8_chips |
| `/api/v8/continue` | GET | web/v8extra.py:646 | v8_continue |
| `/api/v8/email` | DELETE | web/v8extra.py:730 | v8_email_del |
| `/api/v8/email` | GET | web/v8extra.py:704 | v8_email |
| `/api/v8/email` | POST | web/v8extra.py:710 | v8_email_set |
| `/api/v8/email/digest` | POST | web/v8extra.py:724 | v8_email_digest |
| `/api/v8/email/verify` | POST | web/v8extra.py:717 | v8_email_verify |
| `/api/v8/friend/{other}` | GET | web/v8routes.py:326 | v8_friend_view |
| `/api/v8/friends/request` | POST | web/v8routes.py:296 | v8_friend_request |
| `/api/v8/friends/{action}` | POST | web/v8routes.py:306 | v8_friend_action |
| `/api/v8/hide` | POST | web/v8routes.py:285 | v8_hide |
| `/api/v8/history/clear` | POST | web/v8routes.py:395 | v8_history_clear |
| `/api/v8/home` | GET | web/v8routes.py:166 | v8_home |
| `/api/v8/import` | POST | web/v8routes.py:376 | v8_import |
| `/api/v8/me/summary` | GET | web/v8extra.py:776 | v8_summary |
| `/api/v8/onboarding/tv` | GET | web/tv_prefs.py:324 | onboarding_tv |
| `/api/v8/onboarding/tv/groups` | POST | web/tv_prefs.py:333 | onboarding_tv_groups |
| `/api/v8/pick/tv` | GET | web/v8extra.py:621 | v8_pick_tv |
| `/api/v8/privacy` | POST | web/v8routes.py:275 | v8_privacy |
| `/api/v8/profile` | GET | web/v8routes.py:251 | v8_profile |
| `/api/v8/review` | POST | web/v8routes.py:352 | v8_review |
| `/api/v8/reviews/friends` | GET | web/reviews_feed.py:57 | reviews_friends |
| `/api/v8/search` | GET | web/v8extra.py:679 | v8_search |
| `/api/v8/show/{sid}` | GET | web/v8extra.py:608 | v8_show |
| `/api/v8/show/{sid}/season/{n}` | GET | web/v8extra.py:615 | v8_season |
| `/api/v8/similar/{fid}` | GET | web/v8routes.py:206 | v8_similar |
| `/api/v8/theatres/{fid}` | GET | web/v8routes.py:173 | v8_theatres |
| `/api/v8/trakt/device/poll` | POST | web/trakt_sync.py:402 | trakt_device_poll |
| `/api/v8/trakt/device/start` | POST | web/trakt_sync.py:390 | trakt_device_start |
| `/api/v8/trakt/{action}` | POST | web/v8routes.py:429 | trakt_action |
| `/api/v8/tv/choice` | POST | web/tv_prefs.py:342 | tv_choice |
| `/api/v8/tv/follow` | POST | web/v8routes.py:190 | v8_tv_follow |
| `/api/v8/tv/progress` | POST | web/v8extra.py:652 | v8_progress |
| `/api/v8/tv/search` | GET | web/v8routes.py:179 | v8_tv_search |
| `/api/v8/watched` | POST | web/v8routes.py:365 | v8_watched |
| `/api/v8/watchlist` | GET | web/v8routes.py:233 | v8_watchlist |
| `/api/v8/watchlist/overview` | GET | web/v8routes.py:244 | v8_watchlist_overview |
| `/api/v8/watchlist/upcoming` | GET | web/v8extra.py:663 | v8_upcoming |
| `/api/watchlist` | GET | web/server.py:351 | watchlist |
| `/classic` | GET | web/server.py:219 | classic |
| `/email/unsubscribe` | GET | web/v8extra.py:737 | v8_unsub |
| `/how` | GET | web/server.py:224 | how |
| `/static/{name}` | GET | web/server.py:237 | static_file |
| `/trakt/callback` | GET | web/v8routes.py:412 | trakt_callback |
| `/trakt/start` | GET | web/v8routes.py:403 | trakt_start |
| `/v8` | GET | web/v8routes.py:144 | v8_page |
| `/v8/{name}` | GET | web/v8routes.py:159 | v8_asset |

## Appendix B. Environment switches read by the code (194 names)

Name: files that read it. Values come from config.yaml (via mbconfig.apply), the lock files, site.env or the shell.

- `ADAPTER_ASSISTANT_TAG`: train_adapter.py
- `ADAPTER_BASE_GGUF`: adapter_data.py
- `ADAPTER_BASE_HF`: train_adapter.py
- `ADAPTER_DB`: adapter_data.py
- `ADAPTER_KEEP`: adapter_data.py
- `ADAPTER_MASK`: train_adapter.py
- `ADAPTER_MAX_AGE_DAYS`: adapter_data.py
- `ADAPTER_MIN_NEW`: adapter_data.py
- `ADAPTER_MODELFILE`: adapter_data.py
- `ADAPTER_OFFLOAD_EMBED`: train_adapter.py
- `ADAPTER_OPTIM`: train_adapter.py
- `ADAPTER_ORIGINAL`: adapter_data.py
- `ADAPTER_PERSONA`: adapter_data.py
- `ADAPTER_PROBE`: train_adapter.py
- `ADAPTER_TRIM`: train_adapter.py
- `ADAPTER_USER_TAG`: train_adapter.py
- `ALERTS_DB`: web/discovery.py
- `ALIAS_IMDB`: aliases.py
- `ANSWER_MODEL`: adapter_data.py, web/site_cache.py
- `ANSWER_PLOT_OK`: moderation.py
- `ANSWER_ROOM`: context_report.py
- `ANSWER_STRICT`: chat.py
- `ANSWER_STYLE`: chat.py
- `ANSWER_WIKI`: chat.py
- `ANSWER_WIKI_CHARS`: chat.py
- `AWS_DEFAULT_REGION`: fetch_imdb_api.py
- `BANNER_DIR`: web/v8extra.py
- `B_MAIN_MIN`: pf5_model.py
- `CARD_MARGIN`: web/memory.py
- `CHATS_DB`: like_filter.py, web/accounts.py, web/chats.py, web/memory.py
- `CLAIM_REWRITE`: claim_rewrite.py
- `CRITIC_SCORES`: tools.py
- `CTX_BUILDER`: chat.py
- `CTX_BUILDER_V`: chat.py
- `CTX_CHARS`: chat.py
- `CTX_DEDUP_MIN`: chat.py
- `CTX_MIN_SOURCE`: chat.py
- `CTX_MODEL`: chunk_context.py
- `CTX_V3_SKIP_TV`: chat.py
- `CTX_WORKERS`: chunk_context.py
- `DISTILL_MIN_SCORE`: adapter_data.py
- `EMAIL_DB`: web/emailer.py
- `EMAIL_FROM`: web/emailer.py
- `EMAIL_OUTBOX`: web/emailer.py
- `EMAIL_PROVIDER`: web/emailer.py
- `EMAIL_SANDBOX`: web/emailer.py
- `EMBED_FT_BASE`: tools/embed_ft.py
- `EMBED_FT_BATCH`: tools/embed_ft.py
- `EMBED_FT_DIR`: tools/embed_ft.py
- `EMBED_FT_LAYERS`: tools/embed_ft.py
- `EMBED_FT_LR`: tools/embed_ft.py
- `EMBED_FT_N`: tools/embed_ft.py
- `EMBED_MODEL`: add_recent.py, chunk_context.py, evidence.py, like_filter.py, news.py, tv.py ...
- `EVAL_QMODEL`: add_recent.py
- `EVIDENCE_VEC_GUARDIAN`: evidence.py
- `EXTRA_DATA`: tools.py
- `FACTCHECK_WARN`: web/pipeline.py
- `FACTS_DB`: facts_db.py, tools/build_genre_votes.py, tools/fetch_episodes.py, tools/fetch_film_data.py, tools/fetch_home_extra.py, tools/fetch_tvmaze.py ...
- `FAST`: fastpath.py, web/server.py
- `FEEDBACK_DB`: feedback.py
- `FRED_API_KEY`: pf5_data.py
- `GENOME_NPZ`: tools/build_genre_votes.py
- `GENRE_AGREE`: tools.py
- `GENRE_LIVE`: tools/genre_gold.py
- `GENRE_MAIN`: web/genres.py
- `GENRE_VOTES_DB`: tools/build_genre_votes.py, web/genres.py
- `GUARD`: attack_eval.py, chat.py, safety.py, web/server.py
- `GUARD2`: attack_eval.py, safety2.py
- `GUARDIAN_API_KEY`: pf5_data.py
- `GUARD_MODEL`: moderation.py
- `HISTORY_CHARS`: context_report.py, web/pipeline.py
- `HISTORY_DB`: web/history.py
- `HOST`: web/server.py
- `IDENT_HONEST`: chat.py
- `IMDB`: tools.py
- `IMDB_ALIAS_LONG`: aliases.py
- `IMDB_ALIAS_MIN_CONTENT`: aliases.py
- `IRONY_MODEL`: sentiment.py
- `ISSUES`: tools.py
- `JUDGE_MODEL`: attack_eval.py, eval_v3.py, pairwise.py
- `KEEP_ALIVE`: tools.py
- `LIKE_CHECK_MODEL`: like_filter.py
- `LIKE_LLM`: like_filter.py
- `LONG_SUMMARY`: web/longsum.py
- `MAILGUN_BASE`: web/emailer.py
- `MB_CONFIG`: mbconfig.py
- `MB_CONTACT`: aliases.py, fetch_v6_data.py, v7_data.py
- `MB_LIVE`: mbconfig.py
- `MEMORY_DB`: web/accounts.py, web/memory.py
- `MEMORY_MODEL`: web/memory.py
- `MEM_BACK_COS`: web/memory.py
- `MEM_CARDS`: web/memory.py
- `MEM_EMBED`: web/memory.py
- `MEM_EMBED_V`: web/memory.py
- `MEM_MIN_COS`: web/memory.py
- `MOCK`: add_recent.py, chunk_context.py, like_filter.py, mock_llm.py, news.py, news_era.py ...
- `MOCK_CACHE`: mock_llm.py
- `MOCK_DIM`: mock_llm.py
- `MODERATION`: attack_eval.py, moderation.py, web/server.py
- `MONEY_REAL`: tools.py
- `MOVIES_DB`: tools/embed_ft.py, web/discovery.py
- `MOVIE_BUFF_CODE`: tools/chat_bench.py, tools/long_eval.py, web/server.py, web/warm_cache.py
- `MOVIE_BUFF_MOCK`: web/pipeline.py, web/site_cache.py
- `NEWS`: tools.py
- `NEWS_WINDOW_DAYS`: news.py
- `NLI_MIN`: claimcheck.py
- `NLI_MODEL`: claimcheck.py
- `NUM_CTX`: context_report.py
- `NYT_API_KEY`: news.py
- `OLLAMA_URL`: reception_cards.py
- `PEOPLE`: people.py
- `PERF`: tools.py
- `PERSONA`: persona.py
- `PERSONAL`: personal.py
- `PERSONAL_W`: personal.py
- `PERSONA_SYSTEM`: persona.py
- `PLAN_PAST`: web/server.py
- `PORT`: web/server.py, web/warm_cache.py
- `PREF_CHIPS`: web/v8extra.py
- `PRESS_MAX`: pf5_data.py
- `PROMPT_ORDER`: chat.py
- `PROMPT_ORDER_V`: chat.py
- `RECEPTION`: tools.py
- `RECEPTION_K`: tools.py
- `RECEPTION_V`: chat.py
- `REC_CLEAN`: rec_clean.py
- `REC_MIN_VOTES`: rec_clean.py
- `REFILL`: web/user_prefs.py
- `RERANK`: retrieve.py
- `REVIEW_CHECK_MODEL`: review_filter.py
- `REVIEW_FILMS_MAX`: reviews_analyze.py
- `REVIEW_FILTER`: reviews_analyze.py
- `REVIEW_NUM_PREDICT`: reviews_analyze.py
- `REVIEW_QUALITY_DB`: review_filter.py
- `REWRITE_BUDGET_S`: claim_rewrite.py
- `REWRITE_MODEL`: claim_rewrite.py
- `ROUTE_FIX`: tools.py
- `RR_BASE`: train_reranker.py
- `RR_DEVICE`: train_reranker.py
- `RR_OUT`: train_reranker.py
- `RT_UNKNOWN_N`: reception_cards.py
- `SAME_NAME`: tv.py
- `SECTION_CHARS`: tools.py
- `SECTION_N`: tools.py
- `SEMCACHE`: web/semcache.py
- `SEM_MIN`: web/semcache.py
- `SENT_MAX_SENTS`: sentiment.py
- `SENT_MODEL`: sentiment.py
- `SENT_SOURCES`: sentiment.py
- `SHORTS`: chat.py, shorts.py
- `SHORT_MAX_RUNTIME`: shorts.py
- `SHORT_POOL`: shorts.py
- `SITE_CACHE`: web/site_cache.py
- `SITE_URL`: web/emailer.py
- `SMTP_FROM`: web/emailer.py
- `SMTP_PASS`: web/emailer.py
- `SMTP_PORT`: web/emailer.py
- `SMTP_USER`: web/emailer.py
- `SOCIAL_DB`: web/social.py
- `SPEC_MODEL`: tools.py
- `STANCE_MODEL`: news_era.py
- `SUCCESS`: tools.py
- `SUM_MODEL`: reception_cards.py
- `TASTE_GATE`: personal.py
- `TASTE_MAX`: personal.py
- `TMDB_API_KEY`: fetch_credits.py, fetch_tmdb.py, fetch_v6_data.py, tools/fetch_home_extra.py, tools/tmdb_http.py
- `TMDB_EXTRA_DB`: tools/fetch_home_extra.py, web/tmdb_extra.py
- `TMDB_TOKEN`: fetch_tmdb.py, fetch_v6_data.py, tools/tmdb_http.py
- `TOOLS`: chat.py, tools.py
- `TOOL_CHARS`: context_report.py, tools.py
- `TRAKT_CLIENT_ID`: tools/patch_r38.py
- `TRAKT_DB`: web/trakt_sync.py
- `TRAKT_REDIRECT`: web/trakt_sync.py
- `TV`: tv.py
- `TVMAZE_API`: tools/fetch_tvmaze.py
- `TVMAZE_DB`: tools/fetch_tvmaze.py, web/tvmaze.py
- `TVMAZE_GAP`: tools/fetch_tvmaze.py
- `TV_CRIT_SENTS`: tv.py
- `TV_LONG`: tv.py
- `TV_MAX_DISTANCE`: tv.py
- `TV_MIDFILM_MIN_VOTES`: tv.py
- `TV_NAMED_MIN_VOTES`: tv.py
- `TV_REVIEWS`: tv.py
- `TV_SEASONS`: tv.py
- `TV_SHORT`: tv.py
- `TV_TASTE`: web/tv_prefs.py
- `USERS_DB`: personal.py, web/accounts.py, web/film_info.py, web/tv_prefs.py, web/user_prefs.py
- `V8_HOME`: web/server.py
- `VIRTUAL_ENV`: mb.py
- `WIKI_DB`: chunk_context.py, fetch_v6_data.py, tools/embed_ft.py, v7_data.py, web/film_info.py, web/links.py
- `WIKI_UA`: wiki_link.py
- `YOUTUBE_API_KEY`: youtube_analysis.py
- `YT_COMMENTS`: tools.py
- `YT_COMMENTS_N`: youtube_analysis.py

## Appendix C. Unit test modules (round 40, each run on its own)

| Module | Exit | Result |
|---|---|---|
| test_chat_filters | 0 | Ran 11 tests in 0.307s OK |
| test_connectors | 0 | Ran 11 tests in 0.100s OK |
| test_feat_nov4 | 0 | Ran 6 tests in 0.100s OK |
| test_pf4_nov6 | 0 | Ran 4 tests in 0.115s OK |
| test_pf5_data | 0 | Ran 5 tests in 3.533s OK |
| test_r36_genres | 0 | Ran 8 tests in 0.006s OK |
| test_r36_onboarding | 0 | Ran 9 tests in 0.007s OK |
| test_r37_genres | 0 | Ran 5 tests in 0.000s OK |
| test_r37_tv | 0 | Ran 9 tests in 0.025s OK |
| test_r38 | 0 | Ran 6 tests in 0.299s OK |
| test_r39_genres | 0 | Ran 3 tests in 0.007s OK |
| test_r39_trakt | 0 | Ran 1 test in 0.000s OK |
| test_samename | 0 | Ran 11 tests in 0.005s OK |
| test_site_nov4 | 0 | Ran 10 tests in 0.009s OK |
| test_tvmaze | 0 | Ran 7 tests in 0.939s OK |
| test_two_users | 0 | Ran 3 tests in 5.605s OK |
| test_v7_nov1_fixes | 0 | Ran 7 tests in 0.035s OK |
| test_v7_nov3_fixes | 0 | Ran 12 tests in 0.057s OK |
| test_v7_oct30_fixes | 5 | Ran 0 tests in 0.000s |
| test_v8_extra | 0 | Ran 17 tests in 0.177s OK |
| test_v8_home | 0 | Ran 17 tests in 0.111s OK |
| test_v8_pick | 0 | Ran 3 tests in 1.730s OK |
| test_v8_r18 | 0 | Ran 11 tests in 0.030s OK |
| test_v8_r21 | 0 | Ran 18 tests in 0.037s OK |
| test_v8_r27b | 0 | Ran 15 tests in 0.087s OK |
| test_v8_r27 | 0 | Ran 13 tests in 0.379s OK |
| test_v8_r34 | 0 | Ran 7 tests in 0.001s OK |
| test_v8_web | 0 | Ran 26 tests in 0.075s OK |

## Appendix D. Databases (definitions in audit_raw/db_schema.txt; no rows were read)

| File | Size | Journal | Tables | Indexes |
|---|---|---|---|---|
| facts.db | 9384.6 MB | wal | 114 | 108 |
| logs/adapters.db | 0.0 MB | delete | 2 | 2 |
| logs/alerts.db | 0.0 MB | delete | 4 | 4 |
| logs/chats.db | 0.1 MB | wal | 2 | 3 |
| logs/email.db | 0.0 MB | delete | 2 | 2 |
| logs/feedback.db | 0.3 MB | delete | 2 | 3 |
| logs/genre_votes.db | 4.7 MB | delete | 2 | 2 |
| logs/history.db | 0.0 MB | wal | 5 | 3 |
| logs/memory.db | 0.0 MB | wal | 4 | 3 |
| logs/social.db | 0.0 MB | wal | 6 | 7 |
| logs/tmdb_extra.db | 0.3 MB | delete | 4 | 3 |
| logs/trakt.db | 0.0 MB | delete | 1 | 1 |
| logs/tvmaze.db | 217.7 MB | wal | 6 | 5 |
| logs/users.db | 0.1 MB | wal | 10 | 10 |
| movies.db | 403.7 MB | delete | 19 | 5 |
| review_quality.db | 9.7 MB | delete | 2 | 3 |
| wiki.db | 2106.9 MB | delete | 26 | 17 |
| wiki_pfts.db | 153.5 MB | delete | 6 | 0 |
