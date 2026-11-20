# Build notes, version by version (v3 to v7)

> This is the running build log that used to be the top-level README. It is kept as written at the time, so some
> switch defaults and file names here describe the version they belong to. The current overview is the
> [README](../README.md); the plain-language guide is [docs/guide/](guide/README.md).

## v3 (branch `v3`, from tag `v2.0-lock`)

v3 keeps the v2 tip-of-the-tongue pipeline as it is and adds what it couldn't do. Every part has its
own switch in `config.yaml`, and each starts **off** until its experiment says to turn it on
(`run_v3a.sh`, `run_v3b.sh`; decision rules are written at the top of each script).

| Switch | File | What it adds |
|---|---|---|
| `CONV` | `conv.py` | The planner sees the conversation: earlier questions, every film so far (in order, with turn numbers, rejected ones marked), the start of the last answer, and stated tastes. It resolves "the one from earlier", "compare that with Barbie", "their budgets" and "no, it's Korean". A first question gets exactly the v2 prompt. |
| `TOOLS` | `tools.py`, `chat.py` | A rule-based router picks tools per question: structured facts (budget/gross from 3 sources with conflicts shown, crew, runtime, awards, ratings), side-by-side comparisons, market context (release window, genre-year baseline, director record, franchise), Wikipedia Production/Reception/Box office passages, review analysis, careers and rankings (SQL from a small LLM spec), and now playing/upcoming. "Why" questions get an answer format that keeps evidence and interpretation apart. |
| `PERSONAL` | `personal.py` | Film likes/dislikes (card buttons, kept apart from answer feedback) and stated tastes become an interpretable profile. Recommendations are re-ranked for the person, with a "why it fits you" line each. |
| `PARALLEL` | `retrieve.py` | The question's own searches run while the planner writes. Results are the same. |
| `RERANK_MODEL` | `retrieve.py` | A smaller re-ranker model (latency experiment). |

New data lives in `facts.db` (`facts_db.py`). `movies.db` and `wiki.db` are unchanged.

```bash
export TMDB_API_KEY=...
python fetch_tmdb.py catalog && python fetch_tmdb.py current   # TMDB details, credits, reviews, current films
python fetch_wikidata.py                                        # awards, box office, cost
python wiki_sections.py build --corpus <corpus.jsonl.zip>       # Production / Reception / ... passages
python wiki_sections.py infobox && python wiki_sections.py embed
python tools.py build                                           # best money figure per film, MovieLens stats
python reviews_analyze.py --films 800                           # audience + critic aspects (llama3.2)
python fetch_tmdb.py refresh                                    # later: only what is stale (daily/weekly/monthly)
python tools.py try "why did Jaws do so well?"                  # show the route and the data, no answer
```

Evaluation: `stage_scores.py` (retrieval, now also multi-turn `sets/multiturn.json` with `expect_absent`)
and `eval_v3.py` (`sets/analytic.json`: route, data in context, answers + judge). Feedback:
`python feedback.py stats|list|review|export` (`logs/feedback.db`; only reviewed rows are ever exported).
Experiments: `summarize.py eval` (flat passages vs summary-first) and `personal.py eval` (MovieLens leave-out).

## v4 (from 14 Oct; GPU free again)

`run_v4.sh` runs everything below with decision rules written before the results; every new
behaviour is a switch in `config.yaml` (`v4:`), off until its rule passes.

