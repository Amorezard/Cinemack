#!/usr/bin/env bash
# E11 (night of 1 Oct): search each remembered detail of a description on its own.
# Baseline = the locked defaults (e11_base, cached: expect 0 new calls, = e10_base).
#   dv     DETAILS=split DETAIL_LISTS=dv    each sentence/clause -> vector search over passages
#   dkw    DETAILS=split DETAIL_LISTS=dkw   each sentence/clause -> keyword search in single passages
#   split  DETAILS=split (dv + dkw)
#   llm    DETAILS=llm   (dv + dkw)         llama3.2 lists the details (one short call)
# Each also without the re-ranker (_norr, compared with e10_norr): that shows the ranking
# itself, without the re-ranker's 3-5 question churn. Those run first (minutes); the full arms
# after (~13 min each).
#
# Decision rule, set BEFORE seeing results:
#   adopt a variant only if (a) its no-re-ranker arm gains >= +3 on tune over e10_norr and does
#   not drop on check, and (b) with the re-ranker, tune + check together is not below e11_base
#   and no own question is lost. Among several, the one with the larger (a).
#
#   tmux new -s e11
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e11.sh
#   bash run_e11.sh summary          # rebuild the summary only
set -u
cd "$(dirname "$0")"
LOG=results/e11.log
SUM=results/e11_summary.txt
STEPS=${*:-prep fast full summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e11.sh started $(date)   steps: $STEPS"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_CHARS RERANK_AGREE RERANK_GAP RERANK_ENGINE RERANK_SECOND RRF_WEIGHTS RRF_K PLAN_FAST QUERY_MODEL \
      HYDE HYBRID ANSWER_WIKI ANSWER_WIKI_CHARS ANSWER_STYLE ANSWER_STRICT NUM_BATCH WIKI_PASSAGE_FAST \
      VEC_MEM PLAN_ROUTE ROUTE_MODEL RERANK_CROSS_MODEL RERANK_CROSS_KEEP RERANK_CROSS_INSTRUCT \
      RERANK_WIN WIKI_PER_FILM MB_CONFIG DETAILS DETAIL_LISTS DETAILS_MODEL DETAIL_K PFTS_DB

grep -q "DETAIL_LISTS" retrieve.py || { echo "retrieve.py is old (no DETAILS) - copy the new one up first"; exit 1; }
[ -f wiki_pfts.py ] || { echo "wiki_pfts.py missing - copy it up first"; exit 1; }
has() { [[ " $STEPS " == *" $1 "* ]]; }
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
while pgrep -f "stage_scores.py" >/dev/null; do echo "waiting for another stage_scores.py run ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings
  local tag=$1 set=$2; shift 2
  echo "=== $tag $set $* $(date +%H:%M)"
  env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4
}

if has prep; then
  echo "=== prep $(date +%H:%M)"
  python -c "import vecmem; vecmem.Store('movies.db')" 2>/dev/null && echo "vec_cache is current" \
    || python mb.py build-cache || { echo "vec_cache build FAILED"; exit 1; }
  python wiki_pfts.py || { echo "wiki_pfts.py FAILED"; exit 1; }
fi

if has fast; then
  for set in tune check; do
    run e11_base       $set
    run e11_dv_norr    $set RERANK=0 DETAILS=split DETAIL_LISTS=dv
    run e11_dkw_norr   $set RERANK=0 DETAILS=split DETAIL_LISTS=dkw
    run e11_split_norr $set RERANK=0 DETAILS=split
    run e11_llm_norr   $set RERANK=0 DETAILS=llm
  done
fi

if has full; then
  for set in tune check; do
    run e11_dv    $set DETAILS=split DETAIL_LISTS=dv
    run e11_dkw   $set DETAILS=split DETAIL_LISTS=dkw
    run e11_split $set DETAILS=split
    run e11_llm   $set DETAILS=llm
  done
fi

if has summary; then
cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E11 summary  $(date)"
  echo
  echo "##### PASSES and RANKING. d = change in passes vs the baseline (e11_base, or e10_norr for _norr arms)."
  echo "##### pool@1 / pool@10 = expected film 1st / in top 10 of the fused search, before any re-ranking;"
  echo "##### to-RR = expected film among the re-ranker's 50. Both sets, all questions with an expected film."
  python - <<'PY'
