#!/usr/bin/env bash
# E6 (v2 plan, Mon 28 Sep night): faster and more accurate re-ranking and search.
# All on top of the new defaults (E5 v2_default), one change at a time:
#   e6_cross     cross-encoder re-ranker (bge-reranker-v2-m3) on every description, no LLM re-ranker
#   e6_crossllm  gate as default; when it re-ranks, the cross-encoder keeps 10 films and the LLM picks
#   e6_planfast  planner no longer copies the question into search_query; 1-2 sentence guessed plot
#   e6_fusion    search lists weighted by fusion_sim.py (only if it finds weights that help)
# Then no-cache timing on 20 TREC questions, and how often the planner's JSON was cut off.
# Waits for run_e5.sh if it is still running.
# bash run_e6.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e6   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e6.sh
set -u
cd "$(dirname "$0")"
LOG=results/e6.log
SUM=results/e6_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e6.sh started $(date)"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_AGREE RERANK_GAP RERANK_ENGINE RRF_WEIGHTS RRF_K PLAN_FAST HYDE HYBRID ANSWER_WIKI ANSWER_STYLE

FUS=$(python fusion_sim.py --best "$(latest tune_f_wiki50)" "$(latest check_f_wiki50)")
echo "fusion_sim recommends: '${FUS:-none}'"

if [ "${1:-}" != "--summary" ]; then
while pgrep -f "run_e5.sh" >/dev/null; do echo "waiting for run_e5.sh ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}
for set in tune check; do
  run e6_cross $set RERANK=1 RERANK_ENGINE=cross
  run e6_crossllm $set RERANK_ENGINE=cross_llm
  run e6_planfast $set PLAN_FAST=1
  [ -n "$FUS" ] && run e6_fusion $set RRF_WEIGHTS="${FUS%;*}" RRF_K="${FUS#*;}"
done
run time_e6_cross tune RERANK=1 RERANK_ENGINE=cross -- --no-cache --group TREC --limit 20
run time_e6_crossllm tune RERANK_ENGINE=cross_llm -- --no-cache --group TREC --limit 20
run time_e6_planfast tune PLAN_FAST=1 -- --no-cache --group TREC --limit 20
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E6 summary  $(date)"
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = new defaults (LLM re-ranker, gate), B = cross-encoder on every description" ${set}_v2_default ${set}_e6_cross
    cmp "$S: A = LLM re-ranker always (f_wiki50), B = cross-encoder always"                ${set}_f_wiki50   ${set}_e6_cross
    cmp "$S: A = new defaults, B = gate + cross-encoder top 10 + LLM"                      ${set}_v2_default ${set}_e6_crossllm
    cmp "$S: A = new defaults, B = faster planner (PLAN_FAST)"                             ${set}_v2_default ${set}_e6_planfast
    [ -n "$FUS" ] && cmp "$S: A = new defaults, B = weighted fusion $FUS"                  ${set}_v2_default ${set}_e6_fusion
  done
  cmp "SPEED (no cache, 20 TREC): A = new defaults, B = cross-encoder"            tune_only_time_v2_trec tune_only_time_e6_cross
  cmp "SPEED (no cache, 20 TREC): A = new defaults, B = cross-encoder + LLM"      tune_only_time_v2_trec tune_only_time_e6_crossllm
  cmp "SPEED (no cache, 20 TREC): A = new defaults, B = faster planner"           tune_only_time_v2_trec tune_only_time_e6_planfast
  echo; echo "##### CROSS-ENCODER health (errors mean it fell back to the LLM re-ranker)"
  python - "$(latest tune_e6_cross)" "$(latest tune_only_time_e6_cross)" <<'PY'
import json, sys
for p in sys.argv[1:]:
    rows = [r["result"]["trace"] for r in json.load(open(p))["rows"]]
    errs = [t["cross_error"] for t in rows if "cross_error" in t]
    ms = [t["cross_ms"] for t in rows if "cross_ms" in t]
    print(f"{p}: cross ran {len(ms)}x, mean {sum(ms)/max(len(ms),1):.0f} ms; errors {len(errs)} {errs[:1]}")
PY
  echo; echo "##### PLANNER: questions where the plan failed and the regex path was used"
  python - "$(latest tune_v2_default)" "$(latest tune_e6_planfast)" "$(latest check_v2_default)" "$(latest check_e6_planfast)" <<'PY'
import json, sys, collections
for p in sys.argv[1:]:
    c = collections.Counter(r["result"]["trace"].get("path", "?") for r in json.load(open(p))["rows"])
    print(f"{p}: {dict(c)}")
PY
  echo; echo "##### FUSION WEIGHTS (fusion_sim.py on f_wiki50)"
  python fusion_sim.py "$(latest tune_f_wiki50)" "$(latest check_f_wiki50)"
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e6.sh finished $(date)"
