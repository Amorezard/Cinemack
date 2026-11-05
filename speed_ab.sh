#!/usr/bin/env bash
# D9 (v7, 27 Oct): speed - does a tuned Ollama answer faster with the same passes?
#   arm A  the shared Ollama service as it is (port 11434)
#   arm B  a second Ollama server of yours on port 11435 with flash attention, an 8-bit KV cache and 2 parallel slots
#          (OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=2); the shared service is not touched
#   arm A+ PROMPT_ORDER=1 on arm A (fixed instructions first: Ollama can reuse that prefix between questions)
# Runs A-B-A-B (+ A+) on 20 TREC questions (retrieval, no cache) and 18 analytic questions (answers, no cache), only
# when nothing else is running, and writes results/speed_ab.txt. Rule: an arm is adopted if it is faster AND passes
# the same questions (v7_rules.py stage / regression, max lost 0 between arms of the same code).
#
#   bash speed_ab.sh            # ~1.5 h
#   MODELS_DIR=/path/to/models bash speed_ab.sh   # if arm B still finds no models
#
# 2 Nov fixes (Sun night gave no result):
#   1. Retrieval ran 0 questions. Without --questions, stage_scores.py loads only the built-in TEST SET / FOLLOW-UP /
#      FRESH groups, so --group TREC removed all of them; the empty report divided by zero before writing the JSON
#      (12 s = start-up + warm-up). Now: --questions trec_tot_2023.json --split dev --limit 20, and a count check.
#   2. Arm B had no models: a server started as you uses ~/.ollama/models, not the service's folder. Now the script
#      finds the service's models folder (or MODELS_DIR) and passes it with `env` (the old `${MODELS_DIR:+X=Y} nohup`
#      prefix is not an assignment in bash - it would have run "OLLAMA_MODELS=..." as a command). A B server left
#      running from an earlier night is restarted. If B still lists no movie-buff model, the B arms are skipped and
#      the summary says so, instead of printing 0 s.
#   3. The pass check used an unmatched glob (literal '*' reached Python). Now: newest file for each tag, or a message.
#   4. Arms run with the live switches (EVAL_ENV + v7_lock.env), like the rest of Monday, and a failed command is
#      reported as FAILED rather than as a time.
set -u -o pipefail
cd "$(dirname "$0")"
OUT=results/speed_ab.txt
mkdir -p results logs
TREC_Q=${TREC_Q:-trec_tot_2023.json}
[ -f "$TREC_Q" ] || { echo "no $TREC_Q here (python trec_tot.py makes it): stop" | tee $OUT; exit 1; }
EVAL_ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1"
LOCK=$(grep -v '^#' v7_lock.env 2>/dev/null | tr '\n' ' ')
# SEMCACHE=0 for timing: with the semantic answer cache on, A2 / B2 would replay A1's answers and look fast.
NOCACHE="SEMCACHE=0"
# 1 Nov fix: the old check also matched run_v7_night.sh - the script that starts this one - so inside a night it
# always stopped ("another evaluation is running"). Now: wait (up to 60 min) only for an evaluation that is
# actually running (stage_scores.py / eval_v3.py), then go on.
for i in $(seq 1 30); do
  pgrep -f "stage_scores.py|eval_v3.py" >/dev/null || break
  [ "$i" = 30 ] && { echo "an evaluation is still running after 60 min: stop"; exit 1; }
  echo "waiting for another evaluation to finish ... $(date +%H:%M)"; sleep 120
done
free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1)
echo "GPU free: ${free:-?} MiB   lock: ${LOCK:-(no v7_lock.env)}" | tee $OUT
unload() { for m in movie-buff llama3.2 gemma3:27b mxbai-embed-large; do
  OLLAMA_HOST=$1 python -c "import ollama,sys; ollama.generate(model='$m', prompt='', keep_alive=0)" 2>/dev/null; done; }

