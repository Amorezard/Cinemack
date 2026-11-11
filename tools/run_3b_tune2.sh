#!/usr/bin/env bash
# 3b v2 tuning (Tue 10 Nov ~16:45). v1 failed its rule (tune_v8_long OFF 2/6, ON 2/6). Seen in the output: the final
# "one film for tonight" retrieved titles with "Night" in them and every candidate broke the turn-1 limit, so telling
# the model the limit could not help. v2: with LONG_SUMMARY=1 the limits stated in earlier messages also become this
# request's chat filters (strict: an unknown fact fails). Same tuning set (NOT blind; re-tuning on it after seeing v1).
# RULE (unchanged, written before this run): ON >= 5/6 and ON >= OFF (OFF = the 13:48 run, 2/6; the OFF path is the
# same code). Multi-turn: stage_scores on multiturn.json does not go through server.py, so v2 cannot change it; the
# v1 files are compared conversation by conversation below.
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
LOG=results/tune_3b.txt
exec > >(tee -a "$LOG") 2>&1
echo "=== 3b v2 tuning $(date)"
while pgrep -f "stage_scores.py|eval_v3.py|memory_eval.py|run_v8_tune.sh|run_final_v8|long_eval.py" >/dev/null; do echo "waiting $(date +%H:%M)"; sleep 60; done
L7=$(grep -v '^#' v7_lock.env | tr '\n' ' '); L8=$(grep -v '^#' v8_lock.env | grep -v LONG_SUMMARY | tr '\n' ' ')
BASE="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L7 $L8"
( cd web && env $BASE LONG_SUMMARY=1 SITE_CACHE=0 MOVIE_BUFF_CODE=movies PORT=49160 python server.py > ../logs/tune3b_on2.log 2>&1 ) &
pid=$!
for i in $(seq 1 100); do curl -s -o /dev/null -H "X-Access-Code: movies" localhost:49160/api/health && break; sleep 3; done
env $BASE LONG_SUMMARY=1 python tools/long_eval.py --set sets/tune_v8_long.json --base http://localhost:49160 --tag tune_long_on2
pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null; sleep 2
python - <<'PY'
import json
off = json.load(open("results/long_tune_long_off.json")); on = json.load(open("results/long_tune_long_on2.json"))
print(f"v2 long chats: OFF {off['pass']}/{off['n']}  ON(v2) {on['pass']}/{on['n']}")
for a, b in zip(off["items"], on["items"]):
    print(f"  {a['id']}: off {'PASS' if a['pass'] else 'fail'}  |  on2 {'PASS' if b['pass'] else 'fail'} {b['named']} {'' if b['pass'] else b['fails']}")
print("RULE (long part, v2):", "PASS" if on["pass"] >= 5 and on["pass"] >= off["pass"] else "FAIL")
def rows(p):
    d = json.load(open(p))
    r = d.get("rows") or d.get("results") or d.get("questions") or (d if isinstance(d, list) else [])
    return {x.get("id"): x for x in r if isinstance(x, dict)}
a = rows("results/stage_scores_2026-11-10_multiturn_tune3b_mt_0.json"); b = rows("results/stage_scores_2026-11-10_multiturn_tune3b_mt_1.json")
skip = {"ms", "timings", "time", "seconds", "plan_ms", "search_ms", "rerank_ms", "total_ms"}
diff = [i for i in a if i in b and {k: v for k, v in a[i].items() if k not in skip and "ms" not in k} != {k: v for k, v in b[i].items() if k not in skip and "ms" not in k}]
print(f"multiturn.json: {len(a)} conversations; differ between OFF and ON (v1 files): {len(diff)} {diff[:10]}")
if a:
    print("  row keys:", sorted(next(iter(a.values())).keys())[:20])
PY
echo "=== 3b v2 tuning done $(date)"
