#!/usr/bin/env bash
# Round 27 check (11 Nov): the 3b tuning set again with the round-27 fixes on - ANSWER_PLOT_OK (moderation keeps a film
# answer flagged only as crime), STRIP_WHEN (no "tonight" in the recommendation search), GENRE_AGREE - on top of the
# v8 lock (LONG_SUMMARY=1). Not a new rule and not blind (the fixes were written after seeing tl01): it only checks
# that tl01 now answers and nothing that passed before fails. Compare with results/long_tune_long_on2.json (5/6).
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
LOG=results/tune_3b.txt
exec > >(tee -a "$LOG") 2>&1
echo "=== round 27 check (tune_long_on3) $(date)"
while pgrep -f "stage_scores.py|eval_v3.py|memory_eval.py|run_v8_tune.sh|run_final_v8|long_eval.py" >/dev/null; do echo "waiting $(date +%H:%M)"; sleep 60; done
L7=$(grep -v '^#' v7_lock.env | tr '\n' ' '); L8=$(grep -v '^#' v8_lock.env | tr '\n' ' ')
BASE="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L7 $L8 STRIP_WHEN=1 ANSWER_PLOT_OK=1 GENRE_AGREE=1"
( cd web && env $BASE SITE_CACHE=0 MOVIE_BUFF_CODE=movies PORT=49160 python server.py > ../logs/tune3b_on3.log 2>&1 ) &
pid=$!
for i in $(seq 1 100); do curl -s -o /dev/null -H "X-Access-Code: movies" localhost:49160/api/health && break; sleep 3; done
env $BASE python tools/long_eval.py --set sets/tune_v8_long.json --base http://localhost:49160 --tag tune_long_on3
pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null; sleep 2
python - <<'PY'
import json
a = json.load(open("results/long_tune_long_on2.json")); b = json.load(open("results/long_tune_long_on3.json"))
print(f"3b long chats: v2 {a['pass']}/{a['n']}  v2 + round-27 fixes {b['pass']}/{b['n']}")
REF = "I can't help"
for x, y in zip(a["items"], b["items"]):
    ref = "  REFUSAL" if (y.get("answer") or "").startswith(REF) else ""
    print(f"  {x['id']}: v2 {'PASS' if x['pass'] else 'fail'} | r27 {'PASS' if y['pass'] else 'fail'} {y['named']} {'' if y['pass'] else y['fails']}{ref}")
lost = [y["id"] for x, y in zip(a["items"], b["items"]) if x["pass"] and not y["pass"]]
print("CHECK nothing that passed in v2 fails now:", "yes" if not lost else f"NO {lost}")
PY
echo "=== round 27 check done $(date)"
