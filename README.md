# Movie Buff (Cinemack)

> **New here? Start with [`docs/guide/`](docs/guide/README.md).** It explains the whole system in plain language:
> [how it works](docs/guide/HOW_IT_WORKS.md), [recommendations](docs/guide/RECOMMENDATIONS.md),
> [box-office forecasting](docs/guide/BOX_OFFICE.md), [how everything was measured and every result](docs/guide/EVALUATION_AND_RESULTS.md),
> [engineering and design choices](docs/guide/ENGINEERING.md), [rebuilding it](docs/guide/REPRODUCE.md) and
> [publishing safely](docs/guide/PUBLISHING.md). The rest of this file is the running build history and reference.

A movie chatbot that answers questions about films, including "what's that movie where…"
questions from half-remembered descriptions. A persona model (`movie-buff`, a LoRA on a 27B
Qwen model, served by Ollama) answers from a retrieval pipeline over ~30,000 TMDB films and
their Wikipedia plot text.

How a question is answered:

1. **Plan**: the chat model reads the question: which film is named, is it a description,
   a recommendation, a follow-up? For descriptions it also writes a guessed plot (HyDE).
2. **Search**: vector search over TMDB records, TMDB plots, Wikipedia intro and plot
   passages, and generated "tip of the tongue" descriptions, plus keyword search. The lists
   are fused with reciprocal rank fusion (RRF).
3. **Re-rank**: when the search isn't confident, the chat model reads the top 50 films'
   Wikipedia passages and picks the match.
4. **Answer**: the chat model answers from the top films' records and Wikipedia details.

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

## Quick start (a machine that already has the data)

```bash
cd ~/movie-buff/rag
source ~/venvs/movie-rag/bin/activate
python mb.py check           # every line should say ok; fix any FAIL first
python mb.py build-cache     # once, and again whenever movies.db or wiki.db change (1-3 min)
python mb.py ask "that movie where a guy relives the same day over and over"
python mb.py chat            # the terminal chat
```

## Setting it up somewhere new

**Hardware.** Default: an NVIDIA GPU with 24 GB (the 27B model takes ~17 GB). With less
memory, set `ANSWER_MODEL` in `config.yaml` to a smaller Ollama model (answers lose the
persona, and the re-ranker gets weaker). CPU-only works but each answer takes minutes.

**Software.**

```bash
python3 -m venv ~/venvs/movie-rag && source ~/venvs/movie-rag/bin/activate
pip install -r requirements.txt
ollama pull mxbai-embed-large          # the embedder the vectors were built with
ollama pull llama3.2                   # only to build the generated descriptions
```

The `movie-buff` model is created from the persona LoRA with `ollama create` (see the
Step-by-Step Build Guide). Without it, point `ANSWER_MODEL` at any chat model Ollama has.

**Data.** Either copy `movies.db` and `wiki.db` into this folder, or build them:

```bash
python mb.py build           # lists every build step, whether it's done, and its command
```

The Wikipedia steps need the TREC Tip-of-the-Tongue 2023 corpus (`corpus.jsonl.zip` and
`wikipage_id_to_imdb.json.zip`). The two long steps are embedding the passages (~40 min on
an A10) and generating descriptions with llama3.2 (~6 h); both are resumable.

Then `python mb.py check` and `python mb.py build-cache`.

## Commands

| Command | Does |
|---|---|
| `python mb.py check` | Checks packages, Ollama and the models, both databases, that the embedder matches the one that built the vectors, the vector cache, and the GPU. Exit code 1 on any FAIL. `--quick` skips loading the embedder. |
| `python mb.py settings` | Every setting in effect and where it came from (shell, config.yaml, or code default). |
| `python mb.py build` | The data build steps in order, with status and command. |
| `python mb.py build-cache` | Builds `vec_cache/` (in-memory copies of the vectors) and compares it with sqlite-vec. |
| `python mb.py ask "…"` | One question through the whole pipeline; prints the films found and the answer. `--think` shows reasoning. |
| `python mb.py chat` | The terminal chat (`chat.py`). `/think` toggles reasoning. |
| `python mb.py serve` | The website (`web/server.py`); set `MOVIE_BUFF_CODE` first. See `web/RUN.md`. |
| `python mb.py eval …` | `stage_scores.py` with the same arguments, e.g. `python mb.py eval --split tune --tag mytest`. |

## Settings

All settings are environment variables read by `retrieve.py` and `chat.py`. `config.yaml`
holds the chosen values (the ones locked after experiments E1-E9); a variable set in the
shell wins over the file, so one-off experiments need no edits:

```bash
RERANK_N=30 python mb.py eval --split tune --tag rerank30
```

`MB_CONFIG=other.yaml` uses another file; `MB_CONFIG=none` ignores it. The most useful ones:

| Setting | Default | What it does |
|---|---|---|
| `ANSWER_MODEL` | movie-buff | Answers, title guesses and the re-ranker |
| `QUERY_MODEL` | = ANSWER_MODEL | Reads the question first (the planner) |
| `EMBED_MODEL` | mxbai-embed-large | Must match the model that built the vectors |
| `WIKI` | 1 | Search Wikipedia passages |
| `WIKI_Q` | rrf | Generated descriptions as their own ranked list |
| `HYDE` | 1 | Search with a guessed plot for descriptions |
| `VEC_MEM` | 1 | In-memory vector search: same results as sqlite-vec, ~7x faster. Falls back to sqlite-vec when `vec_cache/` is missing or stale |
| `RERANK` | auto | 1 always re-rank, 0 never, auto skip when the search is confident |
| `RERANK_N` | 50 | Films the re-ranker reads |
| `RERANK_WIKI_CHARS` | 400 | Characters of Wikipedia text per film for the re-ranker |
| `ANSWER_WIKI` | 1 | Answers include Wikipedia details |

`config.yaml` lists the rest with a note on the experiment behind each.

## Evaluating

`stage_scores.py` scores every stage (is the expected film in the search pool, does it reach
the re-ranker, is it first after re-ranking, is it in the final answer), for the question
sets in `sets/` (`tune`, `check`; `final` is sealed and runs once with `--final`). Model
calls are cached in `results/llm_cache.sqlite`, so re-running with unchanged prompts makes
0 new calls. `--no-cache` measures real timings. `compare_stage.py A.json B.json` compares
two runs question by question.

## Files

| File | Role |
|---|---|
| `retrieve.py` | Planning, search, fusion, re-ranking |
| `chat.py` | Answer prompt and the terminal chat |
| `vecmem.py` | In-memory vector search (`vec_cache/`) |
| `mbconfig.py`, `config.yaml` | Settings |
| `mb.py` | This command-line entry point |
| `web/` | The website (`server.py`, `pipeline.py`, `site_cache.py`) |
| `wiki_*.py` | Wikipedia link, chunk, embed, keyword index, generated descriptions |
| `build_db.py`, `fetch_credits.py`, `rebuild_embeddings.py`, `build_plot_vectors.py`, `build_fts.py` | TMDB database |
| `stage_scores.py`, `compare_stage.py`, `judge.py`, `factcheck.py` | Evaluation |
| `run_e*.sh` | The experiments, each writing `results/eN_summary.txt` |
| `run_v4.sh` | v4 steps and their decision rules (`results/v4_summary.txt`) |

## Data and licences

Film records: TMDB (via the Kaggle "TMDB movies dataset", and in v3 the TMDB API: details, credits,
user reviews, current and upcoming films); this product uses the TMDB API but is not endorsed or
certified by TMDB. Wikipedia text: the TREC Tip-of-the-Tongue 2023 corpus, CC BY-SA. Film links,
awards, box office and cost: Wikidata (CC0). News headlines (v4): The New York Times Archive API
("Data provided by The New York Times"; headline, abstract, date and link only) and Wikipedia's dated
event lists (CC BY-SA). Audience ratings: MovieLens (F. Maxwell Harper and
Joseph A. Konstan, 2015, ACM TiiS 5(4)), used under the GroupLens licence.

### Licensing register (v7, from `v7_data.py SOURCES`)