| Piece | File | What it does |
|---|---|---|
| Contextual chunks | `chunk_context.py` | Jackson Higgins' method: llama3.2 writes a context line, topics and keywords for every Wikipedia passage; the passage is embedded and keyword-indexed with them (`WIKI_KINDS=plot_c,lead_c`, `WIKI_CTX_KW=1`). The text shown to the models is unchanged. A sample test (per-retriever hit@k / MRR, plain vs contextual) decides whether the full ~25k-call run happens. |
| Liked-answer filter | `like_filter.py` | Screens thumbs-up answers before they can become training data: relevance to the question, grounding in the answer's own context, numbers not in the context, the user's next message correcting it, duplicates, contradicting thumbs-down, one browser's share, optional llama3.2 check. `auto_accept` / `needs_review` / `auto_reject`; thresholds calibrated on judged answers (`calibrate`). |
| Nightly adapter rebuild | `rebuild_adapter.sh`, `adapter_data.py`, `train_adapter.py` | Each night: filter likes, build the training set (persona + written + liked + corrected + distilled, minus anything older than 90 days or overlapping an evaluation set), skip unless 50+ new examples, train the LoRA, `ollama create movie-buff-aYYYYMMDD`, test against the live model (analytic answers + judge, retrieval check set), promote through `live_model.yaml` only if it wins, keep the newest 3 adapters and remove the rest. Settings in `adapter.env`. |
| News at release | `news.py` | NYT Archive front pages + Wikipedia event lists around each film's release; related headlines, topic overlap vs control windows, news themes, links the film's own Wikipedia text states, and a corpus-wide test of whether on-topic news goes with box-office return relative to similar films (`NEWS=1`). |
| Success measures | `tools.py` | Money / reception / recognition measured against films of the same genre and year, plus a thin-evidence line under 10 analysed reviews (`SUCCESS=1`). |
| Route fix | `tools.py` | Comparison wording finds both films even when retrieval returned one (`ROUTE_FIX=1`). |
| Review pipeline | `review_filter.py`, `reviews_analyze.py` | Small-model troll/spam check after the rule checks; review analysis uses only usable reviews and no longer truncates its JSON (`REVIEW_NUM_PREDICT`). |
| Mock client | `mock_llm.py` | `MOCK=replay` replays recorded model calls from `results/llm_cache.sqlite`, `MOCK=fake` fakes the rest, so the pipeline runs on a laptop without Ollama. |

```bash
python news.py fetch --source wiki --from 1925 && python news.py embed && python news.py films && python news.py analyze
python news.py show "Jaws"                         # the block the answer model sees
python like_filter.py run && python like_filter.py report
bash rebuild_adapter.sh dry                        # the nightly steps without training
bash rebuild_adapter.sh list | rollback            # adapters built, and going back one
MOCK=fake python mb.py ask "that movie with the shark"
```

## v6 (16-22 Oct): the site as a product, and more free data

Every answer-changing piece is a switch, off until its rule (written before the result) passes; site-only
features that don't change answers are on.

| Piece | File | What it does |
|---|---|---|
| Accounts | `web/accounts.py` | Optional sign-up / sign-in (scrypt or Argon2id hashes, HttpOnly session cookie, lockout after 5 failures); a guest's chats, film choices, settings and memory move to the new account; delete-account removes everything. |
| Sign-in and onboarding | `web/index.html` | One centred flow: Account → You → Taste (11 groups → stated tastes) → Style (length, voice, spoilers, age rating) → All set. Full-screen on phones. |
| Settings | `web/user_prefs.py` | Length (also a hard `num_predict` cap), voice, spoilers, never-genres, earliest year, longest runtime, US age-rating limit, "about me" note, money / critic lines on or off, memory, recall. Recommendation limits are applied in code before the prompt. |
| Memory | `web/memory.py` | Notes the person states about themselves (llama3.2, checked against their words); recall of related exchanges from their other chats (word + title overlap, no model call); a line per recent chat when they refer back. Private chat uses none of it. |
| Film cards and panel | `web/film_info.py` | Posters, cast photos, IMDb / RT / Metacritic, US rating, the four taste buttons, watchlist; panel with trailer, where to watch, review and box-office links. |
| Links under answers | `web/links.py` | Chosen by the question: where to watch (JustWatch via TMDB), reviews (matched Guardian / NYT review, RT, Metacritic, IMDb, Letterboxd), box office (Box Office Mojo, Wikipedia section), trailers (YouTube, privacy-enhanced, loads on click). |
| Free data | `fetch_v6_data.py`, `fetch_free_data.py` | Posters / cast photos, IMDb ratings, Wikipedia pageviews before release; TMDB trailers, watch providers and certifications; OMDb critic scores; Guardian and NYT reviews; YouTube trailer stats, descriptions, review videos and comments; Netflix engagement reports; MovieLens 32M. `python fetch_free_data.py coverage`. |
| Data in answers | `extra_data.py`, `tools.py` (`EXTRA_DATA=1`) | OMDb scores where Wikipedia has none, Guardian / NYT lines, a where-to-watch block, an audience-and-reach block (IMDb, MovieLens, Netflix, trailer lead time; YouTube views labelled as after release). |
| Review sources | `reviews_analyze.py --kinds pro,ytc` | Aspect counts with quotes from professional reviews and trailer comments, next to TMDB users and Wikipedia's reception text. |
| Box-office model pf3 | `performance.py` | + trailer timing (pre-release only; used per film only if it beats pf2 on 2016-19), + pre-release Wikipedia views (own 5-fold test, reported only), + OMDb / IMDb in the after-release model. |
| Date words | `retrieve.py` (`DATE_WORDS=1`) | "late 90s", "seventies", "last 5 years", "last year"; "recent" / "old" only in recommendations; ages ("in her early 20s") are not decades. |
| Kinds of picks | `retrieve.py` (`DISCOVER=1`) | "highly rated / acclaimed / best" ranks recommendations by a vote-weighted rating; "lesser-known / hidden gems / underrated" keeps well-rated films with 60-2,500 votes; "what came out in 1999" and "highly rated horror" count as browse requests; the catalogue tops up the list with the same year / genre filters. |
| Planner sees your films | `retrieve.PERSON`, `web/server.py` (`PLAN_PAST=1`, web only) | The person's liked / disliked / watchlist films go to the planner, so "like the last one I liked" resolves. |

