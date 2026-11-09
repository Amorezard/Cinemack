#!/usr/bin/env bash
# v8 model-backed TUNING runs (Sun 8 Nov 2026). Tuning sets only - never the sealed_v8 sets.
# Every arm = the shipped v7 switches (v7_lock.env) + the one v8 switch being tested, set explicitly.
#   bash run_v8_tune.sh                      # both steps below
#   bash run_v8_tune.sh samename             # 4a: answers + judge on tune_v8_samename, SAME_NAME off vs on
#   bash run_v8_tune.sh invented             # 4b baseline: what v7 returns for 20 made-up films + 20 real vague ones
# Uses the GPU (answer model + judge): run in tmux, one GPU job at a time. Writes runs/<job>/manifest.json and a
# line in docs/daily_progress.md. Model calls are replayed from results/llm_cache.sqlite where they match.
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
STEPS=${*:-samename invented}
JOB="v8tune-$(date +%Y%m%d-%H%M%S)"
RUN="runs/$JOB"; mkdir -p "$RUN" docs results
LOG="$RUN/log.txt"
exec > >(tee -a "$LOG") 2>&1
echo "=== $JOB  steps: $STEPS  $(date)"
[ -f v7_lock.env ] || { echo "no v7_lock.env: stop"; exit 1; }
EVAL_ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1"
V7_LOCK=$(grep -v '^#' v7_lock.env | tr '\n' ' ')
BASE="$EVAL_ENV $V7_LOCK"
echo "base arm: $BASE"
while pgrep -f "stage_scores.py|eval_v3.py|run_final|run_v7_night|performance.py fit" >/dev/null; do
  echo "waiting for another GPU/eval job ... $(date +%H:%M)"; sleep 120; done
STATUS=()
step() { local name=$1; shift; local t0; t0=$(date -Is); echo "=== $name $(date +%H:%M)"; "$@"; local rc=$?
         STATUS+=("$name|$t0|$(date -Is)|$rc"); [ $rc -eq 0 ] || echo "!!! $name exited $rc"; }
# 8 Nov night: SN_EXTRA = extra env for BOTH arms (e.g. "TV_SEASONS=1"), SN_TAG = result tag (default tune8_sn)
samename() {
  local X=${SN_EXTRA:-} T=${SN_TAG:-tune8_sn}
  echo "samename arms: base + [$X] + SAME_NAME=0/1, tag $T"
  env $BASE $X SAME_NAME=0 python eval_v3.py --questions sets/tune_v8_samename.json --tag ${T}_off --answers | tail -14
  env $BASE $X SAME_NAME=1 python eval_v3.py --questions sets/tune_v8_samename.json --tag ${T}_on --answers | tail -14
  python eval_v3.py --compare results/v3eval_${T}_off.json results/v3eval_${T}_on.json | tee results/${T}_compare.txt
}
invented() {
  env $BASE python stage_scores.py --questions sets/tune_v8_invented.json --tag tune8_inv_v7 | tail -60
}
# 9 Nov (4b): IDENT_HONEST off vs on. Off arm for the invented set = tune8_inv_v7 (run 9 Nov morning).
ident() {
  [ -f sets/tune_v8_ident_real_ev.json ] || python make_ident_real.py
  env $BASE IDENT_HONEST=1 python eval_v3.py --questions sets/tune_v8_invented_ev.json --tag tune8_inv_ih --answers --no-judge | tail -4
  env $BASE IDENT_HONEST=0 python eval_v3.py --questions sets/tune_v8_ident_real_ev.json --tag tune8_real_off --answers --no-judge | tail -4
  env $BASE IDENT_HONEST=1 python eval_v3.py --questions sets/tune_v8_ident_real_ev.json --tag tune8_real_on --answers --no-judge | tail -4
  env $BASE IDENT_HONEST=0 python eval_v3.py --questions sets/tv.json --category identify --tag tvid_off --answers --no-judge | tail -4
  env $BASE IDENT_HONEST=1 python eval_v3.py --questions sets/tv.json --category identify --tag tvid_on --answers --no-judge | tail -4
  python ident_score.py | tee results/tune8_ident_score.txt
}
for s in $STEPS; do
  case $s in
    samename) step samename samename ;;
    invented) step invented invented ;;
    ident) step ident ident ;;
    *) echo "unknown step $s"; STATUS+=("$s|-|-|unknown") ;;
  esac
done
python - "$RUN" "$JOB" "$STEPS" "$BASE ${SN_EXTRA:-}" "${STATUS[@]}" <<'PY'
import hashlib, json, os, socket, subprocess, sys, datetime
run, job, steps, base, *st = sys.argv[1:]
def md5(p):
    try: return hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError: return None
git = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
files = ("tv.py", "retrieve.py", "chat.py", "confidence.py", "eval_v3.py", "stage_scores.py", "v7_lock.env",
         "sets/tune_v8_samename.json", "sets/tune_v8_invented.json", "run_v8_tune.sh")
m = {"job_id": job, "host": socket.gethostname(), "steps_requested": steps.split(), "git": git, "base_env": base,
     "code_md5": {f: md5(f) for f in files},
     "steps": [dict(zip(("name", "start", "end", "exit"), s.split("|"))) for s in st],
     "log": os.path.join(run, "log.txt"), "written_at": datetime.datetime.now().isoformat(timespec="seconds")}
json.dump(m, open(os.path.join(run, "manifest.json"), "w"), indent=1)
with open("docs/daily_progress.md", "a") as f:
    f.write(f"- {m['written_at']} {job}: " + ", ".join(f"{s['name']} exit {s['exit']}" for s in m["steps"]) + f" (log {m['log']})\n")
print(f"manifest: {run}/manifest.json")
PY
echo "=== finished $(date)"
