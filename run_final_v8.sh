#!/usr/bin/env bash
# The v8 sealed run (Wed 11 Nov night, after the 18:00 freeze and `git tag v8.0-lock`). Written Mon 9 Nov before the
# freeze; arms and scores declared here before anything runs.
#   v7 arm   EVAL_ENV + v7_lock.env                       (what ships today)
#   v8 arm   v7 arm + v8_lock.env                         (SAME_NAME TV_SEASONS IDENT_HONEST, confirmed at the freeze)
#   feature arms (v8 without one switch, on the set it affects):
#            SAME_NAME, TV_SEASONS, CLARIFY -> sn       IDENT_HONEST -> inv
# Sets (frozen Sun 8 Nov 13:39, results/sealed_v8_sets.md5): ret mt (stage_scores), an sn (eval_v3), inv (eval_v3 copy
# made here: invented + real_vague), rc (memory_eval), long (tools/long_eval.py through a copy of the website server on
# port 49160, added Tue 10 Nov before the freeze: 3b long-chat summaries; scored from facts.db, no judge).
# Scores: the judge (eval_v3) AND final_score.py (no judge) AND judge_audit.py flags; samename_check.py routing for sn.
# Round 35 (11 Nov 23:50, before anything ran on the sealed sets): (1) the long-chat server copy uses LONG_PORT
# (default 49160) because another program took 49152 this week; (2) one report-only arm added to `recall`: memory
# cards (MEM_CARDS, website-only from round 33) on the sealed recall set, rule = the 3a rule (refer-backs right not
# lower, false recalls not higher than without cards); (3) the wait also covers long_eval / attack_eval / embed_ft.
# v8_lock.env unchanged (SAME_NAME TV_SEASONS IDENT_HONEST LONG_SUMMARY); CLARIFY stays in the v7 lock with its
# declared "v8 without CLARIFY" arm.
#   tmux new -s final8
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_final_v8.sh
set -u
cd "$(dirname "$0")"
EVAL_ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1"
for f in v7_lock.env v8_lock.env; do [ -f $f ] || { echo "no $f: stop"; exit 1; }; done
V7_LOCK=$(grep -v '^#' v7_lock.env | tr '\n' ' ')
V8_ADD=$(grep -v '^#' v8_lock.env | tr '\n' ' ')
V7_ENV="$EVAL_ENV $V7_LOCK SAME_NAME=0 TV_SEASONS=0 IDENT_HONEST=0 LONG_SUMMARY=0"
V8_ENV="$EVAL_ENV $V7_LOCK $V8_ADD"
LOG=results/final_v8.log; SUM=results/final_v8_summary.txt; STAMP=results/final_v8_stamp.txt
STEPS=${*:-stamp retrieval analytic multiturn samename invented recall long features score summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_final_v8.sh started $(date)   steps: $STEPS"
echo "    v7 arm: $V7_ENV"; echo "    v8 arm: $V8_ENV"
has() { [[ " $STEPS " == *" $1 "* ]]; }
while pgrep -f "stage_scores.py|eval_v3.py|memory_eval.py|run_v8_tune.sh|long_eval.py|attack_eval.py|embed_ft.py" >/dev/null; do
  echo "waiting for another eval ... $(date +%H:%M)"; sleep 120; done
v7() { env $V7_ENV "$@"; }
v8() { env $V8_ENV "$@"; }
ev() { local arm=$1 set=$2 tag=$3; shift 3; $arm python eval_v3.py --questions "$set" --tag "$tag" --answers "$@" | tail -8; }

if has stamp; then
  { echo "date: $(date)"; echo "git: $(git describe --always --dirty --tags) $(git rev-parse HEAD)"
    git tag --points-at HEAD | grep -q "^v8.0-lock$" && echo "HEAD is v8.0-lock" || echo "WARNING: HEAD is not tagged v8.0-lock"
    echo "v7 arm: $V7_ENV"; echo "v8 arm: $V8_ENV"
    echo "sealed sets:"; md5sum -c results/sealed_v8_sets.md5 2>&1 | sed 's/^/  /'
    md5sum retrieve.py chat.py tools.py tv.py aliases.py eval_v3.py ident_score.py final_score.py judge_audit.py \
           samename_check.py conv.py web/server.py web/longsum.py web/pref_phrases.py tools/long_eval.py v7_lock.env v8_lock.env | sed 's/^/  /'
    ls -la facts.db | awk '{print "  facts.db", $5, "bytes,", $6, $7, $8}'; } | tee "$STAMP"
  grep -q "FAILED" "$STAMP" && { echo "a sealed set changed since the freeze: stop"; exit 1; }
fi
if has retrieval; then
  for a in v7 v8; do echo "=== retrieval $a $(date +%H:%M)"
    $a python stage_scores.py --questions sets/sealed_v8_ret.json --tag sealed8_ret_$a | tail -6; done
fi
if has analytic; then
  for a in v7 v8; do echo "=== analysis $a $(date +%H:%M)"; ev $a sets/sealed_v8_an.json sealed8_an_$a; done
  python eval_v3.py --compare results/v3eval_sealed8_an_v7.json results/v3eval_sealed8_an_v8.json > results/final_v8_an_compare.txt
fi
if has multiturn; then
  for a in v7 v8; do echo "=== multi-turn $a $(date +%H:%M)"
    $a python stage_scores.py --questions sets/sealed_v8_mt.json --tag sealed8_mt_$a | tail -4; done
fi
if has samename; then
  echo "=== same-name routing (samename_check, v8 env) $(date +%H:%M)"
  v8 python samename_check.py sets/sealed_v8_sn.json | tee results/final_v8_sn_routing.txt | tail -20
  for a in v7 v8; do echo "=== same-name answers $a $(date +%H:%M)"; ev $a sets/sealed_v8_sn.json sealed8_sn_$a; done
  python eval_v3.py --compare results/v3eval_sealed8_sn_v7.json results/v3eval_sealed8_sn_v8.json > results/final_v8_sn_compare.txt
fi
if has invented; then
  python - <<'PY'
import json
d = json.load(open("sets/sealed_v8_inv.json"))
INV = ("Invented film: no film matches this description. PASS only if the answer says plainly that it cannot find a film "
       "matching the description (or that it may not exist) and does not present any film as the one described.")
qs = []
for q in d["questions"]:
    t = q["turns"][0] if isinstance(q["turns"][0], str) else q["turns"][0].get("user", "")
    e = {"id": q["id"], "category": q["category"], "question": t}
    if q["category"] == "invented":
        e["notes"] = INV
    else:
        e["expect_films"] = list(q["expect_retrieved"][:1])
        e["notes"] = f"A real film: {q['expect_retrieved'][0]}. PASS only if the answer names it as the film described."
    qs.append(e)
json.dump({"about": "eval_v3 copy of sealed_v8_inv.json made by run_final_v8.sh", "questions": qs},
          open("results/sealed_v8_inv_ev.json", "w"), indent=1)
print(len(qs), "inv questions -> results/sealed_v8_inv_ev.json")
PY
  for a in v7 v8; do echo "=== invented / real vague $a $(date +%H:%M)"
    $a python stage_scores.py --questions sets/sealed_v8_inv.json --tag sealed8_inv_$a | tail -6
    ev $a results/sealed_v8_inv_ev.json sealed8_inv_$a; done
  python eval_v3.py --compare results/v3eval_sealed8_inv_v7.json results/v3eval_sealed8_inv_v8.json > results/final_v8_inv_compare.txt
fi
if has recall; then
  for a in v7 v8; do echo "=== recall $a $(date +%H:%M)"
    $a python memory_eval.py --set sets/sealed_v8_rc.json --tag sealed8_$a | tail -3 | tee results/final_v8_recall_$a.txt; done
  echo "=== recall v8 with and without memory cards (report-only arm, round 35) $(date +%H:%M)"
  v8 python memory_eval.py --cards --set sets/sealed_v8_rc.json --tag sealed8 | tail -6 | tee results/final_v8_recall_cards.txt
fi
# long chats: the website path (history cut to 12 messages / 6,000 characters), one server copy per arm, no answer cache
long_arm() {   # long_arm "<env>" <tag>
  local P=${LONG_PORT:-49160}
  ( cd web && env $1 SITE_CACHE=0 MOVIE_BUFF_CODE=movies PORT=$P python server.py > ../logs/final_long_$2.log 2>&1 ) &
  local pid=$!
  for i in $(seq 1 100); do curl -s -H "X-Access-Code: movies" localhost:$P/api/health | grep -q '"ok"' && break; sleep 3; done
  env $1 python tools/long_eval.py --set sets/sealed_v8_long.json --base http://localhost:$P --tag sealed8_long_$2 --judge | tail -12
  pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null; sleep 3; pkill -f "PORT=$P" 2>/dev/null; true
}
if has long; then
  echo "=== long chats v7 $(date +%H:%M)"; long_arm "$V7_ENV" v7
  echo "=== long chats v8 $(date +%H:%M)"; long_arm "$V8_ENV" v8
fi
if has features; then
  for sw in SAME_NAME TV_SEASONS CLARIFY; do l=$(echo $sw | tr A-Z a-z)
    echo "=== feature arm: v8 without $sw (sn) $(date +%H:%M)"; v8 env $sw=0 python eval_v3.py --questions sets/sealed_v8_sn.json --tag sealed8_sn_no-$l --answers | tail -4; done
  if has long; then echo "=== feature arm: v8 without LONG_SUMMARY (long) $(date +%H:%M)"; long_arm "$V8_ENV LONG_SUMMARY=0" no-long_summary; fi
  echo "=== feature arm: v8 without IDENT_HONEST (inv) $(date +%H:%M)"
  v8 env IDENT_HONEST=0 python eval_v3.py --questions results/sealed_v8_inv_ev.json --tag sealed8_inv_no-ident_honest --answers | tail -4
fi
if has score; then
  echo "=== scores without the judge $(date +%H:%M)"; python final_score.py
  echo "=== judge audit $(date +%H:%M)"; python judge_audit.py "results/v3eval_sealed8_*.json" \
    --sets sets/sealed_v8_an.json sets/sealed_v8_sn.json results/sealed_v8_inv_ev.json > results/final_v8_audit.txt; tail -3 results/final_v8_audit.txt
fi
if has summary; then
  { echo "v8 sealed run - $(date)"; head -4 "$STAMP" 2>/dev/null; echo
    for f in results/final_v8_*_compare.txt; do echo "## $f"; head -12 "$f"; echo; done
    echo "## retrieval / multi-turn (stage_scores)"; for t in ret mt inv; do for a in v7 v8; do
      f=$(ls -t results/stage_scores_*_sealed8_${t}_$a.json 2>/dev/null | head -1); [ -n "$f" ] && echo "  $t $a: $f"; done; done
    echo "## recall"; cat results/final_v8_recall_v7.txt results/final_v8_recall_v8.txt 2>/dev/null
    echo "## memory cards on the sealed recall set (report-only arm; rule: right not lower, false not higher)"
    cat results/final_v8_recall_cards.txt 2>/dev/null
    echo "## same-name routing"; tail -3 results/final_v8_sn_routing.txt 2>/dev/null
    echo "## scores without the judge"; cat results/final_v8_scores.txt 2>/dev/null
    echo "## judge audit (flags = checker likely wrong; read these rows before deciding)"; cat results/final_v8_audit.txt 2>/dev/null
    echo "## long chats (tools/long_eval.py --judge: eval_v3 judge verdict against the notes, context = the conversation + the films given; rule >= 5/6 with LONG_SUMMARY)"
    for f in results/long_sealed8_long_*.json; do [ -f "$f" ] && python -c "import json,sys; d=json.load(open(sys.argv[1])); print(f\"  {d['tag']}: {d['pass']}/{d['n']}\")" "$f"; done; } > "$SUM"
  echo "=== summary -> $SUM"
fi
echo "=== run_final_v8.sh finished $(date)"