models_dir() {   # a models folder that really holds movie-buff (2 Nov pm: ~/.ollama/models existed but was empty)
  local d svc
  svc=$(systemctl show ollama -p Environment 2>/dev/null | grep -o 'OLLAMA_MODELS=[^ ]*' | cut -d= -f2)
  for d in "${MODELS_DIR:-}" "$svc" /usr/share/ollama/.ollama/models /var/lib/ollama/.ollama/models \
           /var/lib/ollama/models "$HOME/.ollama/models"; do
    [ -n "$d" ] && [ -r "$d/manifests/registry.ollama.ai/library/movie-buff" ] && { echo "$d"; return 0; }
    [ -n "$d" ] && [ -e "$d" ] && echo "  (models folder $d: no readable movie-buff)" >&2
  done
  return 1
}
b_models() { curl -s localhost:11435/api/tags 2>/dev/null | python -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(' '.join(m['name'] for m in d.get('models',[])))"; }
B_OK=0
start_b() {
  if curl -s localhost:11435/api/version >/dev/null; then          # left over from an earlier run
    if [[ " $(b_models) " == *" movie-buff"* ]]; then echo "arm B: reusing the server on 11435" | tee -a $OUT; B_OK=1; return 0; fi
    if [ -f logs/ollama_b.pid ]; then kill "$(cat logs/ollama_b.pid)" 2>/dev/null; rm -f logs/ollama_b.pid; sleep 3
    else echo "arm B: port 11435 is in use (not by this script) and has no movie-buff: B arms skipped" | tee -a $OUT; return 1; fi
  fi
  local md; md=$(models_dir) || { echo "arm B: no readable Ollama models folder (set MODELS_DIR=...): B arms skipped" | tee -a $OUT; return 1; }
  echo "arm B models folder: $md" | tee -a $OUT
  env OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS="$md" OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
      OLLAMA_NUM_PARALLEL=2 nohup ollama serve > logs/ollama_b.log 2>&1 &
  echo $! > logs/ollama_b.pid
  for i in $(seq 1 15); do curl -s localhost:11435/api/version >/dev/null && break; sleep 2; done
  local names; names=$(b_models)
  echo "arm B models: ${names:-none}" | tee -a $OUT
  if [[ " $names " == *" movie-buff"* ]]; then B_OK=1; else
    echo "arm B: movie-buff not listed (see logs/ollama_b.log; folder permissions?): B arms skipped" | tee -a $OUT; fi
}
arm() {   # arm <name> <host> [env...]
  local name=$1 host=$2; shift 2
  unload 127.0.0.1:11434; [ "$B_OK" = 1 ] && unload 127.0.0.1:11435
  local t0 t1 t2 n rc1 rc2
  t0=$(date +%s)
  env $EVAL_ENV $LOCK $NOCACHE OLLAMA_HOST=$host "$@" python stage_scores.py --no-cache --questions "$TREC_Q" --split dev \
      --limit 20 --tag speed_$name > logs/speed_$name.log 2>&1; rc1=$?
  t1=$(date +%s)
  env $EVAL_ENV $LOCK $NOCACHE OLLAMA_HOST=$host "$@" python eval_v3.py --no-cache --answers --no-judge \
      --category factual,reviews,why --tag speed_$name >> logs/speed_$name.log 2>&1; rc2=$?
  t2=$(date +%s)
  n=$(grep -m1 -o '^[0-9]* questions' logs/speed_$name.log | grep -o '^[0-9]*'); n=${n:-0}
  local ret="retrieval $n TREC $((t1 - t0)) s ($(( (t1 - t0) / (n > 0 ? n : 1) )) s/q)"
  [ "$rc1" != 0 ] || [ "$n" = 0 ] && ret="retrieval FAILED (exit $rc1, $n questions; see logs/speed_$name.log)"
  local ans="answers $((t2 - t1)) s"; [ "$rc2" != 0 ] && ans="answers FAILED (exit $rc2)"
  echo "$name: $ret; $ans" | tee -a $OUT
}
newest() { ls -t results/stage_scores_*_speed_"$1".json 2>/dev/null | head -1; }
start_b
arm A1 127.0.0.1:11434
[ "$B_OK" = 1 ] && arm B1 127.0.0.1:11435
arm A2 127.0.0.1:11434
[ "$B_OK" = 1 ] && arm B2 127.0.0.1:11435
arm Aplus 127.0.0.1:11434 PROMPT_ORDER=1
echo "--- passes (same code; should not lose any)" | tee -a $OUT
pass_check() {   # pass_check <label> <tag a> <tag b>
  local a b; a=$(newest "$2"); b=$(newest "$3")
  if [ -n "$a" ] && [ -n "$b" ]; then echo "  $1 ($2 vs $3):" | tee -a $OUT
    python v7_rules.py stage "$a" "$b" --max-lost 0 2>&1 | tail -2 | tee -a $OUT
  else echo "  $1: no result file for $2 or $3" | tee -a $OUT; fi
}
pass_check "A run to run" A1 A2
[ "$B_OK" = 1 ] && pass_check "A vs B server" A1 B1
pass_check "A vs A+ (PROMPT_ORDER)" A1 Aplus
echo "--- per-answer seconds (eval_v3 timing.answer_s), mean:" | tee -a $OUT
python - <<'PY' | tee -a $OUT
import glob, json, statistics
for tag in ("A1", "B1", "A2", "B2", "Aplus"):
    for p in glob.glob(f"results/v3eval_speed_{tag}.json"):
        rows = json.load(open(p))["rows"]
        a = [r["timing"].get("answer_s") for r in rows if r["timing"].get("answer_s")]
        print(f"  {tag}: {statistics.mean(a):.1f} s per answer over {len(a)}" if a else f"  {tag}: no answers")
PY
if [ -f logs/ollama_b.pid ]; then kill "$(cat logs/ollama_b.pid)" 2>/dev/null; rm -f logs/ollama_b.pid; echo "(arm B server stopped)" | tee -a $OUT; fi
