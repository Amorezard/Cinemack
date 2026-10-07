#!/usr/bin/env bash
# v3, Wed 7 Oct: the last additions before the sealed run, then a decision file the sealed
# run reads. Run it and the sealed run back to back:
#
#   tmux new -s final
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export TMDB_API_KEY=...        # if it is not already in your environment
#   bash run_v3f.sh; bash run_final_v3.sh
#
# Steps (bash run_v3f.sh <step> ...):
#   data     more films, grosses and awards:
#              fetch_tmdb.py extra       TMDB's most-voted films of every year that are NOT in the
#                                        30,856-film catalogue (facts, budget/gross, credits, keywords)
#              fetch_wikidata.py --from-facts   awards + box office for every film with a Wikidata id
#              tools.py build            money table (best figure per film) + MovieLens stats
#   reviews  review analysis (llama3.2) for more of the most-voted films, for REVIEW_HOURS (default 2.5 h)
#   ctx      context-window experiment on the analytic set (new data for both arms):
#              locked   num_ctx 8192, TOOL_CHARS 9000, 3 section passages x 650 chars
#              ctx16    num_ctx 16384, TOOL_CHARS 20000, 6 section passages x 1200 chars
#            categories detail, compare, reviews, why, career (23 questions; the ones that use
#            Wikipedia sections and long tool data), answers + judge, both arms.
#   gpu      if the GPU has >= 8 GB free: train the bge-reranker-v2-m3 re-ranker on it (run_v3c.sh,
#            the original P4 plan). Otherwise skipped with a note (the CPU MiniLM run was 6-7 Oct).
#   decide   results/v3f_decision.env, read by run_final_v3.sh
#   summary  results/v3f_summary.txt
#
# Decision rule for ctx16 (written 7 Oct 09:55, before any result):
#   adopt NUM_CTX=16384 + the larger tool context for the sealed v3 arm only if, on the 23
#   questions, judge passes are at least locked + 2 AND mean grounding is not lower AND the
#   data check is not lower. Otherwise the sealed run keeps 8192 (and the write-up reports both).
# The data and reviews steps change facts.db for good: the sealed run measures v3 with them.
set -u
cd "$(dirname "$0")"
LOG=results/v3f.log
SUM=results/v3f_summary.txt
DEC=results/v3f_decision.env
STEPS=${*:-data reviews ctx gpu decide summary}
REVIEW_HOURS=${REVIEW_HOURS:-2.5}
CATS=${CATS:-detail,compare,reviews,why,career}
LOCK="CONV=1 TOOLS=1 PERSONAL=1 PERSONAL_W=0.25,2,0.5"
CTX16="NUM_CTX=16384 TOOL_CHARS=20000 SECTION_N=6 SECTION_CHARS=1200"
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3f.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
while pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|summarize.py|train_reranker.py" >/dev/null; do
  echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done
gpu_free_mb() { nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits 2>/dev/null \
                | awk -F', ' '{print $1-$2}' | head -1; }
facts_counts() {
  python - <<'PY'
import facts_db as F
c = F.connect(readonly=True)
q = {"films": "select count(*) from films",
     "films outside the catalogue": "select count(*) from films where in_catalog = 0",
     "films with budget and gross": "select count(*) from money where budget > 0 and gross > 0",
     "films with a gross": "select count(*) from money where gross > 0",
     "award rows": "select count(*) from awards",
     "films with awards": "select count(distinct tmdb_id) from awards",
     "films with analysed reviews": "select count(distinct tmdb_id) from film_aspects",
     "review aspects": "select count(*) from review_aspects"}
for k, s in q.items():
    try:
        print(f"  {k:30s} {c.execute(s).fetchone()[0]:,}")
    except Exception as e:
        print(f"  {k:30s} ? ({e})")
PY
}

