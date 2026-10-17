#!/usr/bin/env bash
# v4 (from 14 Oct, GPU free again): the work v3 left half done, plus four new pieces.
#
#   tmux new -s v4
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export NYT_API_KEY=...            # optional; without it the news step uses Wikipedia only
#   bash run_v4.sh                    # all steps below, in order (~1.5-3 days; each step resumable)
#   bash run_v4.sh reviews news       # or any steps by name
#
# Steps
#   backup   copies of wiki.db / facts.db / movies.db as they were for the sealed v3 run (their md5s are
#            in results/final_v3_summary.txt), before anything here changes them
#   mock     mock_llm.py selftest + an offline replay of two analytic questions (no GPU needed)
#   reviews  review_filter.py run + model check (llama3.2), then review analysis over every film with
#            usable reviews (num_predict fix), REVIEW_HOURS (default 10) at most
#   ctx      Jackson's contextual chunks: sample test (3,000 films). If its rule passes: every film
#            (~25k model calls), the contextual index, then stage_scores arms (rule in chunk_context.py)
#   news     Wikipedia events 1925-now (+ one day of NYT quota if NYT_API_KEY), embed, per-film windows,
#            corpus test, then answers + judge NEWS=0 vs NEWS=1 on sets/news.json and the analytic "why" set
#   success  answers + judge on the why / compare / reviews analytic questions: locked vs SUCCESS=1,
#            ROUTE_FIX=1 and both; level 1 (route/data) on the whole analytic set for each arm
#   gpu      the bge re-ranker trained on the GPU (run_v3c.sh), and PARALLEL / PLAN_ROUTE timings A-B-A-B
#   adapter  the first adapter rebuild by hand (bash rebuild_adapter.sh --force): the "LoRA on the RAG
#            answer format" item; distilled examples + persona + any filtered likes
#   summary  results/v4_summary.txt
#
# Decision rules (written 14 Oct, before any result):
#   ctx      sample: contextual fused hit@5 >= plain + 0.02 and MRR not lower (else stop there).
#            full: adopt WIKI_KINDS=plot_c,lead_c (and separately WIKI_CTX_KW=1) only if tune and check each
#            lose at most 1 question and tune + check together gain at least 2.
#   news     adopt NEWS=1 if on sets/news.json the route is right on 6+ of 8 and judge passes are higher than
#            NEWS=0, AND on the analytic "why" questions judge passes are not lower and mean grounding is
#            not lower by more than 0.05.
#   success  adopt SUCCESS=1 / ROUTE_FIX=1 (each, then both) if judge passes on why+compare+reviews are
#            at least locked + 1, no category loses more than 1, and level-1 route and data on the full
#            analytic set are not lower. (The analytic set was used to tune v3: report this as optimistic;
#            a new sealed set decides for the write-up.)
#   gpu      as run_v3c.sh (re-ranker) ; PARALLEL / PLAN_ROUTE: report times, accuracy rules unchanged.
set -u
cd "$(dirname "$0")"
LOG=${V4_LOG:-results/v4.log}        # 14 Oct: a second lane can log elsewhere (V4_LOG=results/v4b.log)
SUM=results/v4_summary.txt
STEPS=${*:-backup mock success news gpu reviews ctx adapter summary}
LOCK="CONV=1 TOOLS=1 PERSONAL=1 PERSONAL_W=0.25,2,0.5"
mkdir -p results logs backups
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v4.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
busy() { pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|train_reranker.py|chunk_context.py build|rebuild_adapter.sh" \
         | grep -v $$ >/dev/null; }
# summary alone only reads result files, so it runs at once even while other steps are running
if [ "$STEPS" != "summary" ] && [ "${NOWAIT:-0}" != 1 ]; then   # NOWAIT=1: run beside another lane
  while busy; do echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done
fi
st() { local tag=$1 set=$2; shift 2; echo "=== stage_scores $tag ($set) $* $(date +%H:%M)"
       env $LOCK "$@" python stage_scores.py --questions "sets/$set.json" --tag "$tag" | tail -3; }
ev() { local tag=$1; shift; echo "=== eval_v3 $tag $* $(date +%H:%M)"; env $LOCK "$@" python eval_v3.py --tag "$tag" \
       ${EVARGS:-} | tail -4; }

step_backup() {
  for f in wiki.db facts.db movies.db; do
    [ -f "$f" ] && [ ! -f "backups/${f%.db}_v3sealed.db" ] && cp -p "$f" "backups/${f%.db}_v3sealed.db" \
      && echo "backed up $f -> backups/${f%.db}_v3sealed.db"
  done
  md5sum backups/*_v3sealed.db 2>/dev/null
}

step_mock() {
  python mock_llm.py selftest
  MOCK=replay env $LOCK python eval_v3.py --ids a01,a03 --tag v4_mock_replay 2>&1 | tail -3
  echo "(fake calls logged in logs/mock_calls.jsonl: $(wc -l < logs/mock_calls.jsonl 2>/dev/null || echo 0))"
}

step_reviews() {
  python review_filter.py run | tail -12
  python review_filter.py model | tail -2
  python review_filter.py sample 100
  echo "=== review analysis (up to ${REVIEW_HOURS:-10} h)"
  timeout "$(python -c "print(int(${REVIEW_HOURS:-10} * 3600))")" python reviews_analyze.py --films 40000 | tail -4
  python reviews_analyze.py aggregate
  python - <<'PY'
import facts_db as F
c = F.connect(readonly=True)
for k, q in {"films with analysed audience reviews": "select count(distinct tmdb_id) from film_aspects where source_type='audience'",
             "films with 10+ analysed audience reviews": "select count(*) from (select tmdb_id from film_aspects where source_type='audience' group by tmdb_id having max(n_reviews) >= 10)",
             "review aspects": "select count(*) from review_aspects"}.items():
    print(f"  {k:42s} {c.execute(q).fetchone()[0]:,}")
PY
}

step_ctx() {
  n=0; while [ -f results/.recent_writing ] && [ $n -lt 180 ]; do    # at most 6 h, in case that step died
    echo "ctx: waiting for the recent-films step to finish writing wiki.db $(date +%H:%M)"; sleep 120; n=$((n + 1)); done
  CTX_WORKERS=${CTX_WORKERS:-2} python chunk_context.py build --sample 3000 | tail -2
  python chunk_context.py eval --sample 3000 | tail -12
  if [ "${CTX_FULL:-1}" != 1 ]; then
    echo "=== CTX_FULL=0: sample test only (run 'bash run_v4.sh ctx' later for every film if it passed)"
  elif python -c "import json,sys; sys.exit(0 if json.load(open('results/chunk_context_sample.json'))['pass'] else 1)"; then
    echo "=== sample rule passed: contexts for every film (resumable; rerun 'bash run_v4.sh ctx' if stopped)"
    CTX_WORKERS=${CTX_WORKERS:-2} python chunk_context.py build --all | tail -2
    python chunk_context.py add && python chunk_context.py embed | tail -2 && python chunk_context.py fts
    python mb.py build-cache | tail -2
    for s in tune check; do
      st "v4_${s}_lock" $s
      st "v4_${s}_ctx" $s WIKI_KINDS=plot_c,lead_c
      st "v4_${s}_ctxkw" $s WIKI_KINDS=plot_c,lead_c WIKI_CTX_KW=1
    done
  else
    echo "=== sample rule failed: contextual chunks not built for the full index (see results/chunk_context_sample.txt)"
  fi
}

step_news() {
  python news.py fetch --source wiki --from "${NEWS_FROM:-1925}" | tail -2
  if [ -n "${NYT_API_KEY:-}" ]; then python news.py fetch --source nyt | tail -2
  else echo "(no NYT_API_KEY: Wikipedia events only; rerun 'python news.py fetch --source nyt' daily once you have a key)"; fi
  python news.py embed | tail -1
  python news.py films | tail -1
  python news.py analyze --min-headlines 30
  python news_era.py era && python news_era.py stance | tail -1 && python news_era.py analyze
  python news.py show "Jaws" | head -20
  EVARGS="--questions sets/news.json --answers" ev v4_news_off NEWS=0
  EVARGS="--questions sets/news.json --answers" ev v4_news_on NEWS=1
  EVARGS="--category why --answers" ev v4_why_lock NEWS=0
  EVARGS="--category why --answers" ev v4_why_news NEWS=1
}

step_success() {
  for arm in "lock:" "succ:SUCCESS=1" "rfix:ROUTE_FIX=1" "both:SUCCESS=1 ROUTE_FIX=1"; do
    tag=${arm%%:*}; envs=${arm#*:}
    EVARGS="--category why,compare,reviews --answers" ev "v4_an_${tag}" $envs
    EVARGS="" ev "v4_l1_${tag}" $envs
  done
}

step_gpu() {
  # 14 Oct: the judge (gemma3:27b) and movie-buff stay loaded 30 min after the previous step and
  # filled the GPU ("GPU free: 0 MiB"); unload Movie Buff's own models first (not other users').
  for m in $(ollama ps 2>/dev/null | awk 'NR>1{print $1}' | grep -E "^(movie-buff|llama3\.2|mxbai-embed-large|gemma3:27b|qwen3)"); do
    ollama stop "$m" 2>/dev/null; done
  sleep 10
  FREE=$(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits | awk -F', ' '{print $1-$2}' | head -1)
  echo "GPU free: $FREE MiB"
  nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null | head -5
  if [ "${FREE:-0}" -ge 8000 ]; then
    RR_DEVICE=cuda bash run_v3c.sh data train eval summary
  else
    echo "GPU busy again: re-ranker training skipped"
  fi
  [ "${GPU_TIMING:-1}" = 1 ] && for rep in 1 2; do
    for arm in "lock:PARALLEL=0 PLAN_ROUTE=0" "par:PARALLEL=1" "route:PLAN_ROUTE=1"; do
      tag=${arm%%:*}; envs=${arm#*:}
      echo "=== timing $tag $rep"
      env $LOCK $envs python stage_scores.py --questions sets/tune.json --group TREC --limit 20 --no-cache \
        --tag "v4_time_${tag}_${rep}" | tail -2
    done
  done
}

step_recent() {
  # 14 Oct: recent films (2023 on, any number of votes) + live Wikipedia into the search indexes (add_recent.py)
  touch results/.recent_writing          # step_ctx waits while this lane writes wiki.db
  st v4_before_tune tune; st v4_before_check check
  if [ -n "${TMDB_API_KEY:-}${TMDB_TOKEN:-}" ]; then
    python fetch_tmdb.py extra --year-from "${RECENT_FROM:-2023}" --min-votes "${RECENT_MIN_VOTES:-3}" --pages 25 | tail -6
    python fetch_tmdb.py current | tail -2
    python tools.py build | tail -2
  else
    echo "(no TMDB_API_KEY: using the films already in facts.db)"
  fi
  python add_recent.py wiki --year-from "${RECENT_FROM:-2023}" | tail -2
  python add_recent.py catalog --year-from "${RECENT_FROM:-2023}" | tail -2
  python add_recent.py passages | tail -2
  python wiki_embed.py --wiki-db wiki.db --kinds lead,plot | tail -2
  python wiki_enrich.py --n 3 | tail -2 && python wiki_embed.py --wiki-db wiki.db --kinds question | tail -2
  python wiki_sections.py embed | tail -2
  python mb.py build-cache | tail -2
  rm -f results/.recent_writing
  python add_recent.py status
  st v4_recent_tune tune; st v4_recent_check check
  python add_recent.py eval --n "${RECENT_EVAL_N:-100}" | tail -4
}

step_era() {
  # news without self-made headlines (nw2) + era x stance (news_era.py); facts.db only, no search index change
  python news.py films | tail -1 && python news.py analyze | sed -n 5,20p
  python news_era.py era && python news_era.py stance | tail -1 && python news_era.py analyze
}

step_aliases() {
  # nicknames (aliases.py). Rule (written 15 Oct, before any run): adopt ALIASES=1 if sets/aliases.json gains
  # 5+ questions and tune and check each lose at most 1. DYM / SPOILERS / WIKI_CREDIT change only the prompt
  # or the page, not retrieval, so they are reported, not gated here.
  python aliases.py build | tail -6
  st v4_alias_off aliases ALIASES=0; st v4_alias_on aliases ALIASES=1
  st v4_alias_tune tune ALIASES=1; st v4_alias_check check ALIASES=1
}

step_confidence() {
  python confidence.py fit | tail -14          # seconds; uses the stage_scores runs already in results/
}

step_adapter() {
  bash rebuild_adapter.sh --force
  tail -40 logs/adapter_rebuild.log
}

step_summary() {
{
  echo "v4 summary  $(date)"
  [ -f results/review_filter_summary.txt ] && { echo; echo "--- reviews"; head -6 results/review_filter_summary.txt; }
  [ -f results/chunk_context_sample.txt ] && { echo; echo "--- contextual chunks (sample)"; cat results/chunk_context_sample.txt; }
  [ -f results/news_link_summary.txt ] && { echo; echo "--- news"; cat results/news_link_summary.txt; }
  [ -f results/news_era_summary.txt ] && { echo; echo "--- news era x stance"; cat results/news_era_summary.txt; }
  python - <<'PY'
import glob, json, os
def latest(pat):
    fs = sorted(glob.glob(pat), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def st(tag):
    d = latest(f"results/stage_scores_*_{tag}.json")
    return (sum(r["result"]["pass"] for r in d["rows"]), len(d["rows"])) if d else None
def ev(tag):
    d = latest(f"results/v3eval_{tag}.json")
    if not d:
        return None
    rows = d["rows"]
    js = [r["judge"] for r in rows if r.get("judge")]
    g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float)) and j.get("grounded") > 0]
    gr = lambda k: (sum(1 for r in rows if r["grades"].get(k)), sum(1 for r in rows if k in r["grades"]))
    cats = {}
    for r in rows:
        j = r.get("judge") or {}
        c = cats.setdefault(r["category"], [0, 0])
        c[0] += j.get("verdict") == "pass"
        c[1] += bool(j)
    return {"pass": sum(1 for j in js if j.get("verdict") == "pass"), "judged": len(js),
            "grounded": round(sum(g) / len(g), 2) if g else None, "route": gr("route"), "data": gr("data"),
            "cats": cats}
print("\n--- contextual chunks: stage_scores passes (tune, check)")
for arm in ("lock", "ctx", "ctxkw"):
    print(f"  {arm:6s} tune {st(f'v4_tune_{arm}')}  check {st(f'v4_check_{arm}')}")
print("\n--- news (answers + judge)")
for t in ("v4_news_off", "v4_news_on", "v4_why_lock", "v4_why_news"):
    print(f"  {t:14s} {ev(t)}")
print("\n--- success / route fix (why+compare+reviews answers; level 1 on all analytic)")
for arm in ("lock", "succ", "rfix", "both"):
    print(f"  {arm:5s} answers {ev(f'v4_an_{arm}')}\n        L1 {ev(f'v4_l1_{arm}')}")
print("\n--- timings, 20 TREC questions, no cache (mean total s, two repeats)")
for arm in ("lock", "par", "route"):
    ts = []
    for rep in (1, 2):
        d = latest(f"results/stage_scores_*_v4_time_{arm}_{rep}.json")
        if d:
            tot = [r["result"]["trace"].get("total_ms") for r in d["rows"] if r["result"]["trace"].get("total_ms")]
            ts.append(round(sum(tot) / len(tot) / 1000, 1) if tot else None)
    print(f"  {arm:6s} {ts}")
PY
  [ -f results/v3c_summary.txt ] && { echo; echo "--- re-ranker (GPU)"; cat results/v3c_summary.txt; }
  [ -f results/adapter_builds.log ] && { echo; echo "--- adapter builds"; tail -5 results/adapter_builds.log; }
  [ -f results/confidence_summary.txt ] && { echo; echo "--- confidence"; head -3 results/confidence_summary.txt; tail -8 results/confidence_summary.txt; }
  python - <<'PY'
import glob, json, os
def p(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    if not fs: return None
    d = json.load(open(fs[-1])); return f"{sum(r['result']['pass'] for r in d['rows'])}/{len(d['rows'])}"
if p("v4_alias_on"):
    print(f"\n--- nicknames (ALIASES): alias set off {p('v4_alias_off')} -> on {p('v4_alias_on')}; "
          f"with ALIASES=1 tune {p('v4_alias_tune')} check {p('v4_alias_check')}")
PY
  [ -f results/add_recent_eval.txt ] && { echo; echo "--- recent films"; cat results/add_recent_eval.txt;
    python - <<'PY'
import glob, json, os
def p(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    if not fs: return "-"
    d = json.load(open(fs[-1])); return f"{sum(r['result']['pass'] for r in d['rows'])}/{len(d['rows'])}"
print(f"  stage_scores before: tune {p('v4_before_tune')} check {p('v4_before_check')} | "
      f"after adding: tune {p('v4_recent_tune')} check {p('v4_recent_check')} (rule: each loses at most 1)")
PY
  }
} > "$SUM" 2>&1
cat "$SUM"
}
# 14 Oct: steps run in the order given on the command line (they used to run in this file's order)
for s in $STEPS; do
  if declare -F "step_$s" >/dev/null; then echo "=== step $s $(date +%H:%M)"; "step_$s"
  else echo "unknown step: $s (steps: backup mock reviews ctx news era success gpu recent aliases confidence adapter summary)"; fi
done
echo "=== run_v4.sh finished $(date)"
