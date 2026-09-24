#!/usr/bin/env bash
# Extra steps. run_overnight.sh runs this after its main steps if the file is
# there; it can also be run on its own:  bash run_extra.sh
#   1. change 18: MovieLens neighbours + the blind recommendation test
#   2. change 23 (no training): the planner on a small model (llama3.2, 3B)
#      vs. the 27B - accuracy, agreement and planning time
# Writes results/extra_summary.txt (added to overnight_summary.txt).

cd "${RAG:-$HOME/movie-buff/rag}" || exit 1
VENV="${VENV:-$HOME/venvs/movie-rag}"
[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
OUT=results/extra_summary.txt
: > "$OUT"

echo "--- movielens $(date +%H:%M)"
python build_movielens.py > results/night_movielens.txt 2>&1
echo "exit $?"
{ echo "=== MOVIELENS (change 18)"; tail -5 results/night_movielens.txt; } >> "$OUT"
python -u compare_recs.py > results/night_recs.txt 2>&1
echo "exit $?"
{ echo; echo "Blind test ready: cat results/recs_blind.txt"; echo; } >> "$OUT"

SMALL="${SMALL_MODEL:-llama3.2:latest}"
if ollama show "$SMALL" > /dev/null 2>&1; then
  echo "--- small planner ($SMALL) $(date +%H:%M)"
  QUERY_MODEL="$SMALL" python -u stage_scores.py --questions sets/tune.json \
      --tag night_smallplan > results/night_smallplan.txt 2>&1
  echo "exit $?"
  {
    echo "=== SMALL PLANNER (change 23): 27B plan vs $SMALL plan, everything else the same"
    python compare_night.py results/stage_scores_*_night_all.json results/stage_scores_*_night_smallplan.json
    python - <<'EOF'
import glob, json
a = json.load(open(glob.glob("results/stage_scores_*_night_all.json")[0]))
b = json.load(open(glob.glob("results/stage_scores_*_night_smallplan.json")[0]))
pa = {r["id"]: r["result"]["trace"] for r in a["rows"]}
pb = {r["id"]: r["result"]["trace"] for r in b["rows"]}
same = sum(1 for i in pa if i in pb and pa[i].get("path") == pb[i].get("path"))
ms = lambda d: sum(t.get("plan_ms", 0) for t in d.values()) / max(1, len(d))
print(f"Same question type as the 27B: {same}/{len(pa)}.  "
      f"Planning time: 27B {ms(pa):.0f} ms, small {ms(pb):.0f} ms (both uncached).")
EOF
  } >> "$OUT" 2>&1
else
  echo "=== SMALL PLANNER: $SMALL not in Ollama, skipped" >> "$OUT"
fi
echo "--- extras done $(date +%H:%M)"