if has data; then
  echo "=== data before $(date +%H:%M)"; facts_counts | tee results/v3f_data_before.txt
  if [ -z "${TMDB_API_KEY:-}${TMDB_TOKEN:-}" ]; then
    echo "!!! TMDB_API_KEY not set - skipping the TMDB fetch (export it, then: bash run_v3f.sh data)"
  else
    python fetch_tmdb.py extra --pages 50 --min-votes 100 2>&1 | grep -v "^  extra: " | tail -15
  fi
  python fetch_wikidata.py --from-facts 2>&1 | tail -4
  python tools.py build 2>&1 | tail -3
  echo "=== data after $(date +%H:%M)"; facts_counts | tee results/v3f_data_after.txt
fi

if has reviews; then
  secs=$(python -c "print(int(float('$REVIEW_HOURS') * 3600))")
  echo "=== review analysis for up to $REVIEW_HOURS h $(date +%H:%M)"
  REVIEW_FILMS_MAX=3000 timeout "$secs" python reviews_analyze.py --films 3000 2>&1 | tail -5
  echo "(stopped or finished; resumable)"
fi

if has ctx; then
  echo "=== ctx: locked (8k) $(date +%H:%M)"
  env $LOCK python eval_v3.py --category $CATS --answers --tag ctx_lock | tail -14
  echo "=== ctx: 16k + more tool context $(date +%H:%M)"
  env $LOCK $CTX16 python eval_v3.py --category $CATS --answers --tag ctx_16k | tail -14
  python eval_v3.py --compare results/v3eval_ctx_lock.json results/v3eval_ctx_16k.json > results/v3f_ctx_compare.txt 2>&1
  tail -30 results/v3f_ctx_compare.txt
fi

if has gpu; then
  free=$(gpu_free_mb); free=${free:-0}
  if [ "$free" -ge 8000 ]; then
    echo "=== GPU has ${free} MiB free: bge-reranker-v2-m3 on the GPU (run_v3c.sh) $(date +%H:%M)"
    RR_BASE=BAAI/bge-reranker-v2-m3 RR_DEVICE=cuda bash run_v3c.sh data train eval summary 2>&1 | tail -25
  else
    echo "=== GPU: only ${free} MiB free - GPU fine-tuning skipped (needs ~8 GB; another user's job holds the rest)"
  fi
fi

if has decide; then
  python - <<'PY' > "$DEC"
import json
def load(tag):
    try:
        return json.load(open(f"results/v3eval_{tag}.json"))["rows"]
    except (OSError, ValueError, KeyError):
        return None
a, b = load("ctx_lock"), load("ctx_16k")
if not (a and b):
    print("# ctx experiment not run: sealed run keeps 8192")
else:
    def stats(rows):
        js = [r.get("judge") or {} for r in rows]
        p = sum(j.get("verdict") == "pass" for j in js)
        g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float))]
        d = sum(1 for r in rows if (r.get("grades") or {}).get("data"))
        return p, (sum(g) / len(g) if g else 0), d
    pa, ga, da = stats(a); pb, gb, db = stats(b)
    ok = pb >= pa + 2 and gb >= ga and db >= da
    print(f"# ctx experiment: locked pass {pa}/{len(a)} grounding {ga:.2f} data {da}; "
          f"16k pass {pb}/{len(b)} grounding {gb:.2f} data {db}; rule (pass +2, grounding and data not lower): "
          f"{'PASS' if ok else 'FAIL'}")
    if ok:
        print("V3_EXTRA='NUM_CTX=16384 TOOL_CHARS=20000 SECTION_N=6 SECTION_CHARS=1200'")
PY
  cat "$DEC"
fi

if has summary; then
{
  echo "v3f summary  $(date)"
  echo "GPU free MiB: $(gpu_free_mb)"
  echo; echo "##### DATA (before -> after)"
  paste -d'|' results/v3f_data_before.txt results/v3f_data_after.txt 2>/dev/null | sed 's/|/   ->/'
  echo; echo "##### CONTEXT WINDOW EXPERIMENT"
  for t in ctx_lock ctx_16k; do python eval_v3.py --report results/v3eval_$t.json 2>/dev/null | tail -8; done
  grep -E "^  [as][0-9]" results/v3f_ctx_compare.txt 2>/dev/null
  echo; echo "##### DECISION"; cat "$DEC" 2>/dev/null
  echo; echo "##### GPU"; grep -E "GPU" "$LOG" | tail -2
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_v3f.sh done $(date)"
