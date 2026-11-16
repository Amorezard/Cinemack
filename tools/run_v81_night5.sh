#!/usr/bin/env bash
# v8.1 night 5 (16 Nov 2026), v8.1 worktree only. CPU only; no model call, no GPU, nothing live is changed.
#   review coverage inventory on the facts.db copy                 (seconds)
#   YouTube match rules on every review / essay video in the copy  (a minute or two; writes the sample to grade)
#   6.1 motion audit again after the banner-dot edit               (instant)
#   5.2 single weight set on fresh MovieLens users                 (minutes; reads ml-32m.zip in the live folder, read-only)
# Each step commits its own result files on branch v8.1. The log's last line is written BEFORE the last commit.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
[ "$(git branch --show-current)" = "v8.1" ] || { echo "not on branch v8.1; stopping"; exit 1; }
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night5.txt
TRAILER="Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NrJonXzeffgJybczyJitPE"
say() { echo "$@" | tee -a "$R"; }
: > "$R"
say "=== v8.1 night 5  $(date)  ($(git log -1 --format='%h %s' | cut -c1-70))"

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
  have=""; for f in $files; do [ -f "$f" ] && have="$have $f"; done
  git add $have && git commit -q -m "$subj

$TRAILER" && say "  committed $(git log -1 --format=%h)"
}

step v81_review_coverage tools/v81_review_coverage.py 20m "results/v81_review_coverage.txt results/v81_review_coverage.json" \
     "v8.1 round 46: review material per film and where more could be had (inventory)"
step v81_yt_match tools/v81_yt_match.py 2h "results/v81_yt_match.txt results/v81_yt_match.json results/v81_yt_match_check2.csv" \
     "v8.1 round 46: YouTube match rules on the copy; new sample to grade"
step v81_motion_audit tools/v81_motion_audit.py 5m "results/v81_motion_audit.txt results/v81_motion_audit.json" \
     "v8.1 slice 6.1: motion audit after the banner-dot edit" "0 1"
step v81_recs_fresh tools/v81_recs_fresh.py 5h "results/v81_recs_fresh.txt results/v81_recs_fresh.json" \
     "v8.1 slice 5.2: the single weight set on fresh MovieLens users"
say "=== v8.1 night 5 done $(date)"
git add "$R" && git commit -q -m "v8.1 night 5 log

$TRAILER"
