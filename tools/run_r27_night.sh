#!/usr/bin/env bash
# Round 27 night (11 Nov 2026): tuning runs for the features built today. Tuning sets only, plus the NEW sealed attack
# set for guard v2 (its first and only run). One GPU job at a time; waits for other eval jobs. Rules were written
# before any of these runs (docs/decision_log.md, 11 Nov round 27) and are applied by tools/r27_rules.py.
#   bash tools/run_r27_night.sh                  # every step
#   bash tools/run_r27_night.sh cards guardq     # some steps
# Steps:  cards     3a memory cards: recall tuning set (rule) + the harder set (reported)
#         guardq    guard v2 step 1 on every normal question (false blocks, rule <= 1) - CPU, seconds
#         base      answers + judge on analytic / reception / tv with the v8 lock (the comparison arm)
#         persona   the same with PERSONA=cinemack
#         ctx3      the same with CTX_BUILDER=1 CTX_BUILDER_V=3
#         guard2    the same with GUARD2=1, then the attack sets: v7 dev set and the sealed v2 set, GUARD2 off and on
#         claim4c   claim_rewrite.py on the base answers (analytic, reception), judged again, claimcheck before / after
#         embed     tools/embed_ft.py all (data, base, train, shadow, eval) - the longest step, last
#         rules     tools/r27_rules.py -> results/r27_night.txt
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
STEPS=${*:-cards guardq base persona ctx3 guard2 claim4c rules embed rules}
JOB="r27night-$(date +%Y%m%d-%H%M%S)"; RUN="runs/$JOB"; mkdir -p "$RUN" results docs
exec > >(tee -a "$RUN/log.txt") 2>&1
echo "=== $JOB  steps: $STEPS  $(date)"
L7=$(grep -v '^#' v7_lock.env | tr '\n' ' '); L8=$(grep -v '^#' v8_lock.env | tr '\n' ' ')
BASE="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1 $L7 $L8"
echo "base arm: $BASE"
wait_gpu() { while pgrep -f "stage_scores.py|eval_v3.py|memory_eval.py|run_v8_tune.sh|run_final_v8|long_eval.py|attack_eval.py|embed_ft.py" >/dev/null; do
  echo "waiting for another eval job $(date +%H:%M)"; sleep 120; done; }
sets3() {   # $1 = tag prefix, rest = extra env
  local T=$1; shift
  for s in analytic:an reception:rc tv:tv; do
    local f=${s%%:*} k=${s##*:}
    wait_gpu; env $BASE "$@" python eval_v3.py --questions sets/$f.json --tag ${T}_$k --answers | tail -16
  done
}
step() { echo "=== step $1 $(date +%H:%M)"; }
for s in $STEPS; do
  case $s in
    cards) step cards; wait_gpu
      env $BASE python memory_eval.py --cards --tag r27tune | tail -25
      env $BASE python memory_eval.py --cards --set sets/recall_hard.json --tag r27hard | tail -40 ;;
    guardq) step guardq; GUARD2=1 python safety2.py falseblocks | tail -25 ;;
    base) step base; sets3 r27base ;;
    persona) step persona; sets3 r27persona PERSONA=cinemack ;;
    ctx3) step ctx3; sets3 r27ctx3 CTX_BUILDER=1 CTX_BUILDER_V=3 ;;
    guard2) step guard2; sets3 r27guard2 GUARD2=1
      for g in 0 1; do
        wait_gpu; env $BASE GUARD2=$g MODERATION=1 python attack_eval.py --tag r27dev_g$g | tail -14
        wait_gpu; env $BASE GUARD2=$g MODERATION=1 python attack_eval.py --set sets/sealed_attacks_v2.json --tag r27sealed_g$g | tail -20
      done ;;
    claim4c) step claim4c
      for k in an rc; do
        [ -f results/v3eval_r27base_$k.json ] || { echo "no base run for $k: run the base step first"; continue; }
        wait_gpu; CLAIM_REWRITE=1 python claim_rewrite.py eval results/v3eval_r27base_$k.json --tag r27claim_$k | tail -6
        wait_gpu; python eval_v3.py --rejudge results/v3eval_r27claim_$k.json | tail -12
        python claimcheck.py eval results/v3eval_r27base_$k.json | tail -1
        python claimcheck.py eval results/v3eval_r27claim_$k.json | tail -1
      done ;;
    embed) step embed; wait_gpu; python tools/embed_ft.py all 2>&1 | grep -v "^  step [0-9]*[1-9]00/" | tail -40 ;;
    rules) step rules; python tools/r27_rules.py | tee results/r27_night.txt ;;
    *) echo "unknown step $s" ;;
  esac
done
echo "- $(date -Is) $JOB: $STEPS (log $RUN/log.txt)" >> docs/daily_progress.md
echo "=== $JOB done $(date)"
