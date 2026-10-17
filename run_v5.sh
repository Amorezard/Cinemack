#!/usr/bin/env bash
# v5 (15 Oct evening -> 16 Oct morning): more money data, critic scores, the box-office model, the nickname
# test rerun, and the answer tests for the new switches.
#
#   tmux new -s v5
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   bash run_v5.sh                      # all steps below, in order
#   bash run_v5.sh money perf           # or any steps by name
#
# Steps
#   money     money_extra.py all: exchange rates + CPI (FRED), Wikidata currency codes, non-dollar budgets and
#             grosses converted at the release year's rate, critic scores from Wikipedia reception text,
#             money table rebuilt (tools.py build), today's-dollar figures. CPU + network, minutes.
#   perf      performance.py fit: the box-office model (installs lightgbm if missing). CPU, a few minutes.
#   wait      waits until the recent-films chain and the adapter rebuild have finished (GPU free)
#   aliases   nickname set rerun (sets/aliases.json years fixed): ALIASES=0 vs ALIASES=1
#   perfeval  answers + judge on the why / compare / reviews analytic questions: v5 switches off, PERF=1 only,
#             all v5 switches on (PERF, CRITIC_SCORES, MONEY_REAL)
#   nyt       next day's NYT headlines (stops by itself at the daily quota)
#   summary   results/v5_summary.txt
#
# Decision rules (written 15 Oct 2026 evening, before any result):
#   PERF           the model part in performance.py (MAE >= 5% below the genre baseline with the CI excluding 0,
#                  Spearman >= 0.30) AND perfeval: PERF=1 judge passes >= off, mean grounding not lower by > 0.05
#   CRITIC_SCORES  and MONEY_REAL (together, "all" arm): judge passes >= the PERF=1 arm, grounding not lower by > 0.05
#   ALIASES        unchanged from 15 Oct: the nickname set gains 5+ questions (tune 73/115 and check 18/33 with
#                  ALIASES=1 were already measured: -1 and 0)
#   money conversion is data, not a switch: reported (films with budget and gross before/after, a sample to check)
set -u
cd "$(dirname "$0")"
LOG=results/v5.log
SUM=results/v5_summary.txt
STEPS=${*:-money perf wait aliases perfeval nyt summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v5.sh started $(date)   steps: $STEPS"

st() { local tag=$1 set=$2; shift 2; echo "=== stage_scores $tag ($set) $* $(date +%H:%M)"
       env "$@" python stage_scores.py --questions "sets/$set.json" --tag "$tag" | tail -3; }
ev() { local tag=$1; shift; echo "=== eval_v3 $tag $* $(date +%H:%M)"
       env "$@" python eval_v3.py --category why,compare,reviews --answers --tag "$tag" | tail -4; }

step_money() {
  python money_extra.py all
}

step_perf() {
  python -c "import lightgbm" 2>/dev/null || pip install -q lightgbm || echo "(lightgbm not installed: scikit-learn fallback)"
  python performance.py fit
  for t in "Oppenheimer" "Barbie" "John Carter" "The Blair Witch Project" "Cats"; do
    echo "--- $t"; python performance.py show "$t" 2>&1 | head -8; done
}

step_wait() {
  local idle=0
  while [ $idle -lt 3 ]; do
    if pgrep -f "add_recent.py|wiki_embed.py|wiki_enrich.py|wiki_sections.py|mb.py build-cache|rebuild_adapter.sh|train_adapter.py" \
         >/dev/null; then
      idle=0; echo "waiting for the recent-films chain / adapter rebuild ... $(date +%H:%M)"; sleep 300
    else
      idle=$((idle + 1)); sleep 60
    fi
  done
  echo "GPU work finished $(date +%H:%M)"
  tail -3 results/adapter_builds.log 2>/dev/null
}

step_aliases() {
  st v5_alias_off aliases ALIASES=0
  st v5_alias_on aliases ALIASES=1
}

step_perfeval() {
  ev v5_an_off PERF=0 CRITIC_SCORES=0 MONEY_REAL=0
  ev v5_an_perf PERF=1 CRITIC_SCORES=0 MONEY_REAL=0
  ev v5_an_all PERF=1 CRITIC_SCORES=1 MONEY_REAL=1
}

step_nyt() {
  [ -n "${NYT_API_KEY:-}" ] && python news.py fetch --source nyt | tail -3 || echo "(no NYT_API_KEY in this shell: skipped)"
}

step_summary() {
{
  echo "v5 summary  $(date)"
  [ -f results/money_extra_summary.txt ] && { echo; echo "--- money + critic scores"; head -8 results/money_extra_summary.txt; }
  [ -f results/performance_summary.txt ] && { echo; echo "--- box-office model"; cat results/performance_summary.txt; }
  [ -f results/news_era_summary.txt ] && { echo; echo "--- news era x stance"; cat results/news_era_summary.txt; }
  echo; echo "--- recent films"; python add_recent.py status 2>&1 | head -8
  [ -f results/adapter_builds.log ] && { echo; echo "--- adapter builds"; tail -5 results/adapter_builds.log; }
  python - <<'PY'
import glob, json, os
def latest(pat):
    fs = sorted(glob.glob(pat), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def st(tag):
    d = latest(f"results/stage_scores_*_{tag}.json")
    return f"{sum(r['result']['pass'] for r in d['rows'])}/{len(d['rows'])}" if d else "-"
def ev(tag):
    d = latest(f"results/v3eval_{tag}.json")
    if not d:
        return None
    rows = d["rows"]
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
print(f"\n--- nicknames (years fixed): off {st('v5_alias_off')} -> on {st('v5_alias_on')} (rule: +5)")
print("\n--- answers + judge, why / compare / reviews")
res = {t: ev(t) for t in ("v5_an_off", "v5_an_perf", "v5_an_all")}
for t, r in res.items():
    print(f"  {t:11s} {r}")
def ok(a, b):
    return a and b and a["pass"] >= b["pass"] and (a["grounded"] or 0) >= (b["grounded"] or 0) - 0.05
import facts_db as F
c = F.connect(readonly=True)
row = c.execute("select value from perf_meta where key = 'result'").fetchone() if c else None
model_ok = json.loads(row[0])["rule"]["model_part_passes"] if row else False
print(f"\nPERF: model part {'PASS' if model_ok else 'FAIL'}; answers {'PASS' if ok(res['v5_an_perf'], res['v5_an_off']) else 'FAIL'}"
      f" -> {'ADOPT PERF=1' if model_ok and ok(res['v5_an_perf'], res['v5_an_off']) else 'keep PERF=0'}")
print(f"CRITIC_SCORES + MONEY_REAL: {'ADOPT' if ok(res['v5_an_all'], res['v5_an_perf']) else 'keep off'}")
PY
} > "$SUM" 2>&1
cat "$SUM"
}

for s in $STEPS; do
  if declare -F "step_$s" >/dev/null; then echo "=== step $s $(date +%H:%M)"; "step_$s"
  else echo "unknown step: $s (steps: money perf wait aliases perfeval nyt summary)"; fi
done
echo "=== run_v5.sh finished $(date)"