<!-- register:start -->
| Source | What | Tables | Terms and our rule | Credit | Refresh (days) |
|---|---|---|---|---|---|
| [tmdb](https://www.themoviedb.org/api-terms-of-use) | film and TV metadata, cast, keywords, trailers, posters, age ratings, audience reviews | `films people credits keywords reviews film_videos film_images certifications tv_*` | TMDB API terms: free, non-commercial, attribution required | This product uses the TMDB API but is not endorsed or certified by TMDB. | 30 |
| [justwatch](https://www.justwatch.com) | where to watch (via TMDB) | `watch_providers tv_providers` | TMDB watch-provider data must credit JustWatch | Streaming availability data by JustWatch. | 7 |
| [wikipedia](https://en.wikipedia.org/wiki/Wikipedia:Copyrights) | lead, plot, production, reception and box-office text; reception wikitext with citations | `wiki.db sections wiki_reception recent_wiki tv_wiki` | CC BY-SA 4.0: credit and link each article, share alike | Text from Wikipedia (CC BY-SA 4.0), linked under each answer. | 30 |
| [wikipedia-critics](https://en.wikipedia.org/wiki/Wikipedia:Copyrights) | critic statements from Reception sections, each with the outlet / critic its <ref> cites | `critic_statements review_sentiment evidence` | CC BY-SA 4.0 for the Wikipedia sentence; the quoted outlet is credited and its review linked when the citation has a URL; short quotes only | Critic statements as reported by Wikipedia (CC BY-SA 4.0), with the outlet each one cites. | 30 |
| [wikidata](https://www.wikidata.org/wiki/Wikidata:Licensing) | budget, box office, awards, ids, review scores stated on each item (P444) | `facts awards wikidata_scores` | CC0 | Data from Wikidata (CC0). | 30 |
| [imdb](https://developer.imdb.com/non-commercial-datasets/) | ratings and votes, title types, alternate titles, directors, writers, principal cast, episodes | `imdb_ratings imdb_titles imdb_akas imdb_crew imdb_principals imdb_names imdb_episodes` | IMDb Non-Commercial Datasets: personal and non-commercial use only | Data courtesy of IMDb. | 7 |
| [movielens](https://grouplens.org/datasets/movielens/) | 32M audience ratings: distributions and change over time; tag genome (25M) | `ml32_stats ml_dist ml_year film_tags` | GroupLens research licence: non-commercial, cite the dataset paper; do not redistribute | MovieLens data: F. M. Harper and J. A. Konstan, ACM TiiS 5(4), 2015. Tag genome: Vig, Sen and Riedl, ACM TiiS 2(3), 2012. | - |
| [omdb](https://www.omdbapi.com/legal.htm) | Rotten Tomatoes %, Metacritic, IMDb rating, awards text | `omdb` | OMDb API: free key 1,000 requests/day (CC BY-NC 4.0 for the data) | Scores via OMDb. | 30 |
| [guardian](https://open-platform.theguardian.com/access/) | film reviews: star rating, standfirst, critic, link | `critic_reviews (source = guardian)` | Guardian Open Platform developer key: non-commercial. Decision (E8, 27 Oct): stars and the one-line standfirst are shown and may go in prompts with a link; article bodies are NOT analysed by any model until the terms for text mining are confirmed | Reviews from The Guardian (powered by the Guardian Open Platform), linked. | 7 |
| [nyt](https://developer.nytimes.com/terms) | film reviews: critic, date, Critic's Pick, link (no text) | `critic_reviews nyt_meta (source = nyt)` | NYT Developer APIs: non-commercial, attribution. Decision (E8, 27 Oct): the NYT terms bar using content to ground or train AI without consent, so NYT text never goes into a prompt or through a model; only critic, date, Critic's Pick and a link are shown | Data provided by The New York Times. | 7 |
| [youtube](https://developers.google.com/youtube/terms/developer-policies) | trailer statistics, review / analysis videos of allow-listed channels (title, description, chapters), public comments | `yt_videos yt_comments yt_channels yt_analysis yt_analysis_film yt_analysis_units` | YouTube API Services terms. Decision (E8): stored data is refreshed or deleted within 30 days (nightly.sh purge); no captions or transcripts; comments are counted, not analysed by a model | Video data from YouTube, linked to each video. | 30 |
| [netflix](https://about.netflix.com/en/news/what-we-watched) | hours viewed and views per film per half-year | `netflix_views` | Netflix engagement reports (public); TidyTuesday CSV (CC0) | Viewing figures: Netflix What We Watched. | 180 |
| [tvmaze](https://www.tvmaze.com/api) | TV schedules: network or service, air days and times, upcoming episodes, episode summaries (website only; never in answers or evaluations) | `logs/tvmaze.db` | TVmaze API: free, no key, CC BY-SA 4.0 - credit with a link, and what we pass on carries the same licence | Schedule, air times and some episode summaries: TVmaze (CC BY-SA 4.0), linked on each show page and the calendar. | 1 |
| [letterboxd](https://letterboxd.com/api-beta/) | ratings, distributions, reviews (only if API access is approved) | `-` | API by request only; terms forbid scraping. connectors/letterboxd.py stays APPROVED = False until written approval arrives | - | - |
| [rottentomatoes](https://www.rottentomatoes.com/help_desk/licensing) | official RT API (licensed, not used) | `-` | Licensed through a business proposal; scores come from OMDb and Wikipedia instead | - | - |
<!-- register:end -->
