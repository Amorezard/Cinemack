#!/usr/bin/env bash
# v8.1 night 2 (13 Nov 2026), v8.1 worktree only. CPU only; no model call, no GPU, no outside service, read-only on
# facts.db (the worktree's copy). Slice 4.4 factor study, then its results are committed on branch v8.1.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
[ "$(git branch --show-current)" = "v8.1" ] || { echo "not on branch v8.1; stopping"; exit 1; }
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night2.txt
exec > >(tee "$R") 2>&1
echo "=== v8.1 night 2  $(date)  ($(git log -1 --format='%h %s' | cut -c1-70))"
echo "--- slice 4.4 factor study (log logs/v81_factor_study.log)"
t0=$(date +%s); nice -n 10 timeout 3h python tools/v81_factor_study.py > logs/v81_factor_study.log 2>&1; rc=$?
echo "  exit $rc after $(( $(date +%s) - t0 )) s"
if [ $rc = 0 ]; then
  sed -n '1,60p' results/v81_factor_study.txt | cut -c1-200 | sed 's/^/  /'
  git add results/v81_factor_study.txt results/v81_factor_study.json planning/cinemack-v8.1/box_office/correlation_report.md \
          planning/cinemack-v8.1/box_office/feature_provenance.csv results/v81_night2.txt 2>/dev/null
  git commit -q -m "v8.1 slice 4.4: box-office factor study results (drop / add groups vs pf4, Holm-corrected)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NrJonUbBSzhd6Rp6pw6H1k" && echo "  committed $(git log -1 --format=%h)"
else
  echo "  FACTOR STUDY FAILED - nothing committed; last log lines:"; tail -15 logs/v81_factor_study.log | cut -c1-200 | sed 's/^/    /'
fi
echo "=== v8.1 night 2 done $(date)"
