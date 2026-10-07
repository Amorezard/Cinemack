#!/usr/bin/env bash
# The sealed run (planned for Tue 6 / Wed 7 Oct): v2 and v3 on questions neither version was
# tuned on, run once each, both arms declared here before anything runs.
#
#   v2 arm   the v2.0-lock settings: every v3 switch off (CONV=0 TOOLS=0 PERSONAL=0). v3a showed this
#            reproduces v2 exactly on tune (74/115) and check (18/33).
#   v3 arm   the locked v3 switches: V3_ENV below (set from run_v3a / run_v3b results).
#
# Question sets:
#   sets/final.json              sealed since 27 Sep (split_sets.py): TREC train half + own questions.
#                                Retrieval: is the right film in what the answer model gets?
#   sets/sealed_v3_mt.json       new multi-turn conversations written by the user after v3a (if present)
#   sets/sealed_v3_an.json       new analysis/data questions written by the user (if present):
#                                route + data (level 1) and answers + judge (level 2)
#
# On the CPU (the GPU is shared with a 20 GB job) final.json takes hours per arm; the v3 arm
# replays most single-turn model calls from the cache, because first questions get the v2 prompt.
#
#   tmux new -s final
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_final_v3.sh
set -u
cd "$(dirname "$0")"
V2_ENV="CONV=0 TOOLS=0 PERSONAL=0 PARALLEL=0 PLAN_ROUTE=0"
# 7 Oct: PERSONAL=1 with the sweep's weights (run_v3d.sh personal: held-out recall@10 0.420 vs
# popularity 0.314, content 0.258, neighbours 0.414 - rule passed). PLAN_ROUTE stays 0: check
# lost 2 (rule allowed 1) and analytic routing fell 44 -> 38/46, despite planning 36 -> 2 s.
V3_ENV=${V3_ENV:-"CONV=1 TOOLS=1 PERSONAL=1 PERSONAL_W=0.25,2,0.5 PARALLEL=0 PLAN_ROUTE=0"}
# run_v3f.sh's context-window decision (written before the sealed run): adds NUM_CTX=16384 and the
# larger tool context only if its pre-written rule passed.
V3_EXTRA=""
[ -f results/v3f_decision.env ] && . results/v3f_decision.env
V3_ENV="$V3_ENV ${V3_EXTRA:-}"
LOG=results/final_v3.log
SUM=results/final_v3_summary.txt
STEPS=${*:-stamp retrieval multiturn analytic summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_final_v3.sh started $(date)   steps: $STEPS"
echo "    v2 arm: $V2_ENV"
echo "    v3 arm: $V3_ENV"
has() { [[ " $STEPS " == *" $1 "* ]]; }
while pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|summarize.py" >/dev/null; do
  echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done

if has stamp; then
  # Version stamp: exactly what was measured.
  { echo "date: $(date)"; echo "git: $(git describe --always --dirty --tags) $(git rev-parse HEAD)"
    echo "v2 arm: $V2_ENV"; echo "v3 arm: $V3_ENV"
    md5sum retrieve.py chat.py conv.py tools.py config.yaml sets/final.json sets/sealed_v3_*.json 2>/dev/null
    ollama show movie-buff 2>/dev/null | head -8
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null
  } > results/final_v3_stamp.txt
  cat results/final_v3_stamp.txt
fi

if has retrieval; then
  echo "=== sealed retrieval, v2 arm $(date +%H:%M)"
  env $V2_ENV python stage_scores.py --questions sets/final.json --final --tag final_v2 | tail -6
  echo "=== sealed retrieval, v3 arm $(date +%H:%M)"
  env $V3_ENV python stage_scores.py --questions sets/final.json --final --tag final_v3 | tail -6
fi

if has multiturn && [ -f sets/sealed_v3_mt.json ]; then
  env $V2_ENV python stage_scores.py --questions sets/sealed_v3_mt.json --tag sealed_mt_v2 | tail -4
  env $V3_ENV python stage_scores.py --questions sets/sealed_v3_mt.json --tag sealed_mt_v3 | tail -4
fi

if has analytic && [ -f sets/sealed_v3_an.json ]; then
  env $V2_ENV python eval_v3.py --questions sets/sealed_v3_an.json --tag sealed_an_v2 --answers | tail -20
  env $V3_ENV python eval_v3.py --questions sets/sealed_v3_an.json --tag sealed_an_v3 --answers | tail -20
  python eval_v3.py --compare results/v3eval_sealed_an_v2.json results/v3eval_sealed_an_v3.json \
    > results/final_v3_analytic_compare.txt
fi

if has summary; then
{
  echo "SEALED RUN  $(date)"
  cat results/final_v3_stamp.txt 2>/dev/null
  echo
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def line(name, a, b):
    da, db = latest(a), latest(b)
    if not (da and db):
        return f"{name:28s} (not run)"
    pa = sum(r["result"]["pass"] for r in da["rows"]); pb = sum(r["result"]["pass"] for r in db["rows"])
    n = len(da["rows"])
    by = {}
    for r in da["rows"]:
        g = r["group"]; by.setdefault(g, [0, 0, 0])
        by[g][0] += 1; by[g][1] += r["result"]["pass"]
    for r in db["rows"]:
        by.setdefault(r["group"], [0, 0, 0])[2] += r["result"]["pass"]
    out = f"{name:28s} v2 {pa}/{n}   v3 {pb}/{n}   ({pb - pa:+d})"
    for g, (k, x, y) in sorted(by.items()):
        out += f"\n    {g:22s} v2 {x}/{k}  v3 {y}/{k}"
    return out
print(line("sealed retrieval (final)", "final_v2", "final_v3"))
print(line("sealed multi-turn (new)", "sealed_mt_v2", "sealed_mt_v3"))
PY
  echo
  cat results/final_v3_analytic_compare.txt 2>/dev/null | head -40
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_final_v3.sh finished $(date)"
