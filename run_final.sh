#!/usr/bin/env bash
# The sealed final test (run ONCE, after the winners are chosen):
#
#   cd ~/movie-buff/rag && bash run_final.sh
#
#   before = the pipeline as it was at the start of 23 Sep (no re-ranker cap,
#            hard year filters, none of the search batch)
#   after  = the chosen configuration (the defaults in retrieve.py, plus
#            anything set in FINAL_ENV below)
# Both run with --no-cache so the timings are real. About 20-30 minutes.
# Writes results/final_summary.txt, then make_site_stats.py updates the
# numbers on the How It Works page.

cd "${RAG:-$HOME/movie-buff/rag}" || exit 1
VENV="${VENV:-$HOME/venvs/movie-rag}"
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"

BEFORE="RERANK_CAP=0 SOFT_FILTERS=0 UNKNOWN_FILM=0 MULTI_FILM=0 HYDE=0 HYBRID=0 RERANK_N=30 RERANK_CHARS=220 GUESS_WIDE=0"
FINAL_ENV="${FINAL_ENV:-}"          # e.g. FINAL_ENV="GUESS_WIDE=0" if a switch lost overnight
F="python -u stage_scores.py --questions sets/final.json --final --no-cache --tag"

echo "--- final_before $(date +%H:%M)"
env $BEFORE $F final_before > results/final_before.txt 2>&1
echo "    exit $?  $(grep -E 'pass [0-9]' results/final_before.txt | tr '\n' ' ')"
echo "--- final_after  $(date +%H:%M)  ${FINAL_ENV}"
env $FINAL_ENV $F final_after > results/final_after.txt 2>&1
echo "    exit $?  $(grep -E 'pass [0-9]' results/final_after.txt | tr '\n' ' ')"

{
  echo "=== SEALED FINAL SET: start of 23 Sep vs final configuration (no cache, real timings)"
  python compare_night.py results/stage_scores_*_final_before.json results/stage_scores_*_final_after.json
} > results/final_summary.txt 2>&1
cat results/final_summary.txt
python make_site_stats.py
