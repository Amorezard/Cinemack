#!/usr/bin/env bash
# v4 (14 Oct): automatic nightly rebuild of the movie-buff LoRA adapter.
#
#   once (by hand)   bash rebuild_adapter.sh --force          first rebuild: the "retrain the LoRA on the
#                                                             RAG answer format" item, with distilled examples
#   nightly (cron)   crontab -e, add:
#                    5 3 * * * /bin/bash $HOME/movie-buff/rag/rebuild_adapter.sh >/dev/null 2>&1
#   without cron     tmux new -s adapter ; bash rebuild_adapter.sh daemon      (runs every night at 03:05)
#   other            bash rebuild_adapter.sh status | list | prune | rollback | dry
#
# Each night, in order (log: logs/adapter_rebuild.log, one line per night: results/adapter_builds.log):
#   1. like_filter.py run        screen new thumbs-up answers (semantic filter)
#   2. distill (weekly)          live-model answers to fresh training questions, judged; passes kept
#   3. adapter_data.py build     training set: persona + written + liked + corrected + distill, minus
#                                anything older than ADAPTER_MAX_AGE_DAYS (90) and anything that overlaps an
#                                evaluation set. Fewer than ADAPTER_MIN_NEW (50) new examples -> stop here.
#   4. train                     train_adapter.py (Unsloth QLoRA) or your ADAPTER_TRAIN_CMD; needs the GPU free
#   5. convert + ollama create   lora.gguf (llama.cpp convert_lora_to_gguf.py) -> model movie-buff-aYYYYMMDD
#                                (original Modelfile, new ADAPTER line)
#   6. gate                      candidate vs live on sets/analytic.json (answers + judge) and sets/check.json;
#                                rule in adapter_data.py (written before any build)
#   7. promote                   pass -> live_model.yaml says ANSWER_MODEL: movie-buff-aYYYYMMDD
#                                (SITE_RESTART=1 restarts the website in tmux session SITE_TMUX)
#   8. prune                     keep the newest ADAPTER_KEEP (3) good adapters + the live one + the original
#                                movie-buff; `ollama rm` the rest and delete their builds/ folders; expired
#                                gate/distill result files go too
#
# Settings: adapter.env next to this script (sourced; not in git). The defaults match this server:
#   ADAPTER_BASE_HF=unsloth/Qwen3.8-27B-unsloth-bnb-4bit     base the persona LoRA was trained on
#   TRAIN_VENV=~/venvs/train-lora                            Unsloth / TRL venv for training + conversion
#   LLAMA_CPP=~/llama.cpp-patched                            convert_lora_to_gguf.py
#   ADAPTER_PERSONA=~/movie-buff/movie_training_data.jsonl   the 16 original persona examples
#   ADAPTER_MODELFILE=~/movie-buff/Modelfile                 FROM qwen3.8:27b-mtp-q4_K_M + PARAMETERs + SYSTEM
#   ADAPTER_TRAIN_CMD='...{data}...{out}'                    optional: another trainer instead of train_adapter.py
#   export ADAPTER_MIN_NEW=50 ADAPTER_MAX_AGE_DAYS=90 ADAPTER_KEEP=3 ADAPTER_MIN_FREE_MB=20000
#   export SITE_RESTART=0 SITE_TMUX=moviebuff SITE_CMD='cd ~/movie-buff/rag/web && MOVIE_BUFF_CODE=movies python server.py'
set -u
cd "$(dirname "$0")"
export PATH="$PATH:/usr/local/bin:/usr/bin:/bin"
[ -f "${VENV:-$HOME/venvs/movie-rag}/bin/activate" ] && source "${VENV:-$HOME/venvs/movie-rag}/bin/activate"
[ -f adapter.env ] && source adapter.env
mkdir -p logs results builds
CMD=${1:-run}
case "$CMD" in
  status) python adapter_data.py status; exit ;;
  list) python adapter_data.py list; exit ;;
  prune) python adapter_data.py prune --keep "${ADAPTER_KEEP:-3}"; exit ;;
  rollback) python adapter_data.py rollback; exit ;;
  daemon)
    while true; do
      now=$(date +%s); next=$(date -d "tomorrow 03:05" +%s)
      [ "$(date +%H%M)" -lt 0305 ] && next=$(date -d "today 03:05" +%s)
      echo "next rebuild $(date -d @"$next")"; sleep $((next - now))
      bash "$0" run
    done ;;
