#!/usr/bin/env bash
# v8.1 night 1 (12 Nov 2026), run inside the v8.1 worktree only. CPU only: no model call, no GPU, no outside service.
#   1. a private copy of facts.db for this worktree (the live file is opened read-only)
#   2. slice 4.1: pf5_model.py and performance.py fit on the COPY (reference: pf4 before + trailers test MAE 1.200)
#   3. slice 4.2: box-office data inventory (counts only)
#   4. nightly.sh --dry from this worktree (lists the steps; runs none)
# Rule for 4.1 (planning/cinemack-v8.1/IMPLEMENTATION_PLAN_LOCKED.md, written before this ran): PASS if the pf4 arm is
# 1.200 with 8,742 training and 1,487 test films; anything else is reported as a deviation.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night1.txt
exec > >(tee "$R") 2>&1
echo "=== v8.1 night 1  $(date)  in $W  ($(git log -1 --format='%h %s' | cut -c1-70))"
echo "--- 1. facts.db copy"
if [ -f facts.db ]; then echo "  facts.db already here ($(du -h facts.db | cut -f1))"; else
  python tools/v81_copy_db.py ~/movie-buff/rag/facts.db facts.db films money credits || { echo "copy failed; stopping"; exit 1; }
fi
[ "$(python -c 'import facts_db, os; print(os.path.realpath(facts_db.PATH))')" = "$W/facts.db" ] || { echo "facts_db does not point at the copy; stopping"; exit 1; }
echo "--- 2a. pf5_model.py on the copy (log: logs/v81_pf5.log)"
touch logs/v81_start_pf5; t0=$(date +%s); timeout 3h python pf5_model.py > logs/v81_pf5.log 2>&1; rc=$?; echo "  exit $rc after $(( $(date +%s) - t0 )) s"
PF5_NEW=0; [ $rc = 0 ] && [ results/pf5_summary.txt -nt logs/v81_start_pf5 ] && PF5_NEW=1
echo "  committed summary, pf4 lines:"; git show HEAD:results/pf5_summary.txt 2>/dev/null | grep -iE "pf4|train|test films|coverage|80%" | head -14 | cut -c1-200 | sed 's/^/    was: /'
if [ $PF5_NEW = 0 ]; then echo "  4.1 pf5_model: RUN FAILED or wrote no new summary - NOT reproduced (UNRUN); last log lines:"; tail -8 logs/v81_pf5.log | cut -c1-200 | sed 's/^/    /'; fi
[ $PF5_NEW = 1 ] && echo "  tonight's summary, pf4 lines:" && grep -iE "pf4|train|test films|coverage|80%" results/pf5_summary.txt 2>/dev/null | head -14 | cut -c1-200 | sed 's/^/    now: /'
echo "--- 2b. performance.py fit on the copy (log: logs/v81_pf4.log)"
touch logs/v81_start_pf4; t0=$(date +%s); timeout 3h python performance.py fit > logs/v81_pf4.log 2>&1; rc=$?; echo "  exit $rc after $(( $(date +%s) - t0 )) s"
VERS="git show HEAD:results/performance_summary.txt"
if [ $rc = 0 ] && [ results/performance_summary.txt -nt logs/v81_start_pf4 ]; then VERS="$VERS|cat results/performance_summary.txt"; else
  echo "  4.1 performance.py fit: RUN FAILED or wrote no new summary - NOT reproduced (UNRUN); last log lines:"; tail -8 logs/v81_pf4.log | cut -c1-200 | sed 's/^/    /'; fi
echo "$VERS" | tr '|' '\n' | while read -r v; do
  echo "  [$v]"; $v 2>/dev/null | grep -E "^target|genre median|linear, before|trees, before|trees, \+ reception  |rule \(|80%" | cut -c1-170 | sed 's/^/    /'
done
echo "  files changed by the two runs: $(git status --short --untracked-files=no | tr '\n' ' ')"
echo "--- 3. box-office data inventory (results/v81_boxoffice_inventory.txt)"
timeout 1h python tools/v81_boxoffice_inventory.py > logs/v81_inventory.log 2>&1; echo "  exit $?; $(wc -l < results/v81_boxoffice_inventory.txt 2>/dev/null) lines"
echo "--- 4. nightly.sh --dry (steps listed, none run)"
D=results/nightly_$(date +%F).txt; had=0; [ -f "$D" ] && had=1
timeout 300 bash nightly.sh --dry > logs/v81_nightly_dry.txt 2>&1; echo "  exit $?; steps listed: $(grep -c '(dry)' logs/v81_nightly_dry.txt)"
[ $had = 0 ] && rm -f "$D"
cp logs/v81_nightly_dry.txt results/v81_nightly_dry.txt 2>/dev/null
echo "=== v8.1 night 1 done $(date)"
