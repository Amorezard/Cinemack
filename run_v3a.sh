#!/usr/bin/env bash
# v3, night 1 (Sun 4 Oct -> Mon 5 Oct). Three things in parallel lanes:
#
#   NETWORK  (no GPU)  TMDB: every catalogue film's details/credits/keywords/reviews, then now
#                      playing / upcoming / films since the Wikipedia snapshot -> facts.db
#                      Wikidata: awards, nominations, box office, cost -> facts.db
#   CPU                Wikipedia sections (Production, Reception, Box office ...) from the corpus,
#                      infobox budget / box office -> facts.db
#   GPU (Ollama)       1. evaluation: conversation memory (CONV=1) vs v2 (CONV=0)
#                      2. timing runs for the write-up (no cache)
#                      3. embedding the new section passages (~1 h; last, so it can't slow the eval)
#
# Decision rule for CONV, set before any result (written 4 Oct):
#   adopt CONV=1 if (a) multi-turn passes >= +5 over CONV=0, (b) no topic-change case
#   (category "switch") that passes with CONV=0 fails with CONV=1, and (c) tune + check are
#   identical with CONV=0 and CONV=1 on single-turn questions (first questions use the v2
#   prompt unchanged, so this should show 0 new model calls on them).
#
#   tmux new -s v3a
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export TMDB_API_KEY=...            # same key as fetch_credits.py
#   bash run_v3a.sh                    # everything
#   bash run_v3a.sh summary            # rebuild the summary only
set -u
cd "$(dirname "$0")"
LOG=results/v3a.log
SUM=results/v3a_summary.txt
STEPS=${*:-check net cpu eval timing embed summary}
CORPUS=${CORPUS:-$HOME/movie-buff/data/trec_tot/corpus.jsonl.zip}
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v3a.sh started $(date)   steps: $STEPS"
has() { [[ " $STEPS " == *" $1 "* ]]; }
latest() { ls -t results/stage_scores_*_"$1".json 2>/dev/null | head -1; }
run() {  # $1 = tag, $2 = set, rest = VAR=value settings
  local tag=$1 set=$2; shift 2
  echo "=== $tag $set $* $(date +%H:%M)"
  env "$@" python stage_scores.py --questions sets/$set.json --tag $tag | tail -4
}

if has check; then
  for f in conv.py facts_db.py fetch_tmdb.py fetch_wikidata.py wiki_sections.py sets/multiturn.json; do
    [ -f "$f" ] || { echo "MISSING $f - copy it up first"; exit 1; }
  done
  grep -q "PLAN_SYSTEM_CONV" retrieve.py || { echo "retrieve.py is old (no CONV) - copy the new one up"; exit 1; }
  grep -q "expect_absent" stage_scores.py || { echo "stage_scores.py is old - copy the new one up"; exit 1; }
  python -c "import conv, facts_db, retrieve" || { echo "import FAILED"; exit 1; }
  [ -f "$CORPUS" ] || { echo "corpus not at $CORPUS - set CORPUS=..."; exit 1; }
  echo "files ok; git: $(git rev-parse --abbrev-ref HEAD) $(git describe --always --dirty --tags)"
fi

# ---------------------------------------------------------------- NETWORK lane (background)
if has net; then
  if [ -z "${TMDB_API_KEY:-}" ]; then
    echo "!!! TMDB_API_KEY not set - skipping the TMDB fetch (export it and run: bash run_v3a.sh net)"
  else
    ( python fetch_tmdb.py catalog --limit 20 > results/v3a_tmdb_smoke.log 2>&1 \
        && python fetch_tmdb.py catalog > results/v3a_tmdb.log 2>&1 \
        && python fetch_tmdb.py current >> results/v3a_tmdb.log 2>&1 \
        && echo "TMDB done $(date +%H:%M)" || echo "!!! TMDB FAILED - see results/v3a_tmdb*.log" ) &
  fi
  ( python fetch_wikidata.py > results/v3a_wikidata.log 2>&1 \
      && echo "Wikidata done $(date +%H:%M)" || echo "!!! Wikidata FAILED - see results/v3a_wikidata.log" ) &
fi

# ---------------------------------------------------------------- CPU lane (background)
if has cpu; then
  ( python wiki_sections.py build --corpus "$CORPUS" > results/v3a_sections.log 2>&1 \
      && python wiki_sections.py infobox >> results/v3a_sections.log 2>&1 \
      && echo "sections + infobox done $(date +%H:%M)" || echo "!!! sections FAILED - see results/v3a_sections.log" ) &
fi

# ---------------------------------------------------------------- GPU lane (foreground)
while pgrep -f "stage_scores.py" >/dev/null; do echo "waiting for another stage_scores.py run ... $(date +%H:%M)"; sleep 120; done
if has eval; then
  run v3a_mt_v2    multiturn CONV=0
  run v3a_mt_conv  multiturn CONV=1
  run v3a_tune_v2  tune      CONV=0
  run v3a_tune_conv tune     CONV=1
  run v3a_check_v2 check     CONV=0
  run v3a_check_conv check   CONV=1