esac
FORCE=""; DRY=0
for a in "$@"; do
  [ "$a" = "--force" ] && FORCE="--force"
  [ "$a" = "dry" ] && DRY=1
done

exec 9>logs/adapter_rebuild.lock
flock -n 9 || { echo "another rebuild is running"; exit 0; }
exec >> logs/adapter_rebuild.log 2>&1
DAY=$(date +%Y%m%d)
NAME="movie-buff-a${DAY}"
DIR="builds/${NAME}"
SUMMARY=results/adapter_builds.log
echo; echo "=== rebuild_adapter.sh $(date)  name=$NAME force=${FORCE:-no} dry=$DRY"
note() { echo "$(date '+%F %H:%M') $NAME $*" | tee -a "$SUMMARY"; }

# Leave the GPU to evaluations already running (wait up to 2 h, then skip the night).
for i in $(seq 1 24); do
  pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|train_reranker.py|chunk_context.py build|news.py fetch" >/dev/null || break
  [ "$i" = 24 ] && { note "skipped: another run kept going for 2 h"; exit 0; }
  echo "waiting for another run ... $(date +%H:%M)"; sleep 300
done
LIVE=$(python -c "import adapter_data as A; print(A.live_model())")
echo "live model: $LIVE"

echo "--- 1. like filter"
python like_filter.py run ${LIKE_LLM:+--llm} || echo "(like filter failed; continuing with earlier verdicts)"
python like_filter.py report

echo "--- 2. distill (every ${DISTILL_EVERY_DAYS:-7} days)"
if [ "${DISTILL:-1}" = 1 ] && [ "$DRY" = 0 ] && \
   [ -z "$(find results -maxdepth 1 -name 'v3eval_distill_*.json' -mtime -"${DISTILL_EVERY_DAYS:-7}" 2>/dev/null)" ]; then
  python adapter_data.py distill --n "${DISTILL_N:-300}" --seed "$DAY" && \
  ANSWER_MODEL="$LIVE" python eval_v3.py --questions results/distill_questions.json --answers --tag "distill_${DAY}" \
    2>&1 | tail -6
else
  echo "(skipped: recent distill file, DISTILL=0, or dry run)"
fi

echo "--- 3. training set"
mkdir -p "$DIR"
python adapter_data.py build --out "$DIR" $FORCE
rc=$?
if [ $rc = 3 ]; then note "skipped: not enough new examples ($(python -c "import json;print(json.load(open('$DIR/manifest.json'))['n_new'])" 2>/dev/null) new)"
  rm -rf "$DIR"; python adapter_data.py prune --keep "${ADAPTER_KEEP:-3}"; exit 0; fi
[ $rc = 0 ] || { note "failed: training set ($rc)"; exit 1; }
if [ "$DRY" = 1 ]; then note "dry run: training set built in $DIR (no training)"; exit 0; fi

echo "--- 4. train"
# unload only Movie Buff's own models (the Ollama server may be shared with other users)
for m in $(ollama ps 2>/dev/null | awk 'NR>1{print $1}' | grep -E "^(movie-buff|llama3\.2|mxbai-embed-large|gemma3:27b|${ADAPTER_EXTRA_UNLOAD:-qwen3})"); do
  ollama stop "$m" 2>/dev/null; done
sleep 5
FREE=$(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits 2>/dev/null | awk -F', ' '{print $1-$2}' | head -1)
echo "GPU free: ${FREE:-?} MiB"
if [ "${FREE:-0}" -lt "${ADAPTER_MIN_FREE_MB:-20000}" ]; then
  python adapter_data.py register "$NAME" --dir "$DIR" --status failed --note "GPU busy (${FREE:-?} MiB free)"
  note "failed: GPU busy (${FREE:-?} MiB free, need ${ADAPTER_MIN_FREE_MB:-20000})"; exit 1; fi
