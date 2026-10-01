#!/usr/bin/env bash
# E10 (night of 30 Sep - 1 Oct): what the re-ranker reads, smaller chunks, and whether the
# re-ranker can go. Baseline = the locked defaults (= e9_vec).
#
#  window   e10_base      cached rerun of the defaults: expect 0 new calls (proves the new code
#                         changes nothing while its switches are off)
#           e10_win_kw    RERANK_WIN=kw   re-ranker reads the 400 characters of the matched
#                         passage that share the most words with the question, not the first 400
#           e10_win_emb   RERANK_WIN=emb  ... the 400 characters around the sentence closest to
#                         the question vector (one extra batched embedding call per re-rank)
#           e10_norr      RERANK=0        no re-ranker at all (fused search order)
#  build    plot_s chunks (~80 words) from the same plot text -> embed -> rebuild vec_cache
#  small    e10_s         WIKI_KINDS=plot_s,lead   small chunks instead of the 180-word ones
#           e10_s_kw / e10_s_emb   + RERANK_WIN
#           e10_both      WIKI_KINDS=plot,plot_s,lead + the better window from tonight
#           e10_s_norr / e10_both_norr   the same with no re-ranker
#           (small-chunk arms fetch WIKI_PER_FILM=8 passages per film wanted, not 4, since a
#            film has about twice as many small chunks)
#  summary  results/e10_summary.txt
#
# Decision rules, set BEFORE seeing results (noise is 1-2 questions):
#   window / small chunks: adopt only if tune gains >= +3 questions in total over e10_base,
#     own questions lose nothing on tune or check, and check is not lower.
#   drop the re-ranker (RERANK=0): only if within 1 own and 2 TREC questions of the
#     re-ranked arm on tune, and check is within 2 in total (v2 plan rule).
#
#   tmux new -s e10
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_e10.sh
#   bash run_e10.sh summary            # rebuild the summary from saved results
#   bash run_e10.sh small summary      # run only some steps (window build small summary)
set -u
cd "$(dirname "$0")"
LOG=results/e10.log
SUM=results/e10_summary.txt
STEPS=${*:-window build small summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_e10.sh started $(date)   steps: $STEPS"
unset WIKI WIKI_KW WIKI_KINDS WIKI_Q WIKI_QSTYLES WIKI_EXACT RERANK RERANK_TEXT RERANK_N RERANK_WIKI_CHARS \
      RERANK_CHARS RERANK_AGREE RERANK_GAP RERANK_ENGINE RERANK_SECOND RRF_WEIGHTS RRF_K PLAN_FAST QUERY_MODEL \
      HYDE HYBRID ANSWER_WIKI ANSWER_WIKI_CHARS ANSWER_STYLE ANSWER_STRICT NUM_BATCH WIKI_PASSAGE_FAST \
      VEC_MEM PLAN_ROUTE ROUTE_MODEL RERANK_CROSS_MODEL RERANK_CROSS_KEEP RERANK_CROSS_INSTRUCT \
      RERANK_WIN WIKI_PER_FILM MB_CONFIG

grep -q "RERANK_WIN" retrieve.py || { echo "retrieve.py is old (no RERANK_WIN) - copy the new one up first"; exit 1; }
[ -f wiki_small.py ] || { echo "wiki_small.py missing - copy it up first"; exit 1; }
has() { [[ " $STEPS " == *" $1 "* ]]; }
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }

while pgrep -f "stage_scores.py" >/dev/null; do echo "waiting for another stage_scores.py run ... $(date +%H:%M)"; sleep 120; done
run() {  # $1 = tag, $2 = set, rest = VAR=value settings
  local tag=$1 set=$2; shift 2
  echo "=== $tag $set $* $(date +%H:%M)"
  env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4
}
passes() {  # total passes in a saved run
  python -c "import json,sys; print(sum(r['result']['pass'] for r in json.load(open(sys.argv[1]))['rows']))" "$(latest "$1")" 2>/dev/null || echo 0
}

SMALL="WIKI_KINDS=plot_s,lead WIKI_PER_FILM=8"
BOTH="WIKI_KINDS=plot,plot_s,lead WIKI_PER_FILM=8"

if has window; then
  for set in tune check; do
    run e10_base    $set
    run e10_win_kw  $set RERANK_WIN=kw
    run e10_win_emb $set RERANK_WIN=emb
    run e10_norr    $set RERANK=0
  done
fi

# the better window (tune + check passes; a tie goes to kw, which needs no extra call)
KW=$(( $(passes tune_e10_win_kw) + $(passes check_e10_win_kw) ))
EMB=$(( $(passes tune_e10_win_emb) + $(passes check_e10_win_emb) ))
W=kw; [ "$EMB" -gt "$KW" ] && W=emb
echo "=== window passes: kw $KW, emb $EMB -> e10_both uses RERANK_WIN=$W"

