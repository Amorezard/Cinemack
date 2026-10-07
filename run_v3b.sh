#!/usr/bin/env bash
# v3, day/night 2 (Mon 5 Oct). Needs run_v3a.sh's data (facts.db with TMDB, sections, Wikidata).
#
# Steps (each can be run alone: bash run_v3b.sh <step> ...):
#   prep      money table + MovieLens audience stats (tools.py build), facts.db status
#   tools     analytic set, level 1 (route + data, no answers): TOOLS=0 (v2) vs TOOLS=1
#   latency   PARALLEL A/B (tune+check must be identical) and timing; RERANK_MODEL candidates
#   answers   analytic set, level 2: answers from movie-buff + judge (gemma3:27b), TOOLS=0 vs 1
#   reviews   review intelligence (llama3.2): audience + critic aspects for the 800 most-voted films
#   summaries section summaries (400 films), embed, synthetic queries, flat vs summary-first
#   personal  recommendation leave-out test on MovieLens users (CPU)
#   conv      multi-turn set with CONV=1 TOOLS=1 together (sanity)
#   summary   results/v3b_summary.txt
#
# Decision rules, set before any result (5 Oct):
#   TOOLS=1      data check (strings a correct answer needs are in the context) >= +30 points
#                over TOOLS=0 on the analytic set, judge pass rate not lower, and the "tot"
#                questions unchanged. (TOOLS does not touch retrieval, so tune/check can't move.)
#   PARALLEL=1   tune and check identical (passes and ranks) and mean time per TREC question
#                at least 0.2 s lower.
#   RERANK_MODEL a smaller model replaces movie-buff only if tune + check passes drop by at most 1
#                AND the re-rank step is at least 40% faster (including any model reloads).
#   summaries    adopted per task only if hit@1 is not lower than flat passages and it is cheaper.
#   PERSONAL=1   the hybrid scorer beats popularity and content-only on recall@10.
#
#   tmux new -s v3b
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_v3b.sh
set -u
cd "$(dirname "$0")"
LOG=results/v3b.log
SUM=results/v3b_summary.txt
STEPS=${*:-prep tools latency answers reviews summaries personal conv summary}
RR_MODELS=${RR_MODELS:-qwen3:4b qwen3:8b}
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3b.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
run() {  # $1 = tag, $2 = set, rest = VAR=value settings
  local tag=$1 set=$2; shift 2
  echo "=== $tag $set $* $(date +%H:%M)"
  env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4
}
while pgrep -f "stage_scores.py|eval_v3.py" >/dev/null; do echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done

if has prep; then
  for f in tools.py eval_v3.py personal.py reviews_analyze.py summarize.py feedback.py sets/analytic.json; do
    [ -f "$f" ] || { echo "MISSING $f - copy it up first"; exit 1; }
  done
  python tools.py build || { echo "tools.py build FAILED"; exit 1; }
  python facts_db.py status
fi

if has tools; then
  TOOLS=0 python eval_v3.py --tag l1_v2 | tail -22
  TOOLS=1 python eval_v3.py --tag l1_v3 | tail -22
fi

if has latency; then
  run v3b_tune_par  tune  PARALLEL=1
  run v3b_check_par check PARALLEL=1
  echo "=== timing $(date +%H:%M)"
  PARALLEL=1 python stage_scores.py --questions sets/tune.json --group TREC --limit 20 --no-cache --tag time_par_trec | tail -3
  for m in $RR_MODELS; do
    t=$(echo "$m" | tr ':.' '__')
    ollama pull "$m" >/dev/null 2>&1 || { echo "!!! could not pull $m - skipping it"; continue; }
    run "v3b_tune_rr_$t"  tune  RERANK_MODEL=$m
    run "v3b_check_rr_$t" check RERANK_MODEL=$m
    RERANK_MODEL=$m python stage_scores.py --questions sets/tune.json --group TREC --limit 20 --no-cache --tag "time_rr_${t}_trec" | tail -3
  done
  echo "--- GPU memory after the re-ranker runs"; nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null; ollama ps 2>/dev/null
fi

