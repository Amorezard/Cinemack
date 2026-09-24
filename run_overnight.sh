#!/usr/bin/env bash
# Overnight run, 23-24 Sep 2026. Start it in tmux, then go to bed:
#
#   tmux attach -t trec        (or: tmux new -s night)
#   cd ~/movie-buff/rag && bash run_overnight.sh
#   Ctrl-b d
#
# Morning:  cat results/overnight_summary.txt
#
# Runs (each logs to results/night_<name>.txt; a failure doesn't stop the rest):
#   1. keyword index (build_fts.py)
#   2. tune set: base (the search batch off), all (all on), then all-but-one
#      for each change, so each change's own effect is visible
#   3. check set: base and all
#   4. answer grading with judge.py (change 3): base, all, all + answer styles (change 15)
#   5. run_extra.sh, if present (steps added later)
# Model calls are cached (results/llm_cache.sqlite), so later runs reuse
# earlier identical calls. Expect roughly 3-4 hours in total.

cd "${RAG:-$HOME/movie-buff/rag}" || exit 1
VENV="${VENV:-$HOME/venvs/movie-rag}"
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
LOG=results/overnight.log
mkdir -p results
echo "=== overnight run started $(date)" | tee -a "$LOG"

OFF="UNKNOWN_FILM=0 MULTI_FILM=0 HYDE=0 HYBRID=0 RERANK_N=30 RERANK_CHARS=220 GUESS_WIDE=0"

step () {   # step <name> <env...> -- <command...>
  local name=$1; shift
  local envs=()
  while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  echo "--- $name  $(date +%H:%M)  ${envs[*]}" | tee -a "$LOG"
  env "${envs[@]}" "$@" > "results/night_${name}.txt" 2>&1
  echo "    exit $?  $(date +%H:%M)  $(grep -E 'pass [0-9]' results/night_${name}.txt | tr '\n' ' ')" | tee -a "$LOG"
}

step fts            -- python build_fts.py

T="python -u stage_scores.py --questions sets/tune.json --tag"
step base           $OFF                 -- $T night_base
step all            HYDE=1               -- $T night_all
step no_unknown     UNKNOWN_FILM=0       -- $T night_no_unknown
step no_multi       MULTI_FILM=0         -- $T night_no_multi
step no_hyde        HYDE=0               -- $T night_no_hyde
step no_hybrid      HYBRID=0             -- $T night_no_hybrid
step n30            RERANK_N=30          -- $T night_n30
step rc220          RERANK_CHARS=220     -- $T night_rc220
step no_guesswide   GUESS_WIDE=0         -- $T night_no_guesswide

C="python -u stage_scores.py --questions sets/check.json --tag"
step check_base     $OFF                 -- $C night_check_base
step check_all      HYDE=1               -- $C night_check_all

step judge_base     $OFF                 -- python -u judge.py score --label night_base
step judge_all      HYDE=1               -- python -u judge.py score --label night_all
step judge_style    ANSWER_STYLE=1       -- python -u judge.py score --label night_style

# Anything added later tonight (copied up as run_extra.sh) runs here.
if [ -f run_extra.sh ]; then
  echo "--- run_extra.sh  $(date +%H:%M)" | tee -a "$LOG"
  bash run_extra.sh >> "$LOG" 2>&1
fi

{
  echo "=== TUNE SET (first row is the reference)"
  python compare_night.py \
    results/stage_scores_*_night_base.json results/stage_scores_*_night_all.json \
    results/stage_scores_*_night_no_unknown.json results/stage_scores_*_night_no_multi.json \
    results/stage_scores_*_night_no_hyde.json results/stage_scores_*_night_no_hybrid.json \
    results/stage_scores_*_night_n30.json results/stage_scores_*_night_rc220.json \
    results/stage_scores_*_night_no_guesswide.json
  echo
  echo "=== CHECK SET"
  python compare_night.py results/stage_scores_*_night_check_base.json results/stage_scores_*_night_check_all.json
  echo
  echo "=== ANSWER QUALITY (judge.py, own single-turn questions)"
  python compare_night.py --judge results/judge-night_base.json results/judge-night_all.json results/judge-night_style.json
  [ -f results/extra_summary.txt ] && { echo; echo "=== EXTRA"; cat results/extra_summary.txt; }
  echo
  echo "=== log"
  cat "$LOG"
} > results/overnight_summary.txt 2>&1

echo "=== finished $(date). Summary: results/overnight_summary.txt" | tee -a "$LOG"
