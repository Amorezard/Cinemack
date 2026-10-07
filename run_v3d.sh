#!/usr/bin/env bash
# v3, Tue 6 Oct evening: a smaller planner, and the personalisation weights.
#
# Why: the GPU is held by another user's job (20 of 23 GB), so movie-buff (27B, 18 GB) runs
# 98% on the CPU and every planner call takes tens of seconds. llama3.2 (3B, ~2 GB) is fast
# even on the CPU. E8 (v2): llama3.2 as the ONLY planner lost 4 tune / 4 check questions, all
# plot descriptions ("identify") - it can't guess obscure films' plots. PLAN_ROUTE=1 (built in
# v2, never measured) is the two-step version: llama3.2 reads every first question; only when
# it says "identify" does movie-buff write the plan with the guessed plot. Later turns of a
# conversation (CONV) always use movie-buff.
#
# Steps (bash run_v3d.sh <step> ...):
#   route     tune + check + multiturn + analytic level 1, locked v3 vs PLAN_ROUTE=1 (cached arm replays)
#   timing    --no-cache analytic level 1 on non-description questions: locked, route, locked
#   personal  personal.py sweep (weights chosen on half the MovieLens users, reported on the rest)
#   summary   results/v3d_summary.txt
#
# Decision rules, written 6 Oct before any result:
#   PLAN_ROUTE=1  tune and check each lose at most 1 pass, multiturn not lower, analytic level 1
#                 route / data / spec not lower, AND mean prepare time (plan + search + tools) on
#                 the timing questions at least 30% lower than the locked arm (mean of its 2 runs).
#   PERSONAL=1    as written in personal.py sweep(): on the held-out users the chosen weights beat
#                 popularity and content-only and are within 0.02 of neighbours-only.
#
#   tmux new -s v3d
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_v3d.sh
set -u
cd "$(dirname "$0")"
LOG=results/v3d.log
SUM=results/v3d_summary.txt
STEPS=${*:-personal route timing summary}
LOCK="CONV=1 TOOLS=1"
ROUTE="CONV=1 TOOLS=1 PLAN_ROUTE=1 ROUTE_MODEL=llama3.2"
TIME_CATS=${TIME_CATS:-factual,compare,career,recent,awards}
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3d.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
run() {  # $1 = tag, $2 = set, rest = VAR=value settings
  local tag=$1 set=$2; shift 2
  echo "=== $tag $set $* $(date +%H:%M)"
  env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4
}
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
while pgrep -f "stage_scores.py|eval_v3.py" >/dev/null; do echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done

if has personal; then
  echo "=== personal sweep $(date +%H:%M)"
  python personal.py sweep 300
fi

if has route; then
  for s in tune check multiturn; do
    run v3d_${s}_lock  $s $LOCK
    run v3d_${s}_route $s $ROUTE
    python compare_stage.py "$(latest v3d_${s}_lock)" "$(latest v3d_${s}_route)" | tail -15
  done
  env $LOCK  python eval_v3.py --tag l1_v3d_lock  | tail -18
  env $ROUTE python eval_v3.py --tag l1_v3d_route | tail -18
fi

if has timing; then
  # Same questions, no call cache, alternating arms so a change in the other user's load
  # hits both. Prepare time = plan + search + tools (no answer).
  env $LOCK  python eval_v3.py --no-cache --category $TIME_CATS --tag time_lock1  | tail -10
  env $ROUTE python eval_v3.py --no-cache --category $TIME_CATS --tag time_route  | tail -10
  env $LOCK  python eval_v3.py --no-cache --category $TIME_CATS --tag time_lock2  | tail -10
fi

if has summary; then
{
  echo "v3d summary  $(date)"
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null
  echo
  echo "##### PERSONAL"; cat results/personal_sweep.txt 2>/dev/null
  echo
  echo "##### PLAN_ROUTE: passes (locked -> route)"
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def passes(d):
    rows = d.get("rows", d) if isinstance(d, dict) else d
    return sum(1 for r in rows if (r.get("result") or {}).get("pass")), len(rows)
for s in ("tune", "check", "multiturn"):
    a, b = latest(f"v3d_{s}_lock"), latest(f"v3d_{s}_route")
    if a and b:
        (pa, n), (pb, _) = passes(a), passes(b)
        print(f"  {s:10s} {pa}/{n} -> {pb}/{n}  ({pb - pa:+d})")
    else:
        print(f"  {s:10s} (not run)")
def l1(tag):
    try:
        rows = json.load(open(f"results/v3eval_{tag}.json"))["rows"]
    except FileNotFoundError:
        return None
    out = {}
    for k in ("route", "data", "spec"):
        g = [r["grades"][k] for r in rows if k in r["grades"]]
        out[k] = f"{sum(g)}/{len(g)}"
    return out, rows
a, b = l1("l1_v3d_lock"), l1("l1_v3d_route")
if a and b:
    print("  analytic L1 locked:", a[0], " route:", b[0])
print()
print("##### TIMING (no cache): mean prepare s per question")
for t in ("time_lock1", "time_route", "time_lock2"):
    try:
        rows = json.load(open(f"results/v3eval_{t}.json"))["rows"]
        xs = [r["timing"]["prepare_s"] for r in rows]
        pl = [r["timing"].get("plan_ms") or 0 for r in rows]
        print(f"  {t:12s} n={len(xs)}  prepare {sum(xs) / len(xs):6.1f} s   plan {sum(pl) / len(pl) / 1000:6.1f} s")
    except FileNotFoundError:
        print(f"  {t:12s} (not run)")
PY
} > "$SUM"
cat "$SUM"
fi
echo "=== run_v3d.sh done $(date)"
