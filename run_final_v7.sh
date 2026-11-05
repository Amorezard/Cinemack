#!/usr/bin/env bash
# The v7 sealed run (Tue 3 Nov night, after the 18:00 code freeze and `git tag v7.0-lock`): v6.0 against v7 on
# questions neither version was tuned on, each arm run once, both arms declared here before anything runs.
#
#   v6 arm   every v7 switch off (V7_OFF) on the v7.0-lock code: what the site answered before v7, except the
#            always-on v7 changes (per-request state, NYT text removed from prompts for its terms, link allow-list in
#            the page). For the untouched v6.0 code instead: `git worktree add ../rag-v6 v6.0-lock` and
#            V6_DIR=../rag-v6 bash run_final_v7.sh  (it reads the same facts.db / movies.db / wiki.db).
#   v7 arm   the switch set in v7_lock.env (1 Nov: written by `python v7_decide.py` from the rules; config.yaml on the
#            server holds no v7 switch lines, so the arm sets every v7 switch explicitly instead of relying on it).
#   reported arm (1 Nov, declared before the run): v7 + RECEPTION=1 on the analysis/reception set. Reported only;
#            it does not change what ships (RECEPTION's rule could not pass this week).
#
# Question sets (written by the user by Sun 1 Nov; films, people and wording not used while tuning):
#   sets/sealed_v7_ret.json   "what's that movie" / recommendation questions   -> stage_scores.py (film found?)
#   sets/sealed_v7_an.json    fact, analysis and reception questions          -> eval_v3.py (answers + judge)
#   sets/sealed_v7_mt.json    multi-turn conversations                         -> stage_scores.py
#   sets/sealed_v7_tv.json    TV questions (only if TV=1 is locked)            -> eval_v3.py
#   sets/sealed_v7_sh.json    short-film questions (only if SHORTS=1 is locked) -> eval_v3.py
# A missing set is skipped and named in the summary.
#
#   tmux new -s final
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag && bash run_final_v7.sh
set -u
cd "$(dirname "$0")"
EVAL_ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1"
V7_OFF="ALIAS_IMDB=0 RECEPTION=0 TV=0 PEOPLE=0 REC_CLEAN=0 IMDB=0 GUARD=0 MODERATION=0 MEM_EMBED=0 CTX_BUILDER=0 PROMPT_ORDER=0 FAST=0 SEMCACHE=0"
V7_OFF="$V7_OFF SHORTS=0 CLARIFY=0 ISSUES=0 YT_COMMENTS=0 TV_REVIEWS=0"
V6_ENV="$EVAL_ENV $V7_OFF PERF=0"
if [ ! -f v7_lock.env ]; then echo "no v7_lock.env: run python v7_decide.py first (Monday)"; exit 1; fi
V7_LOCK=$(grep -v '^#' v7_lock.env | tr '\n' ' ')
V7_ENV="$EVAL_ENV $V7_OFF $V7_LOCK"          # later values win: the lock's
locked() { [[ " $V7_LOCK " == *" $1=1 "* ]]; }
V6_DIR=${V6_DIR:-.}
LOG=results/final_v7.log
SUM=results/final_v7_summary.txt
STAMP=results/final_v7_stamp.txt
STEPS=${*:-stamp retrieval analytic multiturn tv shorts recall reception features summary}
mkdir -p results
exec > >(tee -a "$LOG") 2>&1
echo "=== run_final_v7.sh started $(date)   steps: $STEPS"
echo "    v6 arm: $V6_ENV  (code: $V6_DIR)"
echo "    v7 arm: $V7_ENV + config.yaml"
has() { [[ " $STEPS " == *" $1 "* ]]; }
while pgrep -f "stage_scores.py|eval_v3.py|reviews_analyze.py|reception_cards.py summaries|run_v7_night.sh" >/dev/null; do
  echo "waiting for another run ... $(date +%H:%M)"; sleep 120; done
