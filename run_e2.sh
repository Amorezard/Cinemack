#!/usr/bin/env bash
# E2 (v2 plan, Mon 28 Sep): what should the re-ranker read, and how many films?
#   B  = last night's WIKI=1 run (re-ranker reads 50 TMDB overviews)
#   both20 = 20 films, each: 200 chars of TMDB overview + the Wikipedia passage that matched
#   tmdb20 = 20 films, TMDB overview only (separates "fewer films" from "better text")
#   wiki50 = 50 films, each shown its Wikipedia passage (400 chars) instead of the overview: same prompt size as B
#   norerank = no re-ranker at all: the fused hybrid search order is the answer (RERANK=0)
# Then the same speed test as E1, on 20 TREC questions (the slow, descriptive kind).
# Uses Wikipedia passages only (WIKI_KINDS=plot,lead, WIKI_Q=off), so generated descriptions do not mix in.
# bash run_e2.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e2   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e2.sh
set -u
cd "$(dirname "$0")"
LOG=results/e2.log
SUM=results/e2_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e2.sh started $(date)"

run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env WIKI=1 WIKI_KINDS=plot,lead WIKI_Q=off "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}

if [ "${1:-}" != "--summary" ]; then   # --summary: only rebuild the summary from saved runs
for set in tune check; do
  run norerank $set RERANK=0
  run both20 $set RERANK_TEXT=both RERANK_N=20
  run tmdb20 $set RERANK_TEXT=tmdb RERANK_N=20
  run wiki50 $set RERANK_TEXT=wiki RERANK_N=50 RERANK_WIKI_CHARS=400
done
run time_both20 tune RERANK_TEXT=both RERANK_N=20 -- --no-cache --group TREC --limit 20
run time_tmdb50 tune RERANK_TEXT=tmdb RERANK_N=50 -- --no-cache --group TREC --limit 20
run time_norerank tune RERANK=0 -- --no-cache --group TREC --limit 20
fi

{
  echo "E2 summary  $(date)"
  for set in tune check; do
    for v in norerank both20 tmdb20 wiki50; do
      echo; echo "##### ${set^^}: A = WIKI=1 re-ranker on 50 TMDB overviews (E1),  B = $v"
      python compare_stage.py "$(latest ${set}_wiki)" "$(latest ${set}_$v)"
    done
  done
  echo; echo "##### SPEED (no cache, 20 TREC tune questions): A = tmdb50 (v1 re-ranker + Wikipedia search), B = both20"
  python compare_stage.py "$(latest tune_only_time_tmdb50)" "$(latest tune_only_time_both20)"
  echo; echo "##### SPEED: A = tmdb50, B = no re-ranker"
  python compare_stage.py "$(latest tune_only_time_tmdb50)" "$(latest tune_only_time_norerank)"
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e2.sh finished $(date)"
