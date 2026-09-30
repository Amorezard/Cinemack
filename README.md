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

## Data and licences

Film records: TMDB (via the Kaggle "TMDB movies dataset"); this product uses the TMDB API but
is not endorsed or certified by TMDB. Wikipedia text: the TREC Tip-of-the-Tongue 2023 corpus,
CC BY-SA. Film links: Wikidata (CC0).
