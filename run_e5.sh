#!/usr/bin/env bash
# E5 (v2 plan, Mon 28 Sep): verify the new defaults, try a cheaper re-ranker, and grade answers.
#   v2_default  no settings at all = the new defaults (Wikipedia re-ranker, gate agree>=1 gap>=0.05,
#               descriptions as own list, HyDE). Expected from gate_sim: tune 76/115, check 18/33.
#   v2_n30      same, re-ranker reads 30 films instead of 50 (cheaper prompt)
#   judge       answer quality with and without Wikipedia details in the answer (ANSWER_WIKI)
#   speed       no-cache timing of the defaults on 20 TREC and 20 own questions
# bash run_e5.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e5   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e5.sh
set -u
cd "$(dirname "$0")"
LOG=results/e5.log
SUM=results/e5_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e5.sh started $(date)"
# make sure no setting from an earlier shell leaks in
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_AGREE RERANK_GAP HYDE HYBRID ANSWER_WIKI ANSWER_STYLE

if [ "${1:-}" != "--summary" ]; then
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}
for set in tune check; do
  run v2_default $set
  run v2_n30 $set RERANK_N=30
done
run time_v2_trec tune -- --no-cache --group TREC --limit 20
run time_v2_own tune -- --no-cache --group OWN --limit 20
echo "=== judge ANSWER_WIKI=0 $(date +%H:%M)"
ANSWER_WIKI=0 python -u judge.py score --label v2_ans0 | tail -8
echo "=== judge ANSWER_WIKI=1 $(date +%H:%M)"
ANSWER_WIKI=1 python -u judge.py score --label v2_ans1 | tail -8
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E5 summary  $(date)"
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = f_wiki50 (always re-rank), B = new defaults (gate agree>=1 gap>=0.05)" ${set}_f_wiki50 ${set}_v2_default
    cmp "$S: A = new defaults (50 films), B = re-ranker reads 30 films"                ${set}_v2_default ${set}_v2_n30
  done
  cmp "SPEED (no cache, 20 TREC): A = f_wiki50 (always re-rank), B = new defaults"   tune_only_time_f_wiki50 tune_only_time_v2_trec
  cmp "SPEED (no cache, 20 own): A = v1 + Wikipedia search (E1), B = new defaults"   tune_only_time_wiki     tune_only_time_v2_own
  echo; echo "##### ANSWER QUALITY (judge.py, gemma3:27b): A = TMDB context only, B = + Wikipedia intro and matched passage"
  python compare_night.py --judge results/judge-v2_ans0.json results/judge-v2_ans1.json
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e5.sh finished $(date)"
