#!/usr/bin/env bash
# v3, night of Tue 6 -> Wed 7 Oct: finish the data and the last experiments on the CPU
# (the GPU is held by another user's job). Starts after run_v3d.sh has finished.
#
# Steps (bash run_v3e.sh <step> ...):
#   report     context-window use (context_report.py), training-data export (feedback.py pairs),
#              saved-chat counts (web/chats.py stats) - seconds, no model calls
#   embed      the remaining Wikipedia section passages (stopped at 85,056 of 160,547 on 5 Oct;
#              resumable, skips what is done)
#   reranker   P4 on the CPU: run_v3c.sh with a small base (MiniLM-L6, 22M) instead of
#              bge-reranker-v2-m3 (568M, needs the GPU). Same decision rule as run_v3c.sh.
#   reviews    review analysis for more films (llama3.2), resumable; stops by 07:30 so the
#              sealed run can start in the morning
#   summary    results/v3e_summary.txt
#
# More section vectors and analysed reviews change what TOOLS can hand the answer model, so the
# sealed run (Wed) measures v3 with the completed data; the 6 Oct analytic numbers used 85k vectors.
#
#   tmux new -s v3e
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_v3e.sh
set -u
cd "$(dirname "$0")"
LOG=results/v3e.log
SUM=results/v3e_summary.txt
STEPS=${*:-report embed reranker reviews summary}
RR_BASE_CPU=${RR_BASE_CPU:-cross-encoder/ms-marco-MiniLM-L-6-v2}
STOP_AT=${STOP_AT:-07:30}
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3e.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
while pgrep -f "run_v3d.sh|stage_scores.py|eval_v3.py" >/dev/null; do
  echo "waiting for run_v3d.sh / another run ... $(date +%H:%M)"; sleep 300; done
secs_left() {   # seconds until the next STOP_AT
  local now t; now=$(date +%s); t=$(date -d "$STOP_AT" +%s)
  [ "$t" -le "$now" ] && t=$(date -d "tomorrow $STOP_AT" +%s)
  echo $(( t - now ))
}

if has report; then
  echo "=== report $(date +%H:%M)"
  python context_report.py
  python feedback.py pairs
  (cd web && python chats.py stats) || true
fi

if has embed; then
  echo "=== section embeddings $(date +%H:%M)"
  python wiki_sections.py embed 2>&1 | tail -5
fi

if has reranker; then
  echo "=== P4 re-ranker on the CPU, base $RR_BASE_CPU $(date +%H:%M)"
  python -c "import torch, transformers, sentence_transformers" 2>/dev/null \
    || pip install -q torch transformers sentence-transformers 2>&1 | tail -2
  RR_BASE=$RR_BASE_CPU RR_DEVICE=${RR_DEVICE:-} bash run_v3c.sh data train eval summary 2>&1 | tail -40
fi

if has reviews; then
  left=$(secs_left)
  if [ "$left" -gt 1800 ]; then
    echo "=== review analysis until $STOP_AT ($((left / 60)) min) $(date +%H:%M)"
    REVIEW_FILMS_MAX=1500 timeout "$left" python reviews_analyze.py --films 1500 2>&1 | tail -5
    echo "(stopped or finished; resumable)"
  else
    echo "=== reviews skipped: less than 30 min before $STOP_AT"
  fi
fi

if has summary; then
{
  echo "v3e summary  $(date)"
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null
  echo; echo "##### CONTEXT WINDOW"; cat results/context_report.txt 2>/dev/null
  echo; echo "##### TRAINING DATA"; wc -l results/preference_pairs.jsonl results/sft_examples.jsonl 2>/dev/null
  echo; echo "##### DATA"
  python - <<'PY'
import facts_db as F
c = F.connect(readonly=True)
for q, name in (("select count(*) from sections", "section passages"),
                ("select count(*) from sec_vec", "section vectors"),
                ("select count(*) from reviews", "reviews"),
                ("select count(distinct tmdb_id) from film_aspects", "films with analysed reviews"),
                ("select count(*) from review_aspects", "review aspects")):
    try:
        print(f"  {name:28s} {c.execute(q).fetchone()[0]:,}")
    except Exception as e:
        print(f"  {name:28s} ? ({e})")
PY
  echo; echo "##### P4 RE-RANKER (CPU)"; cat results/v3c_summary.txt 2>/dev/null
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_v3e.sh done $(date)"
