#!/usr/bin/env bash
# v8.1 night 4 (15 Nov 2026), v8.1 worktree only. CPU only; no model call, no GPU, no outside service, nothing live.
#   2.3  TV plan conformance on every show in the facts.db copy              (seconds)
#   6.1  motion audit of web/v8/app.css                                       (instant)
#   4.6  forecast candidate (producer + US rating) and the forecast store     (minutes; writes logs/forecast.db, NEW)
#   5.2  one weight set for the film ranking, MovieLens 32M                   (minutes; reads the live folder's
#        data/movielens/ml-32m.zip read-only)
#   8.1 / 6.2 / 4.6 page modules under Node, if Node is installed             (seconds; reported, changes nothing)
# Each step commits its own result files on branch v8.1. A step whose tool is not installed says UNRUN.
# The log's last line is written BEFORE the last commit (round 43 lesson), so the worktree ends clean.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
[ "$(git branch --show-current)" = "v8.1" ] || { echo "not on branch v8.1; stopping"; exit 1; }
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night4.txt
TRAILER="Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NrJonXzeffgJybczyJitPE"
say() { echo "$@" | tee -a "$R"; }
: > "$R"
say "=== v8.1 night 4  $(date)  ($(git log -1 --format='%h %s' | cut -c1-70))"

step() {   # step NAME TOOL TIMEOUT "result files" "commit subject" [ok exit codes, default 0]
  name=$1; tool=$2; lim=$3; files=$4; subj=$5; okrc=${6:-0}
  say "--- $name (log logs/$name.log)"
  [ -f "$tool" ] || { say "  UNRUN: $tool is not installed"; return; }
  t0=$(date +%s); nice -n 10 timeout "$lim" python "$tool" > "logs/$name.log" 2>&1; rc=$?
  say "  exit $rc after $(( $(date +%s) - t0 )) s"
  case " $okrc " in *" $rc "*) ;; *) say "  FAILED - nothing committed:"; tail -15 "logs/$name.log" | cut -c1-200 | sed 's/^/    /' | tee -a "$R"; return;; esac
  first=$(echo $files | cut -d' ' -f1)
  if [ ! -f "$first" ] || [ "$(stat -c %Y "$first")" -lt "$t0" ]; then
    say "  FAILED - no result file was written:"; tail -15 "logs/$name.log" | cut -c1-200 | sed 's/^/    /' | tee -a "$R"; return
  fi
  sed 's/^/  /' "$first" | cut -c1-230 | tee -a "$R"
  git add $files && git commit -q -m "$subj

$TRAILER" && say "  committed $(git log -1 --format=%h)"
}

# exit 1 of a conformance / audit tool means "rule FAIL": that is a result and is committed too
step v81_tv_conformance tools/v81_tv_conformance.py 30m "results/v81_tv_conformance.txt results/v81_tv_conformance.json" \
     "v8.1 slice 2.3: TV plan conformance on the facts.db copy" "0 1"
step v81_motion_audit tools/v81_motion_audit.py 5m "results/v81_motion_audit.txt results/v81_motion_audit.json" \
     "v8.1 slice 6.1: motion audit of the stylesheet" "0 1"
step v81_forecast_store tools/v81_forecast_store.py 3h "results/v81_forecast_store.txt results/v81_forecast_store.json" \
     "v8.1 slice 4.6: forecast candidate (producer + US rating) verdict and forecast store counts"
step v81_recs_single tools/v81_recs_single.py 5h "results/v81_recs_single.txt results/v81_recs_single.json" \
     "v8.1 slice 5.2: one weight set for the film ranking under both benchmark protocols"

say "--- page modules under Node (tests/js/test_v81_frontend.mjs)"
if [ ! -f tests/js/test_v81_frontend.mjs ]; then say "  UNRUN: not installed"
elif ! command -v node >/dev/null 2>&1; then say "  UNRUN: Node is not installed on this machine (the same tests passed where the round was built)"
else
  say "  node $(node -v)"
  timeout 120 node --test tests/js/test_v81_frontend.mjs > logs/v81_node.log 2>&1; rc=$?
  say "  exit $rc | $(grep -E '^# (tests|pass|fail)' logs/v81_node.log | tr '\n' ' ')"
  [ $rc = 0 ] || grep -B2 -A14 '^not ok' logs/v81_node.log | cut -c1-200 | head -60 | sed 's/^/    /' | tee -a "$R"
fi
say "=== v8.1 night 4 done $(date)"
git add "$R" && git commit -q -m "v8.1 night 4 log

$TRAILER"
