#!/usr/bin/env bash
# v8.1 night 7 (16 Nov 2026, round 48), v8.1 worktree only. CPU only; no model call, no GPU, nothing live is changed.
#   5.2 third ranking candidate: choice on 2020, one test on 2021 MovieLens users never used   (about 10-20 minutes)
#   publish check of tracked files and history (where, never what)                             (a minute)
# Each step commits its own result files on branch v8.1. The log's last line is written BEFORE the last commit.
set -u
cd "$(dirname "$0")/.." || exit 1
W=$(pwd)
case "$W" in */rag-v81) ;; *) echo "not the v8.1 worktree ($W); stopping"; exit 1;; esac
[ "$(git branch --show-current)" = "v8.1" ] || { echo "not on branch v8.1; stopping"; exit 1; }
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p logs results
R=results/v81_night7.txt
TRAILER="Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NrJonXzeffgJybczyJitPE"
say() { echo "$@" | tee -a "$R"; }
: > "$R"
say "=== v8.1 night 7  $(date)  ($(git log -1 --format='%h %s' | cut -c1-70))"

step() {   # step NAME TOOL TIMEOUT "result files" "commit subject" [ok exit codes, default 0] [tool arguments]
  name=$1; tool=$2; lim=$3; files=$4; subj=$5; okrc=${6:-0}; targs=${7:-}
  say "--- $name (log logs/$name.log)"
  [ -f "$tool" ] || { say "  UNRUN: $tool is not installed"; return; }
  t0=$(date +%s); nice -n 10 timeout "$lim" python "$tool" $targs > "logs/$name.log" 2>&1; rc=$?
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

step v81_recs_v3 tools/v81_recs_v3.py 4h "results/v81_recs_v3.txt results/v81_recs_v3.json" \
     "v8.1 slice 5.2: third ranking candidate (max-min choice on 2020, one test on 2021)"
step v81_publish_check tools/v81_publish_check.py 30m "results/v81_publish_check.txt results/v81_publish_check.json" \
     "v8.1 round 48: publish check of the tracked files and the history (locations only, never values)" "0 1" "--history --out results/v81_publish_check"
say "=== v8.1 night 7 done $(date)"
git add "$R" && git commit -q -m "v8.1 night 7 log

$TRAILER"
