#!/usr/bin/env bash
# v6 day (21 Oct): the tests and runs for the morning batch. One summary: results/v6_day_summary.txt
#
#   tmux new -s day
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export GUARDIAN_API_KEY=... OMDB_API_KEY=...      # optional: the data step skips what has no key
#   bash run_v6_day.sh                                # all steps below, in order (~5-7 h; GPU from "dates" on)
#   bash run_v6_day.sh data summary                   # or any steps by name
#
# Steps
#   data      network only: Netflix reports (TidyTuesday CSV), Guardian re-fetch with keyword tags (better matching
#             of early-2000s reviews; --body keeps the text), OMDb's next 950 (only after 8 pm ET, when the daily
#             limit resets; it stops by itself before that), coverage
#   wait      waits until run_v6_night.sh has finished (it uses the GPU)
#   money     money_extra.py convert, money, real, report (the 16 Oct fix: numbers inside HTML comments skipped). CPU
#   dates     DATE_WORDS: the phrase table (no model), then retrieval on tune and check, DATE_WORDS=0 vs 1
#   critics   the open v5 question: CRITIC_SCORES + MONEY_REAL were adopted from a test with PERF=1 only; answers + judge
#             on why / compare / reviews with PERF=0, both off vs both on
#   news      news at release with the finished NYT headlines: news_film cleared, news.py embed + films + analyze (GPU)
#   perf      performance.py fit again, after money and news (CPU, minutes)
#   recent    add_recent.py eval --n 100: are the 2,181 added recent films found from their own descriptions?
#   confidence  confidence.py fit (CPU, seconds): calibrated "is the top film right?" vs the fixed cut-offs
#   aspects   reviews_analyze.py --kinds audience,critic,pro,ytc on the 1,500 most-voted films: finishes the TMDB and
#             Wikipedia analysis and adds Guardian / NYT reviews and trailer comments (llama3.2; 3 hours at most, resumable)
#   aliases   (not in the default list: solved 16 Oct, off 12/15 -> on 15/15) the nickname set again
#   summary   results/v6_day_summary.txt
#
# Decision rules (written 21 Oct 2026, 01:30, before any result):
#   ALIASES     unchanged from 14 Oct: on if the nickname set gains 5+ questions (tune -1 / check 0 were measured on 15 Oct)
#   DATE_WORDS  on if tune and check each lose at most 1 question AND the phrase table reads every case as intended
#   DISCOVER    (written 21 Oct 11:50) on if tune and check each lose at most 1 question against the off arms; the
#               six example requests are shown for the write-up, not graded
#   CRITIC_SCORES + MONEY_REAL with PERF=0: stay on if the "on" arm's judge passes >= the "off" arm's and grounding is
#               not lower by more than 0.05; otherwise turn both off
#   recent films (rule of 14 Oct): the added films' own descriptions found in the top 3 at least half as often as the
#               catalogue films' (the tune/check half of that rule needs the films removed, which is not done here)
set -u
cd "$(dirname "$0")"
LOG=results/v6_day.log
SUM=results/v6_day_summary.txt
STEPS=${*:-data money wait dates critics news perf recent confidence aspects summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v6_day.sh started $(date)   steps: $STEPS"
st() { local tag=$1 set=$2; shift 2; echo "=== stage_scores $tag ($set) $* $(date +%H:%M)"
       if [ ! -f "sets/$set.json" ]; then echo "(sets/$set.json not found: skipped)"; return; fi
       env "$@" python stage_scores.py --questions "sets/$set.json" --tag "$tag" | tail -3; }

step_data() {
  python fetch_free_data.py netflix | tail -3
  if [ -n "${GUARDIAN_API_KEY:-}" ]; then python fetch_free_data.py guardian --again --body | tail -3
  else echo "(no GUARDIAN_API_KEY: Guardian re-fetch skipped)"; fi
  if [ -n "${OMDB_API_KEY:-}" ]; then python fetch_free_data.py omdb | tail -4
  else echo "(no OMDB_API_KEY: OMDb skipped)"; fi
  python fetch_free_data.py coverage
}
step_wait() {
  local idle=0
  while [ $idle -lt 3 ]; do
    if pgrep -f "run_v6_night.sh|eval_v3.py|performance.py fit" >/dev/null; then
      idle=0; echo "waiting for run_v6_night.sh ... $(date +%H:%M)"; sleep 300
    else
      idle=$((idle + 1)); sleep 60
    fi
  done
  echo "night run finished $(date +%H:%M)"
}
step_aliases() {
  python diag_aliases.py 2>&1 | tail -25
  st v6_alias_off aliases ALIASES=0
  st v6_alias_on aliases ALIASES=1
}
step_dates() {
  DATE_WORDS=1 python - <<'PY'
import retrieve as R
cases = [("recommend some late 90s thrillers", (1996, 1999)), ("give me something from the seventies", (1970, 1979)),
         ("good sci-fi from the last 5 years", (R.CURRENT_YEAR - 5, R.CURRENT_YEAR)),
         ("recommend recent horror", (R.CURRENT_YEAR - 5, R.CURRENT_YEAR)), ("best films of last year", (R.CURRENT_YEAR - 1,) * 2),
         ("suggest old movies with great dialogue", (1900, 1979)), ("recommend classic hollywood musicals", (1900, 1969)),
         ("early 2000s rom-coms please recommend", (2000, 2003)), ("horror after 2015", (2016, R.CURRENT_YEAR)),
         ("movies from the 80s", (1980, 1989)), ("i saw it recently, a kid finds a ghost in a library", None),
         ("a woman in her early 20s moves to paris and falls in love", None), ("a man in his forties loses his job", None),
         ("the movie where a girl travels back to the nineties", (1990, 1999))]
ok = 0
print("DATE_WORDS phrase table (question -> year filter; expected)")
for q, want in cases:
    f = R.parse_filters(q)
    got = (f.get("year_min"), f.get("year_max")) if f.get("year_min") else None
    good = got == (tuple(want) if want else None)
    ok += good
    print(f"  {'ok ' if good else 'BAD'} {q!r}: {got} (want {want})")
print(f"phrase table: {ok}/{len(cases)}")
PY
  st v6_dates_off_tune tune DATE_WORDS=0
  st v6_dates_on_tune tune DATE_WORDS=1
  st v6_dates_off_check check DATE_WORDS=0
  st v6_dates_on_check check DATE_WORDS=1
  st v6_disc_on_tune tune DISCOVER=1
  st v6_disc_on_check check DISCOVER=1
  echo "--- DISCOVER examples (top 5 films, off -> on)"
  for flag in 0 1; do
    DISCOVER=$flag DATE_WORDS=1 python - <<'PY'
import contextlib, io, os
import retrieve as R
qs = ["show me highly rated horror movies", "any hidden gems from the 80s?", "recommend underrated sci-fi",
      "what are the best comedies of all time", "what movies came out in 1999", "lesser known thrillers from the last 10 years"]
print(f"DISCOVER={os.environ['DISCOVER']}")
for q in qs:
    with contextlib.redirect_stdout(io.StringIO()):
        hits = R.retrieve(q, k=5)
    print(f"  {q}: " + "; ".join(f"{h['title']} ({str(h.get('release_year') or '')[:4]})" for h in hits[:5]))
PY
  done
}
step_aspects() {
  timeout 3h python reviews_analyze.py --kinds audience,critic,pro,ytc --films 1500 | tail -5
  python reviews_analyze.py aggregate
}
ev() { local tag=$1; shift; echo "=== eval_v3 $tag $* $(date +%H:%M)"
       env "$@" python eval_v3.py --category why,compare,reviews --answers --tag "$tag" | tail -4; }
step_money() {
  for s in convert money real report; do python money_extra.py $s | tail -3; done
}
step_critics() {
  ev v6_crit_off PERF=0 CRITIC_SCORES=0 MONEY_REAL=0
  ev v6_crit_on PERF=0 CRITIC_SCORES=1 MONEY_REAL=1
}
step_news() {
  python - <<'PY'
import facts_db as F
c = F.connect()
n = c.execute("delete from news_film").rowcount
c.commit()
print(f"news_film cleared ({n} rows) so news.py films uses the NYT headlines")
PY
  python news.py embed | tail -3          # vectors for the NYT headlines fetched since 15 Oct
  python news.py films | tail -5
  python news.py analyze | tail -12
}
step_perf() {
  python performance.py fit | tail -40
}
step_recent() {
  python add_recent.py eval --n 100 | tail -12
}
step_confidence() {
  python confidence.py fit | tail -25
}
step_summary() {
{
  echo "v6 day summary  $(date)"
  echo; echo "--- free data"; python fetch_free_data.py coverage 2>&1 | tail -14
  grep -E "phrase table:|BAD" "$LOG" | tail -8
  echo; grep -A16 -- "--- DISCOVER examples" "$LOG" | tail -16
  python - <<'PY'
import glob, json, os
def st(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    if not fs:
        return None
    rows = json.load(open(fs[-1]))["rows"]
    return sum(1 for r in rows if r["result"]["pass"]), len(rows)
def show(t):
    r = st(t)
    return f"{r[0]}/{r[1]}" if r else "-"
print("\n--- nicknames (fixed grading)")
a, b = st("v6_alias_off"), st("v6_alias_on")
print(f"  ALIASES=0 {show('v6_alias_off')}   ALIASES=1 {show('v6_alias_on')}")
if a and b:
    print(f"  ALIASES: {'KEEP ON (rule passed: +5 or more)' if b[0] - a[0] >= 5 else 'rule not met (+5 needed)'}")
print("\n--- DATE_WORDS retrieval")
res = {}
for split in ("tune", "check"):
    off, on = st(f"v6_dates_off_{split}"), st(f"v6_dates_on_{split}")
    res[split] = (off, on)
    print(f"  {split}: off {show(f'v6_dates_off_{split}')}  on {show(f'v6_dates_on_{split}')}")
ok = all(o and n and n[0] >= o[0] - 1 for o, n in res.values())
print(f"  DATE_WORDS retrieval part: {'PASS' if ok else 'FAIL or missing'} (also needs every phrase-table row ok)")
print("\n--- DISCOVER retrieval")
d = {sp: (st(f"v6_dates_off_{sp}"), st(f"v6_disc_on_{sp}")) for sp in ("tune", "check")}
for sp, (o, n) in d.items():
    print(f"  {sp}: off {show(f'v6_dates_off_{sp}')}  on {show(f'v6_disc_on_{sp}')}")
ok2 = all(o and n and n[0] >= o[0] - 1 for o, n in d.values())
print(f"  DISCOVER: {'TURN ON (rule passed)' if ok2 else 'keep off (rule failed or missing)'}")
PY
  python - <<'PY'
import glob, json, os
def ev(tag):
    fs = sorted(glob.glob(f"results/v3eval_{tag}.json"), key=os.path.getmtime)
    if not fs:
        return None
    js = [r["judge"] for r in json.load(open(fs[-1]))["rows"] if r.get("judge")]
    g = [j.get("grounded") for j in js if isinstance(j.get("grounded"), (int, float)) and j.get("grounded") > 0]
    return {"pass": sum(1 for j in js if j.get("verdict") == "pass"), "judged": len(js),
            "grounded": round(sum(g) / len(g), 2) if g else None}
off, on = ev("v6_crit_off"), ev("v6_crit_on")
print(f"\n--- CRITIC_SCORES + MONEY_REAL with PERF=0: off {off}  on {on}")
if off and on:
    ok = on["pass"] >= off["pass"] and (on["grounded"] or 0) >= (off["grounded"] or 0) - 0.05
    print(f"  {'KEEP BOTH ON (rule passed)' if ok else 'TURN BOTH OFF (rule failed)'}")
PY
  echo; echo "--- recent films (add_recent.py eval)"; grep -A12 "=== step recent" "$LOG" | tail -12
  echo; echo "--- money (money_extra report)"; head -12 results/money_extra_summary.txt 2>/dev/null
  echo; echo "--- box-office model after money + news"; cat results/performance_summary.txt 2>/dev/null | head -30
  echo; echo "--- confidence"; cat results/confidence_summary.txt 2>/dev/null | head -25
  echo; echo "--- review sources (film_aspects rows by source)"
  python - <<'PY'
import facts_db as F
c = F.connect(readonly=True)
for st, n, f in c.execute("select source_type, count(*), count(distinct tmdb_id) from film_aspects group by source_type"):
    print(f"  {st:9s} {n:,} aspect rows for {f:,} films")
PY
} > "$SUM" 2>&1
cat "$SUM"
}
for s in $STEPS; do
  if declare -F "step_$s" >/dev/null; then echo "=== step $s $(date +%H:%M)"; "step_$s"
  else echo "unknown step: $s (steps: data money wait dates critics news perf recent confidence aspects aliases summary)"; fi
done
echo "=== run_v6_day.sh finished $(date)"
