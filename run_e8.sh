#!/usr/bin/env bash
# E8 (v2 plan, night of 29-30 Sep, last tuning night): speed of the two model calls.
# Each run is the current defaults plus one change:
#   e8_nb        NUM_BATCH=$NB           Ollama reads the prompt in bigger steps (default 512)
#   e8_nosecond  RERANK_SECOND=0         no second re-rank call when the first picks nothing
#   e8_qsmall    QUERY_MODEL=llama3.2    planner on the 2 GB model (watch for model swapping)
#   e8_pslow     WIKI_PASSAGE_FAST=0     the old per-passage lookups (timing only; the new default
#                picks the same passages, checked this morning with a cached tune run)
#   judge        ANSWER_STRICT=0 (new default): answers without the anti-hallucination
#                instructions, graded by judge.py and compared with judge-v2_pf (the old prompt,
#                same search settings), plus answer length and how many answers hedge
# Timing first (no cache, 20 TREC + 20 own questions, against a fresh baseline run tonight
# so server load is the same), then accuracy on tune + check (cache on).
# NEW in the traces: trace["llm"] = where each model call's time goes (reading the prompt
# vs writing, tokens, the re-ranker's listing build) - the second table in the summary.
#   NB=2048 bash run_e8.sh   to test another batch size (default 1024: pick it from nb_test.py)
#   bash run_e8.sh --summary rebuilds the summary from the saved result files
#   tmux new -s e8   ->   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e8.sh
set -u
cd "$(dirname "$0")"
NB=${NB:-1024}
LOG=results/e8.log
SUM=results/e8_summary.txt
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e8.sh started $(date)   NB=$NB"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_CHARS RERANK_AGREE RERANK_GAP RERANK_ENGINE RERANK_SECOND RRF_WEIGHTS RRF_K PLAN_FAST QUERY_MODEL \
      HYDE HYBRID ANSWER_WIKI ANSWER_WIKI_CHARS ANSWER_STYLE ANSWER_STRICT NUM_BATCH WIKI_PASSAGE_FAST

grep -q "_note_llm" retrieve.py || { echo "retrieve.py is old (no NUM_BATCH / timing notes) - copy the new one up first"; exit 1; }

if [ "${1:-}" != "--summary" ]; then
while pgrep -f "stage_scores.py" >/dev/null; do echo "waiting for another stage_scores.py run ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings, then extra args after --
  local tag=$1 set=$2; shift 2
  local envs=() extra=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift && extra=("$@")
  echo "=== $tag $set ${envs[*]} ${extra[*]} $(date +%H:%M)"
  env "${envs[@]}" python stage_scores.py --questions sets/$set.json --tag $tag "${extra[@]}" | tail -30
}
T="-- --no-cache --group TREC --limit 20"
O="-- --no-cache --group OWN --limit 20"
# timing first, so the key speed numbers exist even if the night is cut short
run time_e8_base      tune QUERY_MODEL=movie-buff $T
run time_e8_nb        tune NUM_BATCH=$NB $T
run time_e8_pslow     tune WIKI_PASSAGE_FAST=0 $T
run time_e8_nosecond  tune RERANK_SECOND=0 $T
run time_e8_qsmall    tune QUERY_MODEL=llama3.2 $T
echo "--- ollama ps right after the llama3.2 planner run:"; ollama ps
run time_e8_own_base  tune QUERY_MODEL=movie-buff $O
run time_e8_own_nb    tune NUM_BATCH=$NB $O
for set in tune check; do
  run e8_nosecond $set RERANK_SECOND=0
  run e8_qsmall   $set QUERY_MODEL=llama3.2
  run e8_nb       $set NUM_BATCH=$NB          # every call is new: the cache key includes num_batch
done
echo "=== judge ANSWER_STRICT=0 $(date +%H:%M)"
ANSWER_STRICT=0 python -u judge.py score --label v2_strict0 | tail -8
fi

cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E8 summary  $(date)   NB=$NB"
  echo; echo "##### SPEED BUDGET (no cache): mean seconds per stage, and share of questions done in <= 5 s"
  python - time_e8_base time_e8_nb time_e8_pslow time_e8_nosecond time_e8_qsmall time_e8_own_base time_e8_own_nb <<'PY'
import glob, json, os, statistics as st, sys
def rows(tag):
    files = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return [r["result"]["trace"] for r in json.load(open(files[-1]))["rows"]] if files else None
print(f"{'run':22s} {'n':>3s} {'plan':>5s} {'search':>6s} {'rerank':>6s} {'total':>6s} {'median':>6s} {'<=5s':>5s} {'2nd look':>8s}")
for tag in sys.argv[1:]:
    tr = rows(tag)
    if not tr:
        print(f"{tag:22s} (missing)"); continue
    m = lambda k: st.mean(t.get(k, 0) for t in tr) / 1000
    tot = [t.get("total_ms", 0) / 1000 for t in tr]
    print(f"{tag:22s} {len(tr):3d} {m('plan_ms'):5.1f} {m('search_ms'):6.1f} {m('rerank_ms'):6.1f} {st.mean(tot):6.1f} "
          f"{st.median(tot):6.1f} {sum(x <= 5 for x in tot):3d}/{len(tot)} {sum(1 for t in tr if t.get('second_look')):5d}")