if has build; then
  echo "=== build small chunks $(date +%H:%M)"
  SIZE=$(stat -c %s wiki.db); AVAIL=$(df --output=avail -B1 . | tail -1)
  if [ ! -f wiki.db.bak-e10 ] && [ "$AVAIL" -gt $(( SIZE + 3000000000 )) ]; then
    cp wiki.db wiki.db.bak-e10 && echo "backup: wiki.db.bak-e10"
  else
    echo "no backup copy (exists already, or not enough disk); undo is: python wiki_small.py --drop"
  fi
  if [ "$(python -c "import sqlite3; print(sqlite3.connect('wiki.db').execute(\"select count(*) from chunks where kind='plot_s'\").fetchone()[0])")" = 0 ]; then
    python wiki_small.py || { echo "wiki_small.py FAILED"; exit 1; }
  else
    echo "plot_s chunks exist already; embedding any that are missing"
  fi
  python wiki_embed.py --wiki-db wiki.db --kinds plot_s --batch 64 || { echo "wiki_embed FAILED"; exit 1; }
  python mb.py build-cache || { echo "vec_cache build FAILED"; exit 1; }
fi

if has small; then
  for set in tune check; do
    run e10_s         $set $SMALL
    run e10_s_kw      $set $SMALL RERANK_WIN=kw
    run e10_s_emb     $set $SMALL RERANK_WIN=emb
    run e10_both      $set $BOTH RERANK_WIN=$W
    run e10_s_norr    $set $SMALL RERANK=0
    run e10_both_norr $set $BOTH RERANK=0
  done
fi

if has summary; then
cmp() { echo; echo "##### $1"; python compare_stage.py "$(latest $2)" "$(latest $3)"; }
{
  echo "E10 summary  $(date)"
  echo; echo "##### PASSES (own / TREC), new model calls, re-ranker skipped, windows changed"
  python - <<'PY'
import glob, json, os
arms = ["e9_vec", "e10_base", "e10_win_kw", "e10_win_emb", "e10_norr",
        "e10_s", "e10_s_kw", "e10_s_emb", "e10_both", "e10_s_norr", "e10_both_norr"]
def load(tag):
    f = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(f[-1])) if f else None
base = {s: load(f"{s}_e10_base") for s in ("tune", "check")}
def score(d):
    own = sum(r["result"]["pass"] for r in d["rows"] if "OWN" in r["group"].upper())
    trec = sum(r["result"]["pass"] for r in d["rows"] if "TREC" in r["group"].upper())
    return own, trec
print(f"{'arm':16s} {'tune own':>8s} {'TREC':>5s} {'total':>6s} {'d':>4s}   {'chk own':>8s} {'TREC':>5s} {'total':>6s} {'d':>4s}"
      f"   {'new calls':>9s} {'skipped':>8s} {'windows':>8s}")
for a in arms:
    cells, new, skip, win = [], 0, 0, 0
    for s in ("tune", "check"):
        d = load(f"{s}_{a}")
        if not d:
            cells.append(f"{'-':>8s} {'-':>5s} {'-':>6s} {'':>4s}"); continue
        o, t = score(d)
        b = score(base[s]) if base[s] else (o, t)
        cells.append(f"{o:8d} {t:5d} {o + t:6d} {o + t - sum(b):+4d}")
        new += (d.get("cache") or {}).get("miss", 0)
        tr = [r["result"].get("trace") or {} for r in d["rows"]]
        skip += sum(1 for x in tr if x.get("rerank_skipped"))
        win += sum(x.get("rerank_win", 0) for x in tr)
    print(f"{a:16s} {cells[0]}   {cells[1]}   {new:9d} {skip:8d} {win:8d}")
PY
  for set in tune check; do
    S=${set^^}
    cmp "$S: A = locked defaults (e9_vec), B = e10_base (should be identical)" ${set}_e9_vec ${set}_e10_base
    cmp "$S: A = base, B = window kw"                         ${set}_e10_base ${set}_e10_win_kw
    cmp "$S: A = base, B = window emb"                        ${set}_e10_base ${set}_e10_win_emb
    cmp "$S: A = base, B = no re-ranker"                      ${set}_e10_base ${set}_e10_norr
    cmp "$S: A = base, B = small chunks"                      ${set}_e10_base ${set}_e10_s
    cmp "$S: A = base, B = small chunks + window kw"          ${set}_e10_base ${set}_e10_s_kw
    cmp "$S: A = base, B = small chunks + window emb"         ${set}_e10_base ${set}_e10_s_emb
    cmp "$S: A = base, B = both chunk sizes + window $W"      ${set}_e10_base ${set}_e10_both
    cmp "$S: A = small chunks, B = small chunks, no re-ranker" ${set}_e10_s ${set}_e10_s_norr
    cmp "$S: A = both sizes, B = both sizes, no re-ranker"    ${set}_e10_both ${set}_e10_both_norr
  done
  echo; echo "##### plot_s chunks"
  python -c "import sqlite3; [print(*r) for r in sqlite3.connect('wiki.db').execute('select kind, count(*), round(avg(n_words)) from chunks group by kind')]"
  echo; echo "##### ollama ps at the end"
  ollama ps
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_e10.sh finished $(date)"
