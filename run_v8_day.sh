#!/usr/bin/env bash
# v8 day runner (8 Nov 2026). Each run writes runs/<job_id>/manifest.json (command, git commit, code md5s,
# start/end, exit code per step, log path) and appends a line to docs/daily_progress.md.
#   bash run_v8_day.sh morning                 # health check: jobs, tmux, GPU, disk, last results (read-only)
#   bash run_v8_day.sh people                  # pf5 A: people in the stored headlines (CPU, minutes)
#   bash run_v8_day.sh press                   # pf5 B: Guardian pre-release articles (network; ~40 min per 4,800 calls)
#   bash run_v8_day.sh macro                   # pf5 C: FRED series (needs FRED_API_KEY)
#   bash run_v8_day.sh people press macro      # several, in this order
# Network / CPU only: safe next to the live site. Refuses to start people while news.py is still writing headlines.
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
STEPS=${*:-morning}
JOB="v8day-$(date +%Y%m%d-%H%M%S)"
RUN="runs/$JOB"; mkdir -p "$RUN" docs
LOG="$RUN/log.txt"
exec > >(tee -a "$LOG") 2>&1
echo "=== $JOB  steps: $STEPS  $(date)"
STATUS=()
step() {   # step <name> <command...>
  local name=$1; shift
  local t0; t0=$(date -Is)
  echo "=== $name $(date +%H:%M)"
  "$@"; local rc=$?
  STATUS+=("$name|$t0|$(date -Is)|$rc")
  [ $rc -eq 0 ] || echo "!!! $name exited $rc (see $LOG)"
}
morning() {
  echo "--- jobs running"; pgrep -af "news.py|performance.py|pf5_data.py|stage_scores|eval_v3|run_final|run_v7|run_v8|run_pf4" || echo "(none)"
  echo "--- tmux"; tmux ls 2>/dev/null || echo "(no tmux sessions)"
  echo "--- GPU"; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || echo "(no nvidia-smi)"
  echo "--- disk"; du -sh facts.db data 2>/dev/null; df -h . | tail -1
  echo "--- git"; git describe --tags --always 2>/dev/null; git status --short | head -15
  echo "--- site"; curl -s -m 5 localhost:49151/api/health || echo "(site not answering on 49151)"; echo
  echo "--- Wikipedia headlines by decade (2000+ should be non-zero after Friday's re-fetch)"
  python - <<'PY'
import sqlite3
c = sqlite3.connect("facts.db")
for r in c.execute("select substr(month,1,3)||'0s', count(*) from news_items where source='wikipedia' and month >= '1990' group by 1 order by 1"):
    print("  ", *r)
try:
    print("   news_film_src wikipedia with a score:", c.execute("select count(resonance) from news_film_src where source='wikipedia'").fetchone()[0])
except sqlite3.Error as e:
    print("  ", e)
PY
  echo "--- pf4 news / Letterboxd lines"; grep -E "pf4|films with each news" results/performance_summary.txt 2>/dev/null | head -6
  echo "--- judge hand check"; ls -l results/judge_check_report.txt 2>/dev/null && tail -5 results/judge_check_report.txt || echo "  not graded yet (results/judge_check.html)"
  echo "--- sealed v8 sets"; ls sets/sealed_v8_*.json 2>/dev/null || echo "  none yet"
  python pf5_data.py status 2>/dev/null || true
}
for s in $STEPS; do
  case $s in
    morning) step morning morning ;;
    people)
      if pgrep -f "news.py fetch|news.py embed" >/dev/null; then echo "news.py is still fetching headlines: run people after it"; STATUS+=("people|-|-|skipped"); continue; fi
      step people python pf5_data.py people ;;
    press) step press python pf5_data.py press ;;
    macro) step macro python pf5_data.py macro ;;
    status) step status python pf5_data.py status ;;
    *) echo "unknown step $s"; STATUS+=("$s|-|-|unknown") ;;
  esac
done
python - "$RUN" "$JOB" "$STEPS" "${STATUS[@]}" <<'PY'
import hashlib, json, os, socket, subprocess, sys, datetime
run, job, steps, *st = sys.argv[1:]
def md5(p):
    try: return hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError: return None
git = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
m = {"job_id": job, "host": socket.gethostname(), "steps_requested": steps.split(), "git": git,
     "code_md5": {f: md5(f) for f in ("pf5_data.py", "performance.py", "news.py", "run_v8_day.sh")},
     "steps": [dict(zip(("name", "start", "end", "exit"), s.split("|"))) for s in st],
     "log": os.path.join(run, "log.txt"), "written_at": datetime.datetime.now().isoformat(timespec="seconds")}
json.dump(m, open(os.path.join(run, "manifest.json"), "w"), indent=1)
with open("docs/daily_progress.md", "a") as f:
    f.write(f"- {m['written_at']} {job}: " + ", ".join(f"{s['name']} exit {s['exit']}" for s in m["steps"]) + f" (log {m['log']})\n")
print(f"manifest: {run}/manifest.json")
PY
echo "=== finished $(date)"
