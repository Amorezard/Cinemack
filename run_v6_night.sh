#!/usr/bin/env bash
# v6 night (20 -> 21 Oct): refit the box-office model with the new data, fetch YouTube data, and test the
# free data in answers. One summary at the end: results/v6_night_summary.txt
#
#   tmux new -s night
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export YOUTUBE_API_KEY=...          # optional: without it the youtube step is skipped
#   bash run_v6_night.sh                # all steps below, in order
#   bash run_v6_night.sh perf summary   # or any steps by name
#
# Steps
#   perf      performance.py fit (pf3): + trailer timing (test on 2016-2019), + pre-release Wikipedia views (own 5-fold
#             test), + OMDb / IMDb scores in the after-release model. CPU, minutes. The pf2 summary is kept as
#             results/performance_summary_pf2.txt for comparison.
#   youtube   fetch_free_data.py youtube: trailer stats + descriptions, 60 films' review videos, top comments on
#             1,500 trailers (~8,000 of the 10,000 daily quota units). Network only.
#   extraeval answers + judge on the why / compare / reviews / factual / detail questions, EXTRA_DATA=0 vs 1. GPU
#             (the site keeps working; questions wait their turn).
#   summary   results/v6_night_summary.txt
#
# Decision rule for EXTRA_DATA (written 20 Oct 2026, 23:45, before any result): keep EXTRA_DATA=1 on the site if
# the EXTRA_DATA=1 arm's judge passes >= the off arm's and its mean grounding is not lower by more than 0.05.
# Otherwise restart the site without it (the links under answers do not depend on the switch).
set -u
cd "$(dirname "$0")"
LOG=results/v6_night.log
SUM=results/v6_night_summary.txt
STEPS=${*:-perf youtube extraeval summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v6_night.sh started $(date)   steps: $STEPS"
CATS=why,compare,reviews,factual,detail
ev() { local tag=$1; shift; echo "=== eval_v3 $tag $* $(date +%H:%M)"
       env "$@" python eval_v3.py --category "$CATS" --answers --tag "$tag" | tail -4; }

step_perf() {
  [ -f results/performance_summary.txt ] && cp results/performance_summary.txt results/performance_summary_pf2.txt
  python -c "import lightgbm" 2>/dev/null || pip install -q lightgbm || echo "(lightgbm not installed: scikit-learn fallback)"
  python performance.py fit
  for t in "Oppenheimer" "Barbie" "Joker" "The Marvels" "Everything Everywhere All at Once"; do
    echo "--- $t"; python performance.py show "$t" 2>&1 | head -5; done
}
step_youtube() {
  if [ -z "${YOUTUBE_API_KEY:-}" ]; then echo "(no YOUTUBE_API_KEY in this shell: youtube skipped)"; return; fi
  python fetch_free_data.py youtube --reviews 60 --comments 1500 | tail -20
}
step_extraeval() {
  ev v6_extra_off EXTRA_DATA=0
  ev v6_extra_on EXTRA_DATA=1
}
step_summary() {
{
  echo "v6 night summary  $(date)"
  echo; echo "--- free data coverage"; python fetch_free_data.py coverage 2>&1 | tail -14
  echo; echo "--- pageviews (fetch_v6_data.py)"; python fetch_v6_data.py coverage 2>&1 | grep -i "pageviews"
  [ -f results/performance_summary.txt ] && { echo; echo "--- box-office model (pf3)"; cat results/performance_summary.txt; }
  python - <<'PY'
import glob, json, os
def ev(tag):
    fs = sorted(glob.glob(f"results/v3eval_{tag}.json"), key=os.path.getmtime)
    if not fs:
        return None
    rows = json.load(open(fs[-1]))["rows"]
    js = [r["judge"] for r in rows if r.get("judge")]
    g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float)) and j.get("grounded") > 0]
    cats = {}
    for r in rows:
        j = r.get("judge") or {}
        c = cats.setdefault(r["category"], [0, 0])
        c[0] += j.get("verdict") == "pass"
        c[1] += bool(j)
    return {"pass": sum(1 for j in js if j.get("verdict") == "pass"), "judged": len(js),
            "grounded": round(sum(g) / len(g), 2) if g else None, "cats": cats}
off, on = ev("v6_extra_off"), ev("v6_extra_on")
print("\n--- answers + judge (why, compare, reviews, factual, detail)")
print(f"  EXTRA_DATA=0  {off}")
print(f"  EXTRA_DATA=1  {on}")
if off and on:
    ok = on["pass"] >= off["pass"] and (on["grounded"] or 0) >= (off["grounded"] or 0) - 0.05
    print(f"\nEXTRA_DATA: {'KEEP ON (rule passed)' if ok else 'TURN OFF (rule failed): restart the site without EXTRA_DATA=1'}")
else:
    print("\nEXTRA_DATA: no result (an eval arm did not finish)")
PY
} > "$SUM" 2>&1
cat "$SUM"
}
for s in $STEPS; do
  if declare -F "step_$s" >/dev/null; then echo "=== step $s $(date +%H:%M)"; "step_$s"
  else echo "unknown step: $s (steps: perf youtube extraeval summary)"; fi
done
echo "=== run_v6_night.sh finished $(date)"
