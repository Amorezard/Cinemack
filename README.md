# Movie Buff

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