if has answers; then
  TOOLS=0 python eval_v3.py --tag l2_v2 --answers | tail -22
  TOOLS=1 python eval_v3.py --tag l2_v3 --answers | tail -22
  python eval_v3.py --compare results/v3eval_l2_v2.json results/v3eval_l2_v3.json > results/v3b_answers_compare.txt
fi

if has reviews; then
  python reviews_analyze.py --films 800 | tail -5
fi

if has summaries; then
  python summarize.py build --films 400 | tail -3
  python summarize.py embed | tail -2
  python summarize.py make-queries --n 200 | tail -2
  python summarize.py eval
fi

if has personal; then
  python personal.py eval 150
fi

if has conv; then
  # CONV after the 5 Oct sequel fix (lookup_named series=True + resolved search wording).
  # Developed after seeing v3a's multi-turn results, so this set is no longer unseen for CONV;
  # the sealed check needs new conversations.
  run v3b_mt_conv2 multiturn CONV=1
  run v3b_mt_all   multiturn CONV=1 TOOLS=1 PARALLEL=1
fi

if has summary; then
{
  echo "v3b summary  $(date)"
  echo
  echo "##### ANALYTIC SET, level 1 (route / data in context), v2 vs v3"
  python eval_v3.py --report results/v3eval_l1_v2.json 2>/dev/null | tail -16
  python eval_v3.py --report results/v3eval_l1_v3.json 2>/dev/null | tail -16
  echo
  echo "##### ANALYTIC SET, level 2 (answers + judge)"
  cat results/v3b_answers_compare.txt 2>/dev/null | tail -60
  echo
  echo "##### LATENCY"
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def passes(tag):
    d = latest(tag)
    return (sum(r["result"]["pass"] for r in d["rows"]), len(d["rows"]), (d.get("cache") or {}).get("miss")) if d else None
base = {"tune": "v3a_tune_v2", "check": "v3a_check_v2"}
for s in ("tune", "check"):
    print(f"  {s}: locked {passes(base[s])}  PARALLEL {passes('v3b_' + s + '_par')}")
    for t in glob.glob(f"results/stage_scores_*_v3b_{s}_rr_*.json"):
        tag = t.split(f"_v3b_{s}_rr_")[-1][:-5]
        print(f"     RERANK_MODEL {tag}: {passes('v3b_' + s + '_rr_' + tag)}")
for tag in ["time_lock_trec", "time_norr_trec", "time_par_trec"] + \
           sorted(os.path.basename(f).split("_only_")[-1][:-5] for f in glob.glob("results/stage_scores_*_time_rr_*_trec.json")):
    d = latest(tag)
    if not d:
        continue
    tr = [r["result"]["trace"] for r in d["rows"]]
    def avg(k):
        v = [x.get(k) for x in tr if isinstance(x.get(k), (int, float))]
        return sum(v) / len(v) / 1000 if v else float("nan")
    rr = [x["rerank_ms"] for x in tr if x.get("rerank_ms")]
    p = sum(r["result"]["pass"] for r in d["rows"])
    print(f"  {tag:24s} pass {p}/{len(tr)}  total {avg('total_ms'):.1f}s  plan {avg('plan_ms'):.1f}s  "
          f"search {avg('search_ms'):.2f}s  rerank {sum(rr) / len(rr) / 1000 if rr else 0:.1f}s on {len(rr)}")
PY
  echo
  echo "##### REVIEWS / SUMMARIES / PERSONAL"
  python facts_db.py status | grep -E "review|aspects|summar|sum_vec"
  cat results/summaries_eval.txt 2>/dev/null
  cat results/personal_eval.txt 2>/dev/null
  echo
  echo "##### MULTI-TURN: CONV after the sequel fix, then everything on"
  python stage_scores.py --report "$(ls -t results/stage_scores_*_v3b_mt_conv2.json 2>/dev/null | head -1)" 2>/dev/null | tail -6
  python stage_scores.py --report "$(ls -t results/stage_scores_*_v3b_mt_all.json 2>/dev/null | head -1)" 2>/dev/null | tail -6
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_v3b.sh finished $(date)"