ready() { [ -f "sets/sealed_v7_$1.json" ] && ! grep -q '"<' "sets/sealed_v7_$1.json"; }   # written, no <...> left
for s in ret an mt tv sh rc; do [ -f "sets/sealed_v7_$s.json" ] && ! ready $s && echo "sets/sealed_v7_$s.json still has <...> placeholders: skipped"; done
v6() { ( cd "$V6_DIR" && env $V6_ENV "$@" ); }
v7() { env $V7_ENV "$@"; }
v6out() { if [ "$V6_DIR" = . ]; then echo "$1"; else echo "$V6_DIR/$1"; fi; }

if has stamp; then
  { echo "date: $(date)"
    echo "git: $(git describe --always --dirty --tags) $(git rev-parse HEAD)"
    git tag --points-at HEAD | grep -q "^v7.0-lock$" && echo "HEAD is v7.0-lock" || echo "WARNING: HEAD is not tagged v7.0-lock"
    [ "$V6_DIR" != . ] && echo "v6 code: $(cd "$V6_DIR" && git describe --always --dirty --tags)"
    echo "v6 arm: $V6_ENV"; echo "v7 arm: $V7_ENV"; echo "v7_lock.env: $V7_LOCK"
    echo "config.yaml v7 switches:"
    grep -E "^[[:space:]]*(ALIAS_IMDB|RECEPTION|TV|PEOPLE|REC_CLEAN|IMDB|GUARD|MODERATION|MEM_EMBED|CTX_BUILDER|PROMPT_ORDER|FAST|SEMCACHE|RECENT|PERSONAL_W):" config.yaml | sed 's/^/  /'
    md5sum retrieve.py chat.py tools.py conv.py evidence.py reception_cards.py tv.py shorts.py safety.py config.yaml v7_lock.env sets/sealed_v7_*.json 2>/dev/null
    ls -la facts.db movies.db wiki.db 2>/dev/null
    ollama show movie-buff 2>/dev/null | head -8
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null
  } > "$STAMP"
  cat "$STAMP"
fi

if has retrieval && ready ret; then
  echo "=== sealed retrieval, v6 arm $(date +%H:%M)"
  v6 python stage_scores.py --questions "$PWD/sets/sealed_v7_ret.json" --tag sealed7_ret_v6 | tail -6
  echo "=== sealed retrieval, v7 arm $(date +%H:%M)"
  v7 python stage_scores.py --questions sets/sealed_v7_ret.json --tag sealed7_ret_v7 | tail -6
fi
if has analytic && ready an; then
  v6 python eval_v3.py --questions "$PWD/sets/sealed_v7_an.json" --tag sealed7_an_v6 --answers | tail -20
  v7 python eval_v3.py --questions sets/sealed_v7_an.json --tag sealed7_an_v7 --answers | tail -20
  [ "$V6_DIR" != . ] && cp "$V6_DIR/results/v3eval_sealed7_an_v6.json" results/ 2>/dev/null
  python eval_v3.py --compare results/v3eval_sealed7_an_v6.json results/v3eval_sealed7_an_v7.json > results/final_v7_analytic_compare.txt
  python claimcheck.py eval results/v3eval_sealed7_an_v6.json | tail -2
  python claimcheck.py eval results/v3eval_sealed7_an_v7.json | tail -2
fi
if has multiturn && ready mt; then
  v6 python stage_scores.py --questions "$PWD/sets/sealed_v7_mt.json" --tag sealed7_mt_v6 | tail -4
  v7 python stage_scores.py --questions sets/sealed_v7_mt.json --tag sealed7_mt_v7 | tail -4
fi
if has tv && ready tv && locked TV; then
  v6 python eval_v3.py --questions "$PWD/sets/sealed_v7_tv.json" --tag sealed7_tv_v6 --answers | tail -10
  v7 python eval_v3.py --questions sets/sealed_v7_tv.json --tag sealed7_tv_v7 --answers | tail -10
  [ "$V6_DIR" != . ] && cp "$V6_DIR/results/v3eval_sealed7_tv_v6.json" results/ 2>/dev/null
fi
if has shorts && ready sh && locked SHORTS; then
  v6 python eval_v3.py --questions "$PWD/sets/sealed_v7_sh.json" --tag sealed7_sh_v6 --answers | tail -10
  v7 python eval_v3.py --questions sets/sealed_v7_sh.json --tag sealed7_sh_v7 --answers | tail -10
  [ "$V6_DIR" != . ] && cp "$V6_DIR/results/v3eval_sealed7_sh_v6.json" results/ 2>/dev/null