print()
print("WHERE THE MODEL TIME GOES (mean per question that made the call; Ollama's own counters)")
print(f"{'run':22s} {'call':7s} {'n':>3s} {'calls':>5s} {'build s':>7s} {'read s':>6s} {'read tok':>8s} {'tok/s':>6s} "
      f"{'write s':>7s} {'out tok':>7s} {'load s':>6s}")
for tag in sys.argv[1:]:
    tr = rows(tag)
    if not tr:
        continue
    for call in ("plan", "rerank"):
        xs = [t["llm"][call] for t in tr if call in (t.get("llm") or {})]
        if not xs:
            continue
        a = lambda k: st.mean(x.get(k, 0) for x in xs)
        rate = a("prompt_tokens") / (a("prompt_ms") / 1000) if a("prompt_ms") else 0
        print(f"{tag:22s} {call:7s} {len(xs):3d} {a('calls'):5.2f} {a('build_ms') / 1000:7.2f} {a('prompt_ms') / 1000:6.2f} "
              f"{a('prompt_tokens'):8.0f} {rate:6.0f} {a('out_ms') / 1000:7.2f} {a('out_tokens'):7.0f} {a('load_ms') / 1000:6.2f}")
print("(load s > ~1 on the llama3.2 run = the models are swapping in and out of the GPU)")
print()
print("PASSAGE PICKING (all questions): films answered from the search results vs looked up, and time")
for tag in sys.argv[1:]:
    tr = rows(tag)
    if not tr:
        continue
    ps = [t["passages"] for t in tr if t.get("passages")]
    if ps:
        print(f"{tag:22s} from search {sum(p['fast'] for p in ps):5d}  looked up {sum(p['lookup'] for p in ps):5d}"
              f"  mean {st.mean(p['ms'] for p in ps) / 1000:5.2f} s per question")
PY
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = current defaults, B = NUM_BATCH=$NB"               ${set}_e6_planfast ${set}_e8_nb
    cmp "$S: A = current defaults, B = no second re-rank call"      ${set}_e6_planfast ${set}_e8_nosecond
    cmp "$S: A = current defaults, B = planner on llama3.2"         ${set}_e6_planfast ${set}_e8_qsmall
  done
  cmp "SPEED 20 TREC: A = tonight's defaults, B = NUM_BATCH=$NB"     tune_only_time_e8_base tune_only_time_e8_nb
  cmp "SPEED 20 TREC: A = old passage lookups, B = tonight's defaults" tune_only_time_e8_pslow tune_only_time_e8_base
  cmp "SPEED 20 TREC: A = tonight's defaults, B = no second call"    tune_only_time_e8_base tune_only_time_e8_nosecond
  cmp "SPEED 20 TREC: A = tonight's defaults, B = llama3.2 planner"  tune_only_time_e8_base tune_only_time_e8_qsmall
  cmp "SPEED 20 own: A = tonight's defaults, B = NUM_BATCH=$NB"      tune_only_time_e8_own_base tune_only_time_e8_own_nb
  echo; echo "##### ANSWER PROMPT (judge.py): A = old strict prompt (E6 defaults), B = ANSWER_STRICT=0"
  echo "(the judge's grounded/honest scores reward sticking to the context, so they are expected to drop a little)"
  python compare_night.py --judge results/judge-v2_pf.json results/judge-v2_strict0.json
  python - results/judge-v2_pf.json results/judge-v2_strict0.json <<'PY'
import json, re, statistics as st, sys
H = re.compile(r"\b(not sure|unsure|i don'?t have|i do not have|not in (the|my) (information|database|data)|"
               r"verify|double[- ]check|can'?t confirm|cannot confirm|may be wrong|might be wrong|"
               r"not (listed|mentioned) (above|here))\b", re.I)
for p in sys.argv[1:]:
    try:
        rows = json.load(open(p))
    except OSError:
        print(f"{p}: missing"); continue
    w = [len(r["answer"].split()) for r in rows]
    print(f"{p:36s} answer words: mean {st.mean(w):5.1f}, median {st.median(w):5.1f}   "
          f"answers with a hedge/'I don't have' phrase: {sum(1 for r in rows if H.search(r['answer']))}/{len(rows)}")
print("(honesty questions about films not in the database are SUPPOSED to say 'I don't have that film')")
PY
  echo; echo "##### ollama ps at the end"
  ollama ps
} > "$SUM" 2>&1
cat "$SUM"
echo "=== run_e8.sh finished $(date)"
