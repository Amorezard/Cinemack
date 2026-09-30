#!/usr/bin/env bash
# E9 (v2 plan, night of 29-30 Sep): speed without losing accuracy, plus two accuracy tries.
# Baseline = the locked defaults (gap 0.03, no second re-rank, fast passages, movie-buff planner).
#   e9_vec       VEC_MEM=1      exact vector search in numpy instead of sqlite-vec (vecmem.py).
#                               Should be IDENTICAL: expect 0 new model calls and the same passes.
#   e9_route     PLAN_ROUTE=1   llama3.2 plans first; movie-buff plans only plot descriptions.
#                               Mostly replayed from the cache (E8 qsmall + current runs).
#   e9_x15       Qwen3-Reranker-0.6B narrows the re-ranker's 50 films to 15 for movie-buff (speed)
#   e9_x30n100   Qwen3-Reranker-0.6B narrows 100 films to 30 (accuracy: sees films #51-100)
#   e9_n65       65 films x 300 characters, about the same prompt size as 50 x 400 (accuracy)
# Then no-cache timing on 20 TREC + 20 own questions.
#   bash run_e9.sh --summary   rebuilds the summary from the saved result files
#   tmux new -s e9   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e9.sh
set -u
cd "$(dirname "$0")"
LOG=results/e9.log
SUM=results/e9_summary.txt
XMODEL=${XMODEL:-tomaarsen/Qwen3-Reranker-0.6B-seq-cls}
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e9.sh started $(date)   XMODEL=$XMODEL"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_CHARS RERANK_AGREE RERANK_GAP RERANK_ENGINE RERANK_SECOND RRF_WEIGHTS RRF_K PLAN_FAST QUERY_MODEL \
      HYDE HYBRID ANSWER_WIKI ANSWER_WIKI_CHARS ANSWER_STYLE ANSWER_STRICT NUM_BATCH WIKI_PASSAGE_FAST \
      VEC_MEM PLAN_ROUTE ROUTE_MODEL RERANK_CROSS_MODEL RERANK_CROSS_KEEP RERANK_CROSS_INSTRUCT

grep -q "PLAN_ROUTE" retrieve.py || { echo "retrieve.py is old (no PLAN_ROUTE) - copy the new one up first"; exit 1; }
[ -f vecmem.py ] || { echo "vecmem.py missing - copy it up first"; exit 1; }

X15="RERANK_ENGINE=cross_llm RERANK_CROSS_MODEL=$XMODEL RERANK_CROSS_KEEP=15"
X30="RERANK_ENGINE=cross_llm RERANK_CROSS_MODEL=$XMODEL RERANK_CROSS_KEEP=30 RERANK_N=100"
N65="RERANK_N=65 RERANK_WIKI_CHARS=300"

if [ "${1:-}" != "--summary" ]; then
while pgrep -f "stage_scores.py" >/dev/null; do echo "waiting for another stage_scores.py run ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -6
}

echo "=== vector cache $(date +%H:%M)"
[ -f vec_cache/meta.json ] || python vecmem.py build
python vecmem.py check || echo "vecmem check FAILED"

echo "=== model call overhead probe $(date +%H:%M)"
python - <<'PY'
import time, ollama
def show(tag, r):
    g = r.get if hasattr(r, "get") else (lambda k, d=None: getattr(r, k, d))
    ms = lambda k: (g(k) or 0) / 1e6
    print(f"  {tag:34s} load {ms('load_duration'):6.0f} ms  read {ms('prompt_eval_duration'):6.0f} ms "
          f"({g('prompt_eval_count')} tok)  write {ms('eval_duration'):5.0f} ms  total {ms('total_duration'):6.0f} ms")
msg = [{"role": "user", "content": "Name one film by Steven Spielberg."}]
for i in range(3):
    show(f"movie-buff plain #{i + 1}", ollama.chat(model="movie-buff", messages=msg, think=False, options={"num_predict": 8}))
for i in range(2):
    show(f"movie-buff format=json #{i + 1}", ollama.chat(model="movie-buff", messages=msg, think=False, format="json", options={"num_predict": 8}))
ollama.embed(model="mxbai-embed-large", input=["a question"])
show("movie-buff right after an embed", ollama.chat(model="movie-buff", messages=msg, think=False, options={"num_predict": 8}))
show("llama3.2", ollama.chat(model="llama3.2", messages=msg, think=False, options={"num_predict": 8}))
show("movie-buff right after llama3.2", ollama.chat(model="movie-buff", messages=msg, think=False, options={"num_predict": 8}))
PY

echo "=== small re-ranker load test $(date +%H:%M)"
XOK=1
RERANK_CROSS_MODEL=$XMODEL python - <<'PY' || XOK=0
import retrieve as R
s = R.cross_scores("a boy finds an alien in his backyard and hides it from his mother",
                   ["E.T. the Extra-Terrestrial (1982). A troubled child summons the courage to help a friendly alien escape Earth.",
                    "Jaws (1975). A police chief hunts a man-eating great white shark with a scientist and a fisherman."])
print("  scores:", s)
assert s[0] > s[1], "the small re-ranker ranked the wrong film first"
PY
[ "$XOK" = 1 ] || echo "  small re-ranker did not load or failed the sanity check - skipping the e9_x runs"

# 1) cached accuracy: baseline, the in-memory search (must be identical), the two-step planner
for set in tune check; do
  run v2_locked  $set VEC_MEM=0
  run e9_vec     $set VEC_MEM=1
  run e9_route   $set VEC_MEM=1 PLAN_ROUTE=1