TPY="${TRAIN_VENV:-$HOME/venvs/train-lora}/bin/python"
[ -x "$TPY" ] || TPY=python
if [ -n "${ADAPTER_TRAIN_CMD:-}" ]; then
  TCMD=${ADAPTER_TRAIN_CMD//\{data\}/$DIR/train.jsonl}; TCMD=${TCMD//\{out\}/$DIR/lora}
  echo "$TCMD"; eval "$TCMD"
else
  "$TPY" train_adapter.py --data "$DIR/train.jsonl" --out "$DIR/lora"
fi
[ -f "$DIR/lora/adapter_config.json" ] || { python adapter_data.py register "$NAME" --dir "$DIR" --status failed --note "training"
  note "failed: training (no adapter written)"; exit 1; }

echo "--- 5. convert + ollama create"
# --base for a local HF folder; for a hub id, the converter reads the base's config from the hub (cached)
BASE_HF="${ADAPTER_BASE_HF:-unsloth/Qwen3.8-27B-unsloth-bnb-4bit}"
if [ -d "$BASE_HF" ]; then BASE_ARG=(--base "$BASE_HF"); else BASE_ARG=(--base-model-id "$BASE_HF"); fi
"$TPY" "${LLAMA_CPP:-$HOME/llama.cpp-patched}/convert_lora_to_gguf.py" "${BASE_ARG[@]}" \
  --outtype f16 --outfile "$DIR/lora.gguf" "$DIR/lora" 2>&1 | tail -5
[ -f "$DIR/lora.gguf" ] || { python adapter_data.py register "$NAME" --dir "$DIR" --status failed --note convert
  note "failed: GGUF conversion"; exit 1; }
python adapter_data.py modelfile --lora "$DIR/lora.gguf" --out "$DIR/Modelfile" || { note "failed: Modelfile"; exit 1; }
ollama create "$NAME" -f "$DIR/Modelfile" 2>&1 | tail -2
ollama show "$NAME" >/dev/null 2>&1 || { python adapter_data.py register "$NAME" --dir "$DIR" --status failed --note create
  note "failed: ollama create"; exit 1; }
python adapter_data.py register "$NAME" --dir "$DIR" --status trained

echo "--- 6. gate: $NAME vs $LIVE"
ANSWER_MODEL="$NAME" python eval_v3.py --answers --tag "gate_${NAME}" 2>&1 | tail -4
ANSWER_MODEL="$NAME" python stage_scores.py --questions sets/check.json --tag "gate_${NAME}" 2>&1 | tail -3
ANSWER_MODEL="$LIVE" python eval_v3.py --answers --tag "gate_live_${DAY}" 2>&1 | tail -4
ANSWER_MODEL="$LIVE" python stage_scores.py --questions sets/check.json --tag "gate_live_${DAY}" 2>&1 | tail -3
if python adapter_data.py gate --cand "gate_${NAME}" --live "gate_live_${DAY}" --name "$NAME"; then
  echo "--- 7. promote"
  python adapter_data.py promote "$NAME"
  note "PROMOTED (was $LIVE)"
  if [ "${SITE_RESTART:-0}" = 1 ] && tmux has-session -t "${SITE_TMUX:-moviebuff}" 2>/dev/null; then
    tmux send-keys -t "${SITE_TMUX:-moviebuff}" C-c; sleep 8
    tmux send-keys -t "${SITE_TMUX:-moviebuff}" "${SITE_CMD:-cd ~/movie-buff/rag/web && python server.py}" Enter
    echo "website restarted in tmux ${SITE_TMUX:-moviebuff}"
  else
    echo "restart the website to serve $NAME"
  fi
else
  note "rejected by the gate (live stays $LIVE)"
fi

echo "--- 8. prune"
python adapter_data.py prune --keep "${ADAPTER_KEEP:-3}"
echo "=== done $(date)"
