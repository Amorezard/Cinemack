#!/usr/bin/env bash
# 3b tuning run (Tue 10 Nov). RULE, written ~14:00 before any LONG_SUMMARY run:
#   LONG_SUMMARY=1 goes into the proposed v8 lock if, on sets/tune_v8_long.json (6 chats, not blind),
#     ON passes >= 5/6 AND ON >= OFF,
#   and on sets/multiturn.json (30 chats, retrieval scored by stage_scores.py) ON loses at most 1 conversation vs OFF.
#   The blind test is sealed_v8_long in run_final_v8.sh (>= 5/6, validation matrix 8 Nov).
# Both arms = the v8 proposal (v7_lock + v8_lock) with LONG_SUMMARY 0 / 1; long chats run through a copy of the website
# server on port 49160 with the answer cache off. Waits for the round-19 data load and any evaluation to finish first.
#   tmux new -d -s tune3b "bash tools/run_3b_tune.sh"        -> results/tune_3b.txt
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
LOG=results/tune_3b.txt
exec > >(tee -a "$LOG") 2>&1
echo "=== 3b tuning $(date)"
while tmux has-session -t data19 2>/dev/null; do echo "waiting for the data load (tmux data19) $(date +%H:%M)"; sleep 120; done
while pgrep -f "stage_scores.py|eval_v3.py|memory_eval.py|run_v8_tune.sh|run_final_v8" >/dev/null; do echo "waiting for another eval $(date +%H:%M)"; sleep 120; done
L7=$(grep -v '^#' v7_lock.env | tr '\n' ' '); L8=$(grep -v '^#' v8_lock.env | grep -v LONG_SUMMARY | tr '\n' ' ')
BASE="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L7 $L8"
echo "base: $BASE"
arm() {   # arm "<env>" <tag>
  ( cd web && env $1 SITE_CACHE=0 MOVIE_BUFF_CODE=movies PORT=49160 python server.py > ../logs/tune3b_$2.log 2>&1 ) &
  local pid=$!
  for i in $(seq 1 100); do curl -s -o /dev/null -H "X-Access-Code: movies" localhost:49160/api/health && break; sleep 3; done
  env $1 python tools/long_eval.py --set sets/tune_v8_long.json --base http://localhost:49160 --tag tune_long_$2
  pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null; sleep 3
}
echo "--- long chats, LONG_SUMMARY=0 $(date +%H:%M)"; arm "$BASE LONG_SUMMARY=0" off
echo "--- long chats, LONG_SUMMARY=1 $(date +%H:%M)"; arm "$BASE LONG_SUMMARY=1" on
for x in 0 1; do echo "--- multiturn.json retrieval, LONG_SUMMARY=$x $(date +%H:%M)"
  env $BASE LONG_SUMMARY=$x python stage_scores.py --questions sets/multiturn.json --tag tune3b_mt_$x | tail -6; done
python - <<'PY'
import glob, json
off = json.load(open("results/long_tune_long_off.json")); on = json.load(open("results/long_tune_long_on.json"))
print(f"long chats: OFF {off['pass']}/{off['n']}  ON {on['pass']}/{on['n']}")
for a, b in zip(off["items"], on["items"]):
    print(f"  {a['id']}: off {'PASS' if a['pass'] else 'fail'} {a['named']}  |  on {'PASS' if b['pass'] else 'fail'} {b['named']}  {'' if b['pass'] else b['fails']}")
long_ok = on["pass"] >= 5 and on["pass"] >= off["pass"]
mt = {}
for x in "01":
    f = sorted(glob.glob(f"results/stage_scores_*tune3b_mt_{x}*.json"))
    mt[x] = f[-1] if f else None
print("multiturn result files:", mt, "(compare per conversation; the long rule is computed here, the multi-turn part is read by hand)")
print("RULE (long part):", "PASS" if long_ok else "FAIL")
PY
echo "=== 3b tuning done $(date)"
