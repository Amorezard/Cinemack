#!/usr/bin/env bash
# E3 (v2 plan, Mon 28 Sep): do the generated descriptions help, and can HyDE and the re-ranker go?
# The ~57k generated descriptions are their own ranked list in the RRF fusion (WIKI_Q=rrf),
# not merged into each film's best distance (they match on style as well as content).
#   q_tmdb50           re-ranker as in E1 (50 TMDB overviews)     vs E1 "wiki"  -> effect of descriptions
#   q_norerank         no re-ranker                               vs E2 "norerank"
#   q_merge_norerank   descriptions merged by best distance       vs q_norerank (the style problem, measured)
#   q_ss_norerank      only the "short" and "scene" styles        vs q_norerank (is "fuzzy" noise?)
#   q_nohyde           no guessed plot (HYDE=0), re-ranker on     vs q_tmdb50   -> can HyDE go?
#   q_nohyde_norerank  no HyDE, no re-ranker: the fastest setup
# Then no-cache timing on 20 TREC questions, and gate_sim.py: which questions could skip
# the re-ranker without losing any.
# bash run_e3.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e3   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e3.sh
set -u
cd "$(dirname "$0")"
LOG=results/e3.log
SUM=results/e3_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e3.sh started $(date)"
if [ "${1:-}" != "--summary" ]; then   # --summary: only rebuild the summary from saved runs
python wiki_enrich.py --fix-styles        # one-time: record each description's style

run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env WIKI=1 WIKI_KINDS=plot,lead WIKI_Q=rrf "${envs[@]}" \
    python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}

for set in tune check; do
  run q_tmdb50 $set RERANK_TEXT=tmdb RERANK_N=50
  run q_norerank $set RERANK=0
  run q_merge_norerank $set RERANK=0 WIKI_Q=merge
  run q_ss_norerank $set RERANK=0 WIKI_QSTYLES=short,scene
  run q_nohyde $set HYDE=0 RERANK_TEXT=tmdb RERANK_N=50
  run q_nohyde_norerank $set HYDE=0 RERANK=0
done
run time_q_tmdb50 tune RERANK_TEXT=tmdb RERANK_N=50 -- --no-cache --group TREC --limit 20
run time_q_fast tune HYDE=0 RERANK=0 -- --no-cache --group TREC --limit 20
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E3 summary  $(date)"
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = Wikipedia passages (E1), B = + generated descriptions"        ${set}_wiki       ${set}_q_tmdb50
    cmp "$S: no re-ranker. A = passages only (E2), B = + descriptions"        ${set}_norerank   ${set}_q_norerank
    cmp "$S: no re-ranker. A = descriptions as own list, B = merged by distance" ${set}_q_norerank ${set}_q_merge_norerank
    cmp "$S: no re-ranker. A = all styles, B = short + scene only"            ${set}_q_norerank ${set}_q_ss_norerank
    cmp "$S: A = descriptions + re-ranker, B = same without HyDE"             ${set}_q_tmdb50   ${set}_q_nohyde
    cmp "$S: A = descriptions + re-ranker + HyDE, B = no HyDE, no re-ranker"  ${set}_q_tmdb50   ${set}_q_nohyde_norerank
  done
  cmp "SPEED (no cache, 20 TREC): A = E2 tmdb50 (passages, re-ranker, HyDE), B = descriptions, no HyDE, no re-ranker" \
      tune_only_time_tmdb50 tune_only_time_q_fast
  cmp "SPEED: A = descriptions + re-ranker + HyDE, B = descriptions, no HyDE, no re-ranker" \
      tune_only_time_q_tmdb50 tune_only_time_q_fast
  echo; echo "##### CONFIDENCE GATE: skip the re-ranker only when search is sure (rule chosen on tune, checked on check)"
  python gate_sim.py "$(latest tune_q_tmdb50)" "$(latest tune_q_norerank)" \
                     "$(latest check_q_tmdb50)" "$(latest check_q_norerank)"
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e3.sh finished $(date)"
