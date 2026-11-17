# Rebuilding it from scratch

**In one sentence:** build it in the order it grew - data, retrieval, model, rules and checks - and only then the
website.

## 1. Machine

Linux, an NVIDIA GPU with 24 GB (the 27B model takes about 17 GB), Python 3.11+, Node 18+ (page tests only) and
[Ollama](https://ollama.com). Less GPU memory works with a smaller `ANSWER_MODEL` in `config.yaml`, at the cost of the
persona and a weaker re-ranker.

## 2. Software and models

```bash
python3 -m venv ~/venvs/movie-rag && source ~/venvs/movie-rag/bin/activate
pip install -r requirements.txt
ollama pull mxbai-embed-large    # the embedder every vector is built with
ollama pull llama3.2             # generated descriptions, small jobs
ollama pull llama-guard3:1b      # safety
ollama pull gemma3:27b           # evaluation judge only
```

The `movie-buff` answer model is a persona LoRA on a Qwen 27B base, trained outside this folder (in `~/movie-buff`:
`train_lora_unsloth.py`, rank 16, alpha 32, 10 epochs on `movie_training_data.jsonl`) and registered with
`ollama create movie-buff -f Modelfile`. Without it, point
`ANSWER_MODEL` at any chat model Ollama has.

## 3. Data

API keys go in `keys.env` (never committed): `TMDB_API_KEY`, `OMDB_API_KEY`, `GUARDIAN_API_KEY`, `NYT_API_KEY`,
`YOUTUBE_API_KEY`, `FRED_API_KEY`. Downloads: the TREC Tip-of-the-Tongue 2023 corpus, the IMDb non-commercial
datasets, MovieLens 32M (`data/movielens/ml-32m.zip`).

```bash
python mb.py build        # every build step in order, whether it is done, and its command
python mb.py check        # every line must say ok
python mb.py build-cache  # in-memory vectors
python mb.py ask "that movie where a guy relives the same day over and over"
```

The two long steps are passage embedding (about 40 minutes on an A10) and generated descriptions (about 6 hours);
both resume where they stopped.

## 4. Evaluate before adding anything

Write tuning sets and sealed sets in `sets/`, hash the sealed ones (`md5sum sets/sealed_* > results/sealed.md5`),
and record a baseline:

```bash
python mb.py eval --split tune --tag baseline     # stage_scores.py: is the right film found, kept, named?
python eval_v3.py --tag baseline                  # judge + deterministic checks
```

Then add features one switch at a time, each with its rule written first.

## 5. The website

```bash
MOVIE_BUFF_MOCK=1 MOVIE_BUFF_CODE=test PORT=8765 python web/server.py   # no GPU: fake model, fixture data
MOVIE_BUFF_CODE=<access code> python mb.py serve                         # the real site
```

## 6. Nightly refresh

`bash nightly.sh --dry` lists the steps without running them; then add it to cron (`17 4 * * *`).

## 7. Run every test

```bash
for t in tests/test_*.py; do python -m unittest tests.$(basename $t .py); done
node --test tests/js/test_v81_frontend.mjs
```

## 8. Re-run any v8.1 measurement

Each writes `results/<name>.txt` and `.json`:

| Measurement | Command |
| --- | --- |
| Identity build | `python tools/v81_identity_build.py` |
| Hard limits, films / TV | `python tools/filter_conformance.py`, `python tools/v81_tv_conformance.py` |
| Box-office factor study | `python tools/v81_factor_study.py` |
| Forecast + stored forecasts | `python tools/v81_forecast.py`, `python tools/v81_forecast_store.py` |
| Ranking benchmark / third candidate | `python tools/v81_recs_benchmark.py`, `python tools/v81_recs_v3.py` |
| Motion audit | `python tools/v81_motion_audit.py` |
| Review coverage, YouTube matches, review relinking | `python tools/v81_review_coverage.py`, `tools/v81_yt_match.py`, `tools/v81_review_relink.py` |
| Publish check | `python tools/v81_publish_check.py --history` |
