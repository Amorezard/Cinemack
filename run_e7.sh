#!/usr/bin/env bash
# E7 (v2 plan, Tue 29 Sep): a shorter re-ranker prompt for speed, and a tighter
# Wikipedia passage in the answer. Baseline = current defaults (= E6 e6_planfast).
# The re-ranker is ~11 s of the ~16 s per TREC question and its time is mostly
# reading the prompt, so fewer characters per film should cut it roughly in step.
#   e7_rc250   each film shown with 250 characters instead of 400 (50 films)
#   e7_rc300   300 characters (50 films)
#   e7_n40     40 films at 400 characters
# Then no-cache timing on 20 TREC questions for each, and the answer judge:
#   judge v2_pf        current defaults (ANSWER_WIKI_CHARS=700)
#   judge v2_aw350     Wikipedia passages in the answer cut to 350 characters
# bash run_e7.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e7   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e7.sh
set -u
cd "$(dirname "$0")"
LOG=results/e7.log
SUM=results/e7_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e7.sh started $(date)"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_CHARS RERANK_AGREE RERANK_GAP RERANK_ENGINE RRF_WEIGHTS RRF_K PLAN_FAST HYDE HYBRID \
      ANSWER_WIKI ANSWER_WIKI_CHARS ANSWER_STYLE

grep -q 'environ.get("PLAN_FAST", "1")' retrieve.py || { echo "retrieve.py is old (PLAN_FAST default not 1) - copy the new one up first"; exit 1; }

if [ "${1:-}" != "--summary" ]; then
while pgrep -f "run_e6.sh" >/dev/null; do echo "waiting for run_e6.sh ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}
for set in tune check; do
  run e7_rc250 $set RERANK_WIKI_CHARS=250 RERANK_CHARS=250
  run e7_rc300 $set RERANK_WIKI_CHARS=300 RERANK_CHARS=300
  run e7_n40   $set RERANK_N=40
done
run time_e7_rc250 tune RERANK_WIKI_CHARS=250 RERANK_CHARS=250 -- --no-cache --group TREC --limit 20
run time_e7_rc300 tune RERANK_WIKI_CHARS=300 RERANK_CHARS=300 -- --no-cache --group TREC --limit 20
run time_e7_n40   tune RERANK_N=40 -- --no-cache --group TREC --limit 20
echo "=== judge current defaults $(date +%H:%M)"
python -u judge.py score --label v2_pf | tail -8
echo "=== judge ANSWER_WIKI_CHARS=350 $(date +%H:%M)"
ANSWER_WIKI_CHARS=350 python -u judge.py score --label v2_aw350 | tail -8
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E7 summary  $(date)"
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = current defaults, B = 250 characters per film" ${set}_e6_planfast ${set}_e7_rc250
    cmp "$S: A = current defaults, B = 300 characters per film" ${set}_e6_planfast ${set}_e7_rc300
    cmp "$S: A = current defaults, B = 40 films"                ${set}_e6_planfast ${set}_e7_n40
  done
  cmp "SPEED (no cache, 20 TREC): A = current defaults, B = 250 characters" tune_only_time_e6_planfast tune_only_time_e7_rc250
  cmp "SPEED (no cache, 20 TREC): A = current defaults, B = 300 characters" tune_only_time_e6_planfast tune_only_time_e7_rc300
  cmp "SPEED (no cache, 20 TREC): A = current defaults, B = 40 films"       tune_only_time_e6_planfast tune_only_time_e7_n40
  echo; echo "##### ANSWER QUALITY (judge.py): A = TMDB only (E5), B = current defaults, C = Wikipedia passages cut to 350"
  python compare_night.py --judge results/judge-v2_ans0.json results/judge-v2_pf.json results/judge-v2_aw350.json
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e7.sh finished $(date)"