done
# 2) no-cache timing (20 TREC, 20 own)
T="-- --no-cache --group TREC --limit 20"
O="-- --no-cache --group OWN --limit 20"
run time_e9_base      tune VEC_MEM=0 $T
run time_e9_vec       tune VEC_MEM=1 $T
run time_e9_vecroute  tune VEC_MEM=1 PLAN_ROUTE=1 $T
[ "$XOK" = 1 ] && run time_e9_x15 tune VEC_MEM=1 $X15 $T
run time_e9_own_base     tune VEC_MEM=0 $O
run time_e9_own_vecroute tune VEC_MEM=1 PLAN_ROUTE=1 $O
# 3) accuracy tries (new model calls)
for set in tune check; do
  [ "$XOK" = 1 ] && run e9_x15    $set VEC_MEM=1 $X15
  [ "$XOK" = 1 ] && run e9_x30n100 $set VEC_MEM=1 $X30
  run e9_n65 $set VEC_MEM=1 $N65
done
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E9 summary  $(date)"
  echo; echo "##### VECTOR CACHE + MODEL CALL PROBE (from the log)"
  sed -n '/=== vector cache/,/=== v2_locked tune/p' "$LOG" | grep -v "^=== v2_locked" | tail -25
  echo; echo "##### SPEED BUDGET (no cache): mean seconds per stage"
  python - time_e9_base time_e9_vec time_e9_vecroute time_e9_x15 time_e9_own_base time_e9_own_vecroute <<'PY'
import glob, json, os, statistics as st, sys
def rows(tag):
    files = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return [r["result"]["trace"] for r in json.load(open(files[-1]))["rows"]] if files else None
print(f"{'run':22s} {'n':>3s} {'plan':>5s} {'search':>6s} {'rerank':>6s} {'total':>6s} {'median':>6s} {'<=5s':>5s}")
for tag in sys.argv[1:]:
    tr = rows(tag)
    if not tr:
        print(f"{tag:22s} (missing)"); continue
    m = lambda k: st.mean(t.get(k, 0) for t in tr) / 1000
    tot = [t.get("total_ms", 0) / 1000 for t in tr]
    print(f"{tag:22s} {len(tr):3d} {m('plan_ms'):5.1f} {m('search_ms'):6.1f} {m('rerank_ms'):6.1f} {st.mean(tot):6.1f} "
          f"{st.median(tot):6.1f} {sum(x <= 5 for x in tot):3d}/{len(tot)}")
print()
print("MODEL CALLS (mean per question that made the call)")
print(f"{'run':22s} {'call':7s} {'n':>3s} {'read s':>6s} {'read tok':>8s} {'write s':>7s} {'out tok':>7s} {'load s':>6s}")
for tag in sys.argv[1:]:
    tr = rows(tag)
    if not tr:
        continue
    for call in ("route", "plan", "rerank"):
        xs = [t["llm"][call] for t in tr if call in (t.get("llm") or {})]
        if xs:
            a = lambda k: st.mean(x.get(k, 0) for x in xs)
            print(f"{tag:22s} {call:7s} {len(xs):3d} {a('prompt_ms') / 1000:6.2f} {a('prompt_tokens'):8.0f} "
                  f"{a('out_ms') / 1000:7.2f} {a('out_tokens'):7.0f} {a('load_ms') / 1000:6.2f}")
PY
  echo; echo "##### WHO PLANNED, SMALL RE-RANKER ERRORS"
  python - <<'PY'
import collections, glob, json, os
for f in sorted(glob.glob("results/stage_scores_*_e9_*.json"), key=os.path.getmtime):
    tr = [r["result"]["trace"] for r in json.load(open(f))["rows"]]
    who = collections.Counter(t.get("planner") or "-" for t in tr)
    errs = sum(1 for t in tr if t.get("cross_error"))
    first = next((t["cross_error"] for t in tr if t.get("cross_error")), "")
    print(f"{os.path.basename(f)[13:]:44s} planner {dict(who)}  small re-ranker errors {errs} {first[:80]}")
PY
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = locked defaults, B = in-memory vector search (should be identical)" ${set}_v2_locked ${set}_e9_vec
    cmp "$S: A = in-memory search, B = + two-step planner"                        ${set}_e9_vec ${set}_e9_route
    cmp "$S: A = in-memory search, B = small re-ranker 50 -> 15"                  ${set}_e9_vec ${set}_e9_x15
    cmp "$S: A = in-memory search, B = small re-ranker 100 -> 30"                 ${set}_e9_vec ${set}_e9_x30n100
    cmp "$S: A = in-memory search, B = 65 films x 300 chars"                      ${set}_e9_vec ${set}_e9_n65
  done
  cmp "SPEED 20 TREC: A = locked defaults, B = in-memory search"      tune_only_time_e9_base tune_only_time_e9_vec
  cmp "SPEED 20 TREC: A = in-memory search, B = + two-step planner"   tune_only_time_e9_vec tune_only_time_e9_vecroute
  cmp "SPEED 20 TREC: A = in-memory search, B = small re-ranker 50->15" tune_only_time_e9_vec tune_only_time_e9_x15
  cmp "SPEED 20 own: A = locked defaults, B = in-memory + two-step planner" tune_only_time_e9_own_base tune_only_time_e9_own_vecroute
  echo; echo "##### ollama ps at the end"
  ollama ps
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e9.sh finished $(date)"
