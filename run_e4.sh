#!/usr/bin/env bash
# E4 (v2 plan, Mon 28 Sep): confirm the candidate v2 settings, and measure speed with the search fix.
#   f_wiki50  descriptions as own list + HyDE + re-ranker reading the Wikipedia passage (50 films, 400 chars)
#   f_auto    the same, but the re-ranker only runs when search is unsure (RERANK=auto, agree >= 1)
# Compared with E3 q_tmdb50 (descriptions + HyDE + re-ranker on TMDB overviews).
# Also: diff_pool.py on the E1 vs E2 check runs (why 3 films left the pool).
# bash run_e4.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e4   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e4.sh
set -u
cd "$(dirname "$0")"
LOG=results/e4.log
SUM=results/e4_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e4.sh started $(date)"
V2="WIKI=1 WIKI_KINDS=plot,lead WIKI_Q=rrf HYDE=1 RERANK_TEXT=wiki RERANK_N=50 RERANK_WIKI_CHARS=400"

if [ "${1:-}" != "--summary" ]; then
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env $V2 "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}
for set in tune check; do
  run f_wiki50 $set RERANK=1
  run f_auto $set RERANK=auto
done
run time_f_wiki50 tune RERANK=1 -- --no-cache --group TREC --limit 20
run time_f_auto tune RERANK=auto -- --no-cache --group TREC --limit 20
run time_f_auto_own tune RERANK=auto -- --no-cache --group OWN --limit 20
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E4 summary  $(date)"
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = E3 descriptions + HyDE + re-ranker on TMDB, B = re-ranker reads Wikipedia (f_wiki50)" ${set}_q_tmdb50 ${set}_f_wiki50
    cmp "$S: A = f_wiki50 (always re-rank), B = f_auto (re-rank only when unsure)"                     ${set}_f_wiki50  ${set}_f_auto
  done
  cmp "SPEED (no cache, 20 TREC): A = E3 q_tmdb50 (old search), B = f_wiki50 (new search)" tune_only_time_q_tmdb50 tune_only_time_f_wiki50
  cmp "SPEED (no cache, 20 TREC): A = f_wiki50, B = f_auto"                               tune_only_time_f_wiki50 tune_only_time_f_auto
  cmp "SPEED (no cache, 20 own): A = E1 WIKI=1 own questions, B = f_auto"                tune_only_time_wiki    tune_only_time_f_auto_own
  echo; echo "##### POOL CHECK: E1 check vs E2 check (no re-ranker) - which channel moved?"
  python diff_pool.py "$(latest check_wiki)" "$(latest check_norerank)"
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e4.sh finished $(date)"