fi
if has recall && ready rc && locked MEM_EMBED; then        # 4 Nov: the blind memory check (cut-off fixed on 3 Nov)
  echo "=== sealed recall (memory) $(date +%H:%M)"
  python memory_eval.py --set sets/sealed_v7_rc.json --tag sealed7 | tail -3 | tee results/final_v7_recall.txt
fi
if has reception && ready an && ! locked RECEPTION; then
  echo "=== reported arm: v7 + RECEPTION=1 (not shipped) $(date +%H:%M)"
  v7 env RECEPTION=1 python eval_v3.py --questions sets/sealed_v7_an.json --tag sealed7_an_v7rec --answers | tail -20
  python claimcheck.py eval results/v3eval_sealed7_an_v7rec.json | tail -2
fi
# 3 Nov (declared before the sealed run): every switch turned on by the 3 Nov fixes (on in v7_lock.env, off in the
# 1 Nov lock v7_lock_base.env) gets a blind "v7 without it" arm on the sealed set(s) it affects, so the sealed run
# shows what each one adds on questions it was never tuned on.
BASE_ENV=$( [ -f v7_lock_base.env ] && grep -v '^#' v7_lock_base.env | tr '\n' ' ' )
added() { locked "$1" && [ -n "$BASE_ENV" ] && [[ " $BASE_ENV " != *" $1=1 "* ]]; }
if has features; then
  for sw in RECEPTION CTX_BUILDER PROMPT_ORDER TV CLARIFY SHORTS ISSUES YT_COMMENTS TV_REVIEWS PERF; do
    added "$sw" || continue
    lsw=$(echo "$sw" | tr 'A-Z_' 'a-z-')
    echo "=== feature arm: v7 without $sw $(date +%H:%M)"
    case $sw in
      RECEPTION) ready an && v7 env RECEPTION=0 python eval_v3.py --questions sets/sealed_v7_an.json --tag "sealed7_an_no-$lsw" --answers | tail -6 ;;
      CTX_BUILDER|PROMPT_ORDER)
        ready an && v7 env $sw=0 python eval_v3.py --questions sets/sealed_v7_an.json --tag "sealed7_an_no-$lsw" --answers | tail -6
        ready mt && v7 env $sw=0 python stage_scores.py --questions sets/sealed_v7_mt.json --tag "sealed7_mt_no-$lsw" | tail -4 ;;
      TV) ready tv && v7 env TV=0 CLARIFY=0 python eval_v3.py --questions sets/sealed_v7_tv.json --tag "sealed7_tv_no-$lsw" --answers | tail -6 ;;
      CLARIFY) ready tv && v7 env CLARIFY=0 python eval_v3.py --questions sets/sealed_v7_tv.json --tag "sealed7_tv_no-$lsw" --answers | tail -6 ;;
      SHORTS) ready sh && v7 env SHORTS=0 python eval_v3.py --questions sets/sealed_v7_sh.json --tag "sealed7_sh_no-$lsw" --answers | tail -6 ;;
      ISSUES|YT_COMMENTS|PERF) ready an && v7 env $sw=0 python eval_v3.py --questions sets/sealed_v7_an.json --tag "sealed7_an_no-$lsw" --answers | tail -6 ;;
      TV_REVIEWS) ready tv && v7 env TV_REVIEWS=0 python eval_v3.py --questions sets/sealed_v7_tv.json --tag "sealed7_tv_no-$lsw" --answers | tail -6 ;;
    esac
  done
fi
[ "$V6_DIR" != . ] && cp "$V6_DIR"/results/stage_scores_*_sealed7_*_v6.json results/ 2>/dev/null

