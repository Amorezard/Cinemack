#!/usr/bin/env bash
# v8.1 night 3 (13 Nov 2026), v8.1 worktree only. CPU only; no model call, no GPU, no outside service.
#   slice 4.5 worldwide forecast + 50/80% ranges per horizon, model card   (minutes)
#   slice 5.2a MovieLens 32M time-ordered recommendation benchmark         (tens of minutes; reads the live folder's
#                                                                           data/movielens/ml-32m.zip and movies.db read-only)
# Each step commits its own results on branch v8.1.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
[ "$(git branch --show-current)" = "v8.1" ] || { echo "not on branch v8.1; stopping"; exit 1; }
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night3.txt
exec > >(tee "$R") 2>&1
TRAILER="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NrJonUbBSzhd6Rp6pw6H1k"
echo "=== v8.1 night 3  $(date)  ($(git log -1 --format='%h %s' | cut -c1-70))"
echo "--- slice 4.5 forecast (log logs/v81_forecast.log)"
t0=$(date +%s); nice -n 10 timeout 3h python tools/v81_forecast.py > logs/v81_forecast.log 2>&1; rc=$?
echo "  exit $rc after $(( $(date +%s) - t0 )) s"
if [ $rc = 0 ]; then
  sed 's/^/  /' results/v81_forecast.txt | cut -c1-220
  git add results/v81_forecast.txt results/v81_forecast_samples.json planning/cinemack-v8.1/box_office/forecast_eval.json \
          planning/cinemack-v8.1/box_office/box_office_model_card.md
  git commit -q -m "v8.1 slice 4.5: worldwide forecast with 50/80% ranges at d90 / d7 (d0 reference), model card

$TRAILER" && echo "  committed $(git log -1 --format=%h)"
else echo "  FORECAST FAILED - nothing committed:"; tail -15 logs/v81_forecast.log | cut -c1-200 | sed 's/^/    /'; fi
echo "--- slice 5.2a recommendation benchmark (log logs/v81_recs.log)"
t0=$(date +%s); nice -n 10 timeout 5h python tools/v81_recs_benchmark.py > logs/v81_recs.log 2>&1; rc=$?
echo "  exit $rc after $(( $(date +%s) - t0 )) s"
if [ $rc = 0 ]; then
  sed 's/^/  /' results/v81_recs_benchmark.txt | cut -c1-200
  git add results/v81_recs_benchmark.txt results/v81_recs_benchmark.json
  git commit -q -m "v8.1 slice 5.2a: time-ordered MovieLens 32M recommendation benchmark (current mix vs time-safe vs v2)

$TRAILER" && echo "  committed $(git log -1 --format=%h)"
else echo "  BENCHMARK FAILED - nothing committed:"; tail -20 logs/v81_recs.log | cut -c1-200 | sed 's/^/    /'; fi
git add results/v81_night3.txt 2>/dev/null; git commit -q -m "v8.1 night 3 log

$TRAILER" 2>/dev/null
echo "=== v8.1 night 3 done $(date)"