fi

if has timing; then
  # The write-up's timing rows (status doc "Next 2"), plus what a follow-up costs with CONV=1.
  echo "=== timing $(date +%H:%M)"
  CONV=0 python stage_scores.py --questions sets/tune.json --group TREC --limit 20 --no-cache --tag time_lock_trec | tail -3
  CONV=0 RERANK=0 python stage_scores.py --questions sets/tune.json --group TREC --limit 20 --no-cache --tag time_norr_trec | tail -3
  CONV=1 python stage_scores.py --questions sets/multiturn.json --no-cache --tag time_mt_conv | tail -3
  CONV=0 python stage_scores.py --questions sets/multiturn.json --no-cache --tag time_mt_v2 | tail -3
fi

if has embed; then
  for i in $(seq 1 90); do                      # the sections must be built first
    python -c "import facts_db as F, sys; c=F.connect(readonly=True); sys.exit(0 if c and F.get_meta(c,'infobox_parsed_at') else 1)" && break
    echo "waiting for the sections build ... $(date +%H:%M)"; sleep 60
  done
  echo "=== embedding section passages $(date +%H:%M)"
  python wiki_sections.py embed | tail -5
fi

wait
echo "=== background jobs finished $(date +%H:%M)"

# ---------------------------------------------------------------- summary
if has summary; then
{
  echo "v3a summary  $(date)"
  echo
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def rows(tag):
    d = latest(tag)
    return {r["id"]: r for r in d["rows"]} if d else {}
def line(tag):
    d = latest(tag)
    if not d:
        return f"{tag:16s} (not run)"
    rs = d["rows"]
    p = sum(r["result"]["pass"] for r in rs)
    c = d.get("cache") or {}
    return f"{tag:16s} {p:3d}/{len(rs):3d}   new model calls {c.get('miss', '-')}"
print("PASSES")
for t in ("v3a_mt_v2", "v3a_mt_conv", "v3a_tune_v2", "v3a_tune_conv", "v3a_check_v2", "v3a_check_conv"):
    print("  " + line(t))
a, b = rows("v3a_mt_v2"), rows("v3a_mt_conv")
if a and b:
    print("\nMULTI-TURN, case by case (v2 -> conv)")
    for i in sorted(a):
        ra, rb = a[i]["result"], b.get(i, {}).get("result", {})
        mark = "  " if ra["pass"] == rb.get("pass") else ("++" if rb.get("pass") else "--")
        print(f"  {mark} {i} {b.get(i, a[i]).get('question', '')[:60]:60s} "
              f"{'P' if ra['pass'] else '.'}->{'P' if rb.get('pass') else '.'}  got: {', '.join(rb.get('hits', []))[:60]}")
        if not rb.get("pass"):
            tr = rb.get("trace", {}).get("plan") or {}
            print(f"       plan: {json.dumps(tr)[:300]}")
    cats = {}
    for i in a:
        c = json.load(open("sets/multiturn.json"))
        break
    cat = {q["id"]: q["category"] for q in c["questions"]}
    agg = {}
    for i in a:
        k = cat.get(i, "?")
        s = agg.setdefault(k, [0, 0, 0])
        s[0] += 1; s[1] += a[i]["result"]["pass"]; s[2] += b.get(i, {}).get("result", {}).get("pass", False)
    print("\n  by category: (cases, v2, conv)")
    for k, s in sorted(agg.items()):
        print(f"    {k:12s} {s}")
for x, y in (("v3a_tune_v2", "v3a_tune_conv"), ("v3a_check_v2", "v3a_check_conv")):
    a, b = rows(x), rows(y)
    if a and b:
        diff = [i for i in a if a[i]["result"]["pass"] != b.get(i, {}).get("result", {}).get("pass")]
        print(f"\n{x} vs {y}: {len(diff)} questions differ {diff[:20]}")
print("\nTIMING (no cache)")
for t in ("time_lock_trec", "time_norr_trec", "time_mt_v2", "time_mt_conv"):
    d = latest(t)
    if not d:
        print(f"  {t}: not run"); continue
    tr = [r["result"]["trace"] for r in d["rows"]]
    def avg(k):
        v = [x.get(k) for x in tr if isinstance(x.get(k), (int, float))]
        return sum(v) / len(v) / 1000 if v else float("nan")
    rr = [x.get("rerank_ms") for x in tr if x.get("rerank_ms")]
    print(f"  {t:16s} n={len(tr):2d} total {avg('total_ms'):.1f}s  plan {avg('plan_ms'):.1f}s  "
          f"search {avg('search_ms'):.2f}s  rerank {sum(rr) / len(rr) / 1000 if rr else 0:.1f}s on {len(rr)}")
PY
  echo
  echo "DATA"
  python facts_db.py status
  tail -3 results/v3a_tmdb.log 2>/dev/null
  tail -2 results/v3a_wikidata.log 2>/dev/null
  tail -12 results/v3a_sections.log 2>/dev/null
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_v3a.sh finished $(date)"