if has summary; then
{
  echo "SEALED RUN v6.0 vs v7  $(date)"
  cat "$STAMP" 2>/dev/null
  echo
  for s in ret an mt tv sh rc; do ready $s || echo "(sets/sealed_v7_$s.json missing or not filled in: not run)"; done
  python - <<'PY'
import glob, json, os
def latest(tag):
    fs = sorted(glob.glob(f"results/stage_scores_*_{tag}.json"), key=os.path.getmtime)
    return json.load(open(fs[-1])) if fs else None
def stage(name, a, b):
    da, db = latest(a), latest(b)
    if not (da and db):
        return f"{name:26s} (not run)"
    pa = sum(r["result"]["pass"] for r in da["rows"]); pb = sum(r["result"]["pass"] for r in db["rows"])
    ida = {r["id"]: r["result"]["pass"] for r in da["rows"]}
    lost = [r["id"] for r in db["rows"] if ida.get(r["id"]) and not r["result"]["pass"]]
    gained = [r["id"] for r in db["rows"] if not ida.get(r["id"]) and r["result"]["pass"]]
    return f"{name:26s} v6 {pa}/{len(da['rows'])}   v7 {pb}/{len(db['rows'])}   ({pb - pa:+d}; lost {lost}, gained {gained})"
def judged(name, a, b):
    pa, pb = f"results/v3eval_{a}.json", f"results/v3eval_{b}.json"
    if not (os.path.exists(pa) and os.path.exists(pb)):
        return f"{name:26s} (not run)"
    out = []
    for p in (pa, pb):
        rows = [r for r in json.load(open(p))["rows"] if r.get("answer")]
        js = [r.get("judge") or {} for r in rows]
        g = [j["grounded"] for j in js if isinstance(j.get("grounded"), (int, float)) and j["grounded"] > 0]
        out.append((sum(j.get("verdict") == "pass" for j in js), len(rows), sum(g) / len(g) if g else 0))
    (a1, n1, g1), (a2, n2, g2) = out
    return f"{name:26s} v6 {a1}/{n1} (grounded {g1:.2f})   v7 {a2}/{n2} (grounded {g2:.2f})   ({a2 - a1:+d})"
print(stage("sealed retrieval", "sealed7_ret_v6", "sealed7_ret_v7"))
print(stage("sealed multi-turn", "sealed7_mt_v6", "sealed7_mt_v7"))
print(judged("sealed analysis/reception", "sealed7_an_v6", "sealed7_an_v7"))
print(judged("sealed TV", "sealed7_tv_v6", "sealed7_tv_v7"))
print(judged("sealed short films", "sealed7_sh_v6", "sealed7_sh_v7"))
print(judged("reported: v7+RECEPTION", "sealed7_an_v7", "sealed7_an_v7rec") + "   (v7 vs v7 + RECEPTION=1; not shipped)")
import re as _re
print("feature arms (blind; 'v6' column = v7 WITHOUT the switch, 'v7' column = v7 with it):")
for p in sorted(glob.glob("results/v3eval_sealed7_*_no-*.json")):
    t = os.path.basename(p)[len("v3eval_"):-5]
    s_, rest = t.split("_no-", 1)
    print(judged(f"  without {rest} ({s_[8:]})", t, s_ + "_v7"))
for t in sorted({m.group(0) for p in glob.glob("results/stage_scores_*_sealed7_mt_no-*.json")
                 for m in [_re.search(r"sealed7_mt_no-[a-z-]+", os.path.basename(p))] if m}):
    print(stage(f"  without {t.split('_no-', 1)[1]} (mt)", t, "sealed7_mt_v7"))
for t in ("sealed7_an_v6", "sealed7_an_v7", "sealed7_an_v7rec"):
    p = f"results/claimcheck_{t}.json"
    if os.path.exists(p):
        print(f"claim check {t}: {json.load(open(p)).get('totals')}")
PY
  [ -f results/final_v7_recall.txt ] && { echo; echo "sealed recall (memory, blind):"; cat results/final_v7_recall.txt; }
  echo
  head -40 results/final_v7_analytic_compare.txt 2>/dev/null
  echo; echo "Judge: gemma3:27b (agreement with hand grades: results/judge_check_report.txt, if graded)."
  cat results/judge_check_report.txt 2>/dev/null | head -3
} > "$SUM" 2>&1
cat "$SUM"
fi
echo "=== run_final_v7.sh finished $(date)"