```bash
python fetch_free_data.py tmdb && python fetch_free_data.py movielens && python fetch_free_data.py netflix
python fetch_free_data.py guardian --again --body     # OMDB_API_KEY / GUARDIAN_API_KEY / NYT_API_KEY / YOUTUBE_API_KEY
python fetch_free_data.py omdb                        # free key: 1,000 a day, run daily
python extra_data.py "Oppenheimer"                    # the new answer lines for one film
python performance.py fit && python performance.py show "Oppenheimer"
bash run_v6_night.sh                                  # pf3 fit, YouTube, EXTRA_DATA answer test
bash run_v6_day.sh                                    # nickname rerun, DATE_WORDS test, review sources
```

## v7 (23 Oct - 5 Nov): reception, TV, safety, speed

Same principle as v6: every answer-changing piece is a switch in `config.yaml`, **off** until its rule (written before
the result, plan doc "Tests and rules") passes; tune / check may lose at most 1. `python v7_rules.py ...` prints the
numbers each rule uses and PASS / FAIL; the switch is then set by hand. Code freeze Tue 3 Nov 18:00 (tag
`v7.0-lock`); sealed run v6.0 vs v7 that night.

| Piece | File | Switch | Rule to turn it on |
|---|---|---|---|
| Critic statements from Wikipedia's Reception sections, with the outlet / critic each `<ref>` cites (R2) | `critics.py` | data | outlet right on >= 90% of 50 hand-checked (`critics.py score`) |
| Review sentiment (RoBERTa + head fitted on TMDB ratings), joke / sarcasm, bursts / review bombing, rating-text mismatch (R3, R5, H7) | `sentiment.py` | data | macro-F1 >= 0.80 on hand labels and beats llama3.2; flag precision >= 0.8 |
| Reception card per film: critics vs audiences (0-100), gap, polarisation, trend, legacy, bombing, praise / complaints, summaries (R8, R12, R13, E2-E4, H9) | `reception_cards.py` | data | summary sentences supported >= 95% (`claimcheck.py summaries`) |
| Review scores stated on Wikidata (E1) | `v7_data.py wd-scores` | data | coverage reported |
| Evidence search over critic statements, standfirsts, user reviews, TV reviews, analysis videos (R14) | `evidence.py` | with RECEPTION | evidence hit@5 reported (`evidence.py hit`) |
| Reception answers: card + cited evidence + numbered sources (R15) | `tools.py`, `chat.py` | `RECEPTION=1` | reception set (40): judge passes >= off + 6, unsupported rate not higher, grounding not lower |
| Claim checker (NLI): unsupported rate, citation precision / recall (R16) | `claimcheck.py` | measure | - |
| Video essays from 23 allow-listed channels: title, description, chapters only (R11) | `youtube_analysis.py` | data | right film >= 90% of 50 checked |
| Nightly refresh, purge (YouTube 30 days), rebuild (R17) | `nightly.sh` | cron | dry run clean |
| Connectors registry + tests on saved samples (R1); Letterboxd stays off until API approval | `connectors/` | - | `python -m pytest tests/` |
| TV shows: search, answers, cards (D1) | `tv.py` | `TV=1` | TV set >= 20 / 30; tune / check / analytic each lose <= 1 |
| Director / actor filters (D2) | `people.py` | `PEOPLE=1` | every film returned has the person (`people.py check`) |
| IMDb rating line; IMDb alternate titles as aliases (D2) | `tools.py`, `aliases.py` | `IMDB=1`, `ALIAS_IMDB=1` | regression rule (tested with the D arm on Thursday) |
| Recall earlier chats by meaning (D3) | `web/memory.py`, `memory_eval.py` | `MEM_EMBED=1` | hit@1 higher, false recall not higher |
| Two users at once: per-request state (D4) | `request_state.py` | always | `tests/test_two_users.py` |
| Injection defences: spotlighting, sanitising, link allow-list, leak check (D5) | `safety.py` | `GUARD=1` | >= 36 / 40 attacks defended (`attack_eval.py`); normal sets lose <= 1 |
| Moderation: Llama Guard 3 1B + Detoxify (D6) | `moderation.py` | `MODERATION=1` | <= 1 false block across the normal sets |
| Fact fast path from facts.db (D7) | `fastpath.py` | `FAST=1` | every fast answer holds the required facts; no non-factual question takes it |
| Recommendation clean-up: no TV movies, concerts, shorts, unreleased, low-vote, off-genre (D8) | `rec_clean.py` | `REC_CLEAN=1` | 0 bad items in the 10 discovery prompts' top 10 |
| Ollama speed A/B: flash attention, q8_0 KV cache, 2 parallel (D9) | `speed_ab.sh` | Ollama env | faster with tune / check within 1 |
| Context builder, prompt order (D10) | `chat.py` | `CTX_BUILDER=1`, `PROMPT_ORDER=1` | regression rule |
| Semantic answer cache (D11) | `web/semcache.py` | `SEMCACHE=1` | 0 wrong reuses on 20 near-miss pairs |
| MRR and per-retriever hit rates (D12) | `metrics.py` | report | - |
| Confidence model refit on identify questions (D13) | `confidence.py fit --identify` | report | calibration error down |
| Personal taste weights on MovieLens 32M (D14) | `personal.py sweep32` | `PERSONAL_W` | bootstrap CI beats the live weights |
| Money: euro legacy currencies, nearest-year fallback (H2) | `money_extra.py` | data | `money_extra.py diagnose` |
| Reception panel in the film details (E5) | `web/index.html`, `web/film_info.py` | site | - |
| Judge check: 30 answers graded by hand vs gemma3 (E7) | `judge_check.py` | report | >= 80% agreement and kappa >= 0.6 |
| Licensing register (E6) | `v7_data.py register` | docs | - |

Terms decisions (E8, 27 Oct): New York Times text never goes into a prompt or through a model (only critic, date,
Critic's Pick and a link are shown); Guardian article bodies are not analysed until the text-mining terms are
confirmed (stars and standfirsts are shown and may be quoted with a link); YouTube data is refreshed or deleted within
30 days and captions / comments are not analysed. Letterboxd is used only if API access is approved in writing.

```bash
bash run_v7_night.sh TUE            # reception data, critics, sentiment, flags, samples, aspects, cards, summaries
bash run_v7_night.sh WED            # evidence, cards, reception A/B, claim check, hit@5, analytic regression, rules
bash run_v7_night.sh THU            # TV build + TV set A/B, people, discovery, aliases, fast path, recall, attacks
bash run_v7_night.sh FRI            # speed A/B, context builder / prompt order, recent A/B, confidence, sweep32
python v7_data.py register          # rewrite the licensing register below and on the About page
python judge_check.py sample results/v3eval_<tag>.json   # E7 grading page
```