import glob, json, os
def load(tag):
    f = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(f[-1])) if f else None
def stats(d):
    rows = d["rows"]
    own = sum(r["result"]["pass"] for r in rows if "OWN" in r["group"].upper())
    trec = sum(r["result"]["pass"] for r in rows if "TREC" in r["group"].upper())
    return own, trec
def ranks(ds):
    p1 = p10 = rr = n = 0
    for d in ds:
        for r in d["rows"]:
            rk = r["result"].get("ranks") or {}
            pr = rk.get("pool")
            if pr == "n/a":
                continue
            n += 1
            p1 += isinstance(pr, int) and pr == 1
            p10 += isinstance(pr, int) and pr <= 10
            rr += isinstance(rk.get("rerank_in"), int)
    return p1, p10, rr, n
arms = [("e11_base", "e11_base"), ("e10_norr", "e10_norr"),
        ("e11_dv_norr", "e10_norr"), ("e11_dkw_norr", "e10_norr"), ("e11_split_norr", "e10_norr"),
        ("e11_llm_norr", "e10_norr"),
        ("e11_dv", "e11_base"), ("e11_dkw", "e11_base"), ("e11_split", "e11_base"), ("e11_llm", "e11_base")]
print(f"{'arm':16s} {'tune own':>8s} {'TREC':>5s} {'d':>4s}   {'chk own':>7s} {'TREC':>5s} {'d':>4s}   "
      f"{'pool@1':>6s} {'pool@10':>7s} {'to-RR':>6s} {'n':>4s}   {'new calls':>9s} {'details':>7s}")
for a, b in arms:
    cells, ds, new, nd = [], [], 0, 0
    for s in ("tune", "check"):
        d, base = load(f"{s}_{a}"), load(f"{s}_{b}")
        if not d:
            cells.append(f"{'-':>8s} {'-':>5s} {'':>4s}" if s == "tune" else f"{'-':>7s} {'-':>5s} {'':>4s}")
            continue
        ds.append(d)
        o, t = stats(d)
        bo, bt = stats(base) if base else (o, t)
        w = 8 if s == "tune" else 7
        cells.append(f"{o:{w}d} {t:5d} {o + t - bo - bt:+4d}")
        new += (d.get("cache") or {}).get("miss", 0)
        nd += sum(1 for r in d["rows"] if (r["result"].get("trace") or {}).get("details", {}).get("items"))
    p1, p10, rr, n = ranks(ds)
    print(f"{a:16s} {cells[0]}   {cells[1]}   {p1:6d} {p10:7d} {rr:6d} {n:4d}   {new:9d} {nd:7d}")
PY
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = e10_base, B = e11_base (should be identical)" ${set}_e10_base ${set}_e11_base
    cmp "$S: no re-ranker: A = e10_norr, B = details split (dv + dkw)" ${set}_e10_norr ${set}_e11_split_norr
    cmp "$S: no re-ranker: A = e10_norr, B = details llm (dv + dkw)"   ${set}_e10_norr ${set}_e11_llm_norr
    cmp "$S: A = base, B = details dv"            ${set}_e11_base ${set}_e11_dv
    cmp "$S: A = base, B = details dkw"           ${set}_e11_base ${set}_e11_dkw
    cmp "$S: A = base, B = details split (dv + dkw)" ${set}_e11_base ${set}_e11_split
    cmp "$S: A = base, B = details llm (dv + dkw)"   ${set}_e11_base ${set}_e11_llm
  done
  echo; echo "##### sample details (first 3 TREC tune questions, split and llm)"
  python - <<'PY'
import glob, json, os
for tag in ("tune_e11_split_norr", "tune_e11_llm_norr"):
    f = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    if not f:
        continue
    print(tag)
    shown = 0
    for r in json.load(open(f[-1]))["rows"]:
        d = (r["result"].get("trace") or {}).get("details") or {}
        if "TREC" in r["group"].upper() and d.get("items"):
            print(f"  {r['id']} ({d.get('how')}, {d.get('ms')} ms):")
            for x in d["items"]:
                print(f"     - {x[:110]}")
            shown += 1
            if shown == 3:
                break
PY
  echo; echo "##### ollama ps at the end"
  ollama ps
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_e11.sh finished $(date)"
