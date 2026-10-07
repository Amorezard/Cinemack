#!/usr/bin/env bash
# v3, P4 experiment (Tue 6 Oct): a re-ranker trained for Movie Buff (train_reranker.py).
#   data    4,000 synthetic descriptions (films outside tune/check/final) + TREC train, hard negatives
#           from Movie Buff's own fused search -> results/rr_train.jsonl   (~30-40 min, embeddings)
#   train   fine-tune bge-reranker-v2-m3, listwise loss, 1 epoch (~20-30 min; Ollama models unloaded)
#   eval    tune + check + timing:
#             rr_cl10   RERANK_ENGINE=cross_llm, KEEP=10: the trained model keeps 10, the 27B picks from 10
#             rr_cross  RERANK_ENGINE=cross: the trained model alone (no LLM re-rank)
#
# Decision rule (set before results, 5 Oct): adopt an arm only if tune + check passes are at most
# 1 below the locked v2 settings (v3a_tune_v2 / v3a_check_v2) and the re-rank step is >= 40% faster.
# Between two passing arms, the faster.
#
#   tmux new -s v3c
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_v3c.sh
set -u
cd "$(dirname "$0")"
LOG=results/v3c.log
SUM=results/v3c_summary.txt
STEPS=${*:-data train eval summary}
mkdir -p results models
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3c.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
run() { local tag=$1 set=$2; shift 2; echo "=== $tag $set $* $(date +%H:%M)"
        env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4; }
python -c "import torch, transformers, sentence_transformers" 2>/dev/null \
  || { echo "needs torch, transformers, sentence-transformers (pip install them in the venv)"; exit 1; }
while pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|summarize.py" >/dev/null; do
  echo "waiting for another GPU job ... $(date +%H:%M)"; sleep 120; done

has data  && python train_reranker.py data --n 4000 | tail -5
has train && python train_reranker.py train | tail -12

if has eval; then
  M=models/mb-reranker
  [ -f "$M/config.json" ] || { echo "no trained model in $M"; exit 1; }
  for s in tune check; do
    run "v3c_${s}_cl10"  $s RERANK_ENGINE=cross_llm RERANK_CROSS_MODEL=$M RERANK_CROSS_KEEP=10
    run "v3c_${s}_cross" $s RERANK_ENGINE=cross     RERANK_CROSS_MODEL=$M
  done
  RERANK_ENGINE=cross_llm RERANK_CROSS_MODEL=$M RERANK_CROSS_KEEP=10 python stage_scores.py --questions sets/tune.json \
    --group TREC --limit 20 --no-cache --tag time_cl10_trec | tail -3
  RERANK_ENGINE=cross RERANK_CROSS_MODEL=$M python stage_scores.py --questions sets/tune.json \
    --group TREC --limit 20 --no-cache --tag time_cross_trec | tail -3
fi

if has summary; then
{
  echo "v3c summary  $(date)"
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def p(tag):
    d = latest(tag)
    return f"{sum(r['result']['pass'] for r in d['rows'])}/{len(d['rows'])}" if d else "-"
print("passes        tune     check")
for arm, t, c in (("locked v2", "v3a_tune_v2", "v3a_check_v2"), ("cross_llm 10", "v3c_tune_cl10", "v3c_check_cl10"),
                  ("cross only", "v3c_tune_cross", "v3c_check_cross")):
    print(f"{arm:12s} {p(t):>7s} {p(c):>9s}")
print("\ntiming, 20 TREC questions, no cache")
for t in ("time_lock_trec", "time_cl10_trec", "time_cross_trec"):
    d = latest(t)
    if not d:
        continue
    tr = [r["result"]["trace"] for r in d["rows"]]
    tot = [x["total_ms"] for x in tr if x.get("total_ms")]
    rr = [x.get("rerank_ms") for x in tr if x.get("rerank_ms")]
    print(f"  {t:16s} total {sum(tot) / len(tot) / 1000:.1f}s   rerank {sum(rr) / max(len(rr), 1) / 1000:.2f}s on {len(rr)}")
PY
  tail -3 results/v3c.log | grep -i "dev hit" ; grep -h "dev hit" results/v3c.log | tail -3
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_v3c.sh finished $(date)"
