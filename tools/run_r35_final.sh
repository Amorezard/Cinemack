#!/usr/bin/env bash
# Round 35 (11 Nov, late): tonight's GPU queue, one job at a time, in this order:
#   1. wait until r27night has finished 4c (claim rewrite) and its rules - i.e. it reaches the embed step or ends -
#      and the 3b check (tmux tune3b5) is done; r27night's embed step is stopped and moved to step 4
#   2. the v8 sealed run (run_final_v8.sh, HEAD tagged v8.0-lock by round 35) - the plan's Wednesday-night run
#   3. if 4c passed its rule tonight: CLAIM_REWRITE applied to the sealed analysis answers (report only, not in v8)
#   4. the embedder fine-tune (r27night's last step), then its rules
#   5. the round-33 variant re-tests (persona-lite, context v3 without TV), then the round-34 judge v2 re-score
# The live site stays up (no one uses it at night; timings are not a sealed score). Log: runs/r35final-<time>.txt
set -u
cd "$(dirname "$0")/.." || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
mkdir -p runs results; LOG=runs/r35final-$(date +%Y%m%d-%H%M%S).txt
exec > >(tee -a "$LOG") 2>&1
echo "=== r35final $(date)"

# 1. wait for 4c + rules, then stop r27night before (or at the very start of) its embed step
while tmux has-session -t r27night 2>/dev/null; do
  L=$(ls -td runs/r27night-* 2>/dev/null | head -1)
  if [ -n "$L" ] && grep -q "^=== step embed" "$L/log.txt"; then
    echo "r27night reached the embed step: stopping it (embed runs after the sealed run) $(date +%H:%M)"
    tmux kill-session -t r27night 2>/dev/null; sleep 5; pkill -f "embed_ft.py" 2>/dev/null; sleep 5; break
  fi
  echo "waiting for r27night (4c) $(date +%H:%M)"; sleep 120
done
while tmux has-session -t tune3b5 2>/dev/null || pgrep -f "long_eval.py|eval_v3.py|attack_eval.py|memory_eval.py|embed_ft.py" >/dev/null; do
  echo "waiting for the 3b check / other evals $(date +%H:%M)"; sleep 120; done
python tools/r27_rules.py > results/r27_night.txt 2>&1
grep -A6 "^## 4c" results/r27_night.txt

# 2. the sealed run
P=49160; for p in 49160 49161 49163 49167 49168 49169; do curl -s -m 3 -o /dev/null localhost:$p || { P=$p; break; }; done
echo "=== sealed run (long-chat server copy on port $P) $(date)"
git describe --tags --exact-match HEAD 2>/dev/null | grep -q "^v8.0-lock$" || echo "WARNING: HEAD is not v8.0-lock"
LONG_PORT=$P bash run_final_v8.sh
echo "=== sealed run finished $(date)"

# 3. claim rewrite on the sealed analysis answers, only if 4c passed its rule tonight (report only)
if grep -A6 "^## 4c" results/r27_night.txt | grep -q "RULE: PASS" && [ -f results/v3eval_sealed8_an_v8.json ]; then
  echo "=== 4c passed: claim rewrite on the sealed analysis answers (report only) $(date +%H:%M)"
  V8="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $(grep -v '^#' v7_lock.env | tr '\n' ' ') $(grep -v '^#' v8_lock.env | tr '\n' ' ')"
  env $V8 CLAIM_REWRITE=1 python claim_rewrite.py eval results/v3eval_sealed8_an_v8.json --tag sealed8_an_claim | tail -6
  python eval_v3.py --rejudge results/v3eval_sealed8_an_claim.json | tail -12
  python claimcheck.py eval results/v3eval_sealed8_an_v8.json | tail -1
  python claimcheck.py eval results/v3eval_sealed8_an_claim.json | tail -1
else
  echo "=== 4c did not pass (or no sealed analysis answers): no claim-rewrite arm"
fi

# 4. embedder fine-tune, 5. variant re-tests, judge v2 re-score (LATER=0: r33night / r34judge already running on
# their own because r27night had ended before round 35 was installed - they are not started twice)
echo "=== embedder fine-tune $(date)"; bash tools/run_r27_night.sh embed rules
if [ "${LATER:-1}" = 1 ]; then
  bash tools/run_r33_night.sh
  bash tools/run_r34_judge.sh
fi
python tools/r27_rules.py > results/r27_night.txt 2>&1
echo "=== r35final done $(date)"
