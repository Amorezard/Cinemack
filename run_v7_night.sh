#!/usr/bin/env bash
# v7 nights (from Fri 23 Oct). One script; each night is a list of steps; summary in results/v7_night_summary.txt.
#
#   tmux new -s night
#   source ~/venvs/movie-rag/bin/activate && cd ~/movie-buff/rag
#   export TMDB_API_KEY=...  MB_CONTACT='you@example.edu'  OMDB_API_KEY=...   # OMDb optional
#   bash run_v7_night.sh                 # tonight's steps (Tue 27 Oct), in order
#   bash run_v7_night.sh critics summary # or any steps by name
#
# Tue 27 Oct (catch-up night: the reception core's data)
#   status     what Friday's night left: coverage, running jobs, free disk / GPU               seconds
#   netbg      IN THE BACKGROUND (own log, results/v7_net.log): Wikidata review scores (E1),
#              Wikipedia articles for TV shows (tvwiki, resumes), OMDb daily batch (H5)        ~1-2 h
#   reception  Wikipedia reception wikitext (R2 input; resumes Friday's fetch if it stopped)    0 - 60 min
#   critics    critic statements + the outlet each cites (critics.py, R2)                       ~5 min
#   sentiment  RoBERTa sentiment for every TMDB review, TV review and critic statement, with
#              the 3-class head fitted on star ratings (sentiment.py, R3)                       GPU ~30 min / CPU ~3 h
#   flags      joke / sarcasm (irony model), review-bombing bursts, rating-text mismatch (R5)   ~10 min
#   samples    the 200-review labelling page (H7) and the 50-film outlet check (R2)             seconds
#   aspects    aspects for every reviewed film not yet done (R4), llama3.2, capped at 4 h       <= 4 h
#   cards      reception card per film (reception_cards.py build: R12, R8, E2, E3, E4, H9)      ~5 min
#   summaries  three-level reception summaries, most-reviewed films first (R13), capped at 2 h  <= 2 h
#   cards      again, so the cards carry the new summaries                                       ~5 min
#   waitbg     wait for netbg
#   summary    results/v7_night_summary.txt
#
# Fri 23 Oct steps (still callable): disk sources tv imdb movielens genome reception tvwiki omdb
#
# Later nights (bash run_v7_night.sh wed | thu | fri | sat | allon). Switches are tested TOGETHER where they touch
# different things, against one baseline with every v7 switch off (V7_OFF); a combined arm that loses > 1 on tune or
# check is split into one arm per switch on Saturday (bisect).
#   wed    ytbg (YouTube channels/fetch/match + H4 pageviews, background), evidence index, reception A/B on the
#          40-question set, claim check, evidence hit@5, analytic regression, rules, more summaries
#   thu    TV passages, IMDb aliases; tune/check off vs D arm (TV + PEOPLE + REC_CLEAN + ALIAS_IMDB); TV set A/B;
#          analytic with the D arm; people / discovery / fast-path / semantic-cache checks; recall by meaning;
#          40 attacks off vs on; moderation false blocks; GUARD on the analytic set; rules
#   fri    (30 Oct: stageoff again on the real tune/check sets, then TV and ALIAS_IMDB alone after their fixes)
#          Ollama speed A/B (speed_ab.sh); context builder + prompt order (analytic, multi-turn); RECENT=0 A/B;
#          confidence --identify; personal sweep32 (3 h cap); retriever metrics; H4 performance fit;
#          nightly.sh dry run; YouTube refresh; rules
#   sat    bisect a failed D arm; 50-video match sample; E7 judge-check page; more summaries; rules
#   allon  (Sun 1 / Mon 2 Nov, after setting the passed switches in config.yaml) everything together vs off on
#          tune, check, analytic, reception and multi-turn; rules
#
# 1 Nov (Sun) - Tue 3 Nov: a day run and a night run each day, so everything is decided before Tuesday's freeze.
# Every switch is compared with an OFF arm run the SAME day (Sunday's finding: a baseline from another day differs by
# 1-2 analytic questions with no switch changed).
#   bash run_v7_night.sh sunday     Sun day  (~1.5 h): reception cards with the known-count guard; TV failure report;
#                                   routing checks (TV film-title guard, shorts); short-film coverage; why a11 changed;
#                                   analytic off / ALIAS_IMDB / PEOPLE+REC_CLEAN the same afternoon; rules
#   bash run_v7_night.sh sunnight   Sun night (~7 h): same-night off arms (tune, check, analytic, multi-turn, TV set,
#                                   shorts set); TV re-test; SHORTS; CTX_BUILDER and PROMPT_ORDER each alone; recall;
#                                   speed (last, alone); pageviews restart (background); v7_decide.py -> v7_lock.env
#   bash run_v7_night.sh monday     Mon day  (~2-3 h): everything in v7_lock.env together vs off (tune, check,
#                                   analytic, multi-turn, TV / shorts sets if locked)
#   bash run_v7_night.sh monnight   Mon night: spare - re-run any step by name; more reception summaries; judge-check
#                                   page; video match sample; nightly dry run
#   bash run_v7_night.sh tueday     Tue day: tests, sealed-set check, git status (then 18:00 freeze + git tag v7.0-lock)
#   Tue night: bash run_final_v7.sh  (the sealed run; reads v7_lock.env)
#
# Nothing tonight changes what the site answers: the new tables are read only by code that is switched off
# (RECEPTION=0) until its rule passes. A failed step does not stop the next one; errors go to the log and summary.
set -u -o pipefail
cd "$(dirname "$0")"
LOG=results/v7_night.log
SUM=results/v7_night_summary.txt
# 30 Oct: `bash run_v7_night.sh summary` on its own writes a separate file, so it cannot replace the night's summary
[ "$*" = "summary" ] && SUM=results/v7_summary_now.txt
NETLOG=results/v7_net.log
# Named nights: `bash run_v7_night.sh tue` (= the default tonight), `bash run_v7_night.sh wed`, ...
TUE="status netbg reception critics sentiment flags samples aspects cards summaries cards waitbg summary"
WED="status ytbg mccheck evidence cards recab claim hit analytic rules summaries cards waitbg summary"
# Thu 29 Oct: the D switches tested TOGETHER against one baseline (a combined arm that loses > 1 is split on Saturday)
THU="status tvbuild aliasbuild stageoff staged tvab analyticd peoplecheck disccheck fastcheck semcheck recall attack blocks guardan thurules summary"
# Fri 30 Oct: speed and prompt changes, recent films, report-only fits, H4, nightly dry run
# 30 Oct update: stageoff again first (Thursday's off arm ran the default 63 questions twice: `--split` without
# `--questions` ignores the split), then the TV and ALIAS_IMDB fixes each alone, before the planned Friday steps
FRI="status stageoff tvfix aliasfix speed ctxab recentab confid sweep32 metrics h4 nightlydry ytrefresh frirules summary"
# Sat 31 Oct: split any combined arm that failed; YouTube match check sample; E7 judge-check page
SAT="status bisect ytsample judgecheck summaries cards satrules summary"
# Sun 1 / Mon 2 Nov: everything that passed, together (config.yaml as set by hand) against all v7 switches off
ALLON="status allon allonrules summary"
SUNDAY="status cardsfix tvreport routechecks shortscov a11diff sameday sunrules summary"
SUNNIGHT="status bgviews stagebase anbase tvtest shortstest ctxsplit recall speed sunnightrules summary"
MONDAY="status lockshow allon allonrules summary"
MONNIGHT="status summaries cards judgecheck ytsample nightlydry summary"
TUEDAY="status tests sealedcheck freezecheck summary"
# Tue 3 Nov (3 Nov fixes): each fixed switch added to the LOCKED set, against the locked set run the same day
FIX="status fixtests fixbase fixtv fixclar fixsh fixcb fixpo fixrec fixmem fixrules summary"
# Tue night: everything the fix rules passed, together, against the same base (after `python v7_decide.py --fix`)
FIXALL="status fixall fixallrules summary"
# Wed 4 Nov: new data (YouTube stats + comments, Wikipedia themes / controversy), then each new switch added to the
# lock against the lock run the same day; v7_decide.py --feat adds the ones that pass
FEAT="status featfetch featprep featbase featissues featyt feattv featperf featrules summary"
case "${1:-tue}" in
  tue|TUE) STEPS=$TUE ;;
  wed|WED) STEPS=$WED ;;
  thu|THU) STEPS=$THU ;;
  fri|FRI) STEPS=$FRI ;;
  sat|SAT) STEPS=$SAT ;;
  allon|ALLON) STEPS=$ALLON ;;
  sunday|SUNDAY) STEPS=$SUNDAY ;;
  sunnight|SUNNIGHT) STEPS=$SUNNIGHT ;;
  monday|MONDAY) STEPS=$MONDAY ;;
  monnight|MONNIGHT) STEPS=$MONNIGHT ;;
  tueday|TUEDAY) STEPS=$TUEDAY ;;
  fix|FIX) STEPS=$FIX ;;
  fixall|FIXALL) STEPS=$FIXALL ;;
  feat|FEAT) STEPS=$FEAT ;;
  *)   STEPS=$* ;;
esac
ASPECT_HOURS=${ASPECT_HOURS:-4}
SUM_HOURS=${SUM_HOURS:-2}
mkdir -p results logs
LOG_START=$(( $(wc -l < "$LOG" 2>/dev/null || echo 0) + 1 ))   # errors are listed from tonight only
exec > >(tee -a "$LOG") 2>&1
echo "=== run_v7_night.sh started $(date)   steps: $STEPS"
declare -A STATUS
BG_PID=""

pyq() { python -c "import sqlite3, sys
c = sqlite3.connect('file:facts.db?mode=ro', uri=True)
for q in sys.argv[1:]:
    try:
        [print('  ', *r) for r in c.execute(q)]
    except sqlite3.Error as e:
        print('   (', e, ')')" "$@"; }
need() { if [ -z "${!1:-}" ]; then echo "(no $1 in this shell: step skipped)"; return 1; fi; }

step_disk()      { df -h . | tail -1; du -sh facts.db 2>/dev/null; }
step_status() {
  df -h . | tail -1; du -sh facts.db data 2>/dev/null
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || true
  pgrep -af "stage_scores|eval_v3|v7_data|reviews_analyze|fetch_|sentiment.py|critics.py" | grep -v -e pgrep -e run_v7_night || echo "  no other jobs"
  python v7_data.py coverage 2>&1 | grep -E "Wikipedia reception|TV shows|IMDb:|MovieLens distributions|tag genome"
}
step_sources()   { python v7_data.py sources; }
step_tv()        { need TMDB_API_KEY || return 1; python v7_data.py tv | sed "/^  tv: /d" | tail -25; return ${PIPESTATUS[0]}; }
step_imdb()      { python v7_data.py imdb | tail -25; return ${PIPESTATUS[0]}; }
step_movielens() { python v7_data.py movielens | tail -30; return ${PIPESTATUS[0]}; }
step_genome()    { python v7_data.py genome | tail -8; return ${PIPESTATUS[0]}; }
step_reception() { python v7_data.py reception | sed -E "/^  (TV|IMDb|MovieLens|tag genome|    )/d" | tail -12; return ${PIPESTATUS[0]}; }
step_tvwiki()    { python v7_data.py tv-wiki | tail -8; return ${PIPESTATUS[0]}; }
step_omdb()      { need OMDB_API_KEY || return 1; python fetch_free_data.py omdb | tail -6; return ${PIPESTATUS[0]}; }
step_wdscores()  { python v7_data.py wd-scores | sed -nE "/wd-scores|Wikidata review|only from Wikidata|items,/p" | tail -6; return ${PIPESTATUS[0]}; }
step_netbg() {
  ( echo "=== netbg started $(date)"
    echo "--- wd-scores";  python v7_data.py wd-scores 2>&1 | sed -nE "/wd-scores|Wikidata review|only from Wikidata|items,|Error|Traceback/p" | tail -8
    echo "--- tv-wiki";    python v7_data.py tv-wiki 2>&1 | tail -6
    if [ -n "${OMDB_API_KEY:-}" ]; then echo "--- omdb"; python fetch_free_data.py omdb 2>&1 | tail -6; else echo "--- omdb skipped (no OMDB_API_KEY)"; fi
    echo "=== netbg finished $(date)" ) > "$NETLOG" 2>&1 &
  BG_PID=$!
  echo "  network jobs running in the background (pid $BG_PID, log $NETLOG)"
}
step_waitbg() { if [ -n "$BG_PID" ]; then echo "  waiting for pid $BG_PID"; wait "$BG_PID"; fi; tail -25 "$NETLOG" 2>/dev/null; }
step_critics()   { python critics.py run | tail -12; return ${PIPESTATUS[0]}; }
step_sentiment() { python sentiment.py run | tail -12; return ${PIPESTATUS[0]}; }
step_flags()     { python sentiment.py flags | tail -8; return ${PIPESTATUS[0]}; }
step_samples()   { python sentiment.py sample 200 | tail -4; python critics.py sample 50 | tail -3; }
step_aspects() {
  timeout "${ASPECT_HOURS}h" python reviews_analyze.py --kinds audience,critic --films 40000 2>&1 | sed "/retry/d" | tail -6
  local rc=${PIPESTATUS[0]}
  [ "$rc" = 124 ] && echo "  (stopped at the ${ASPECT_HOURS} h cap; it resumes next night)"
  python reviews_analyze.py aggregate | tail -2
  [ "$rc" = 0 ] || [ "$rc" = 124 ]
}
step_cards()     { python reception_cards.py build | tail -4; return ${PIPESTATUS[0]}; }
step_summaries() {
  timeout "${SUM_HOURS}h" python reception_cards.py summaries --films 3000 2>&1 | sed "/retry/d" | tail -3
  local rc=${PIPESTATUS[0]}
  [ "$rc" = 124 ] && echo "  (stopped at the ${SUM_HOURS} h cap; it resumes next night)"
  python reception_cards.py check 20 | tail -1
  [ "$rc" = 0 ] || [ "$rc" = 124 ]
}
# ---- Wed 28 Oct: reception index + the reception A/B (R14-R16) and the analytic regression
EVAL_ENV="TOOLS=1 EXTRA_DATA=1 DATE_WORDS=1"          # the live site's switches, so off/on differ only in RECEPTION
step_mccheck()   { python reception_cards.py mccheck "Lawrence of Arabia|Frida|The White Ribbon|Three Colors: Red" | head -14; }
step_evidence()  { python evidence.py build | tail -6; return ${PIPESTATUS[0]}; }
step_recab() {
  env $EVAL_ENV RECEPTION=0 python eval_v3.py --questions sets/reception.json --tag rec_off --answers | tail -14
  env $EVAL_ENV RECEPTION=1 python eval_v3.py --questions sets/reception.json --tag rec_on --answers | tail -14
}
step_claim() {
  python claimcheck.py eval results/v3eval_rec_off.json | tail -2
  python claimcheck.py eval results/v3eval_rec_on.json | tail -2
  python claimcheck.py summaries --limit 300 | grep -v "^  " | head -5
}
step_hit()       { python evidence.py hit sets/reception.json | tail -3; return ${PIPESTATUS[0]}; }
step_analytic() {
  env $EVAL_ENV RECEPTION=0 python eval_v3.py --questions sets/analytic.json --tag an_rec_off --answers | tail -14
  env $EVAL_ENV RECEPTION=1 python eval_v3.py --questions sets/analytic.json --tag an_rec_on --answers | tail -14
}
step_rules() {
  python v7_rules.py reception results/v3eval_rec_off.json results/v3eval_rec_on.json
  python v7_rules.py regression results/v3eval_an_rec_off.json results/v3eval_an_rec_on.json
}

# ---- Wed: YouTube analysis channels + H4 pageviews in the background (network only)
step_ytbg() {
  ( { echo "--- wd-scores (resumes; crashed 27 Oct on a blank value) $(date +%H:%M)"; python v7_data.py wd-scores 2>&1 | tail -4
      if ! pyq "select count(*) from film_tags" 2>/dev/null | grep -q "[1-9]"; then
        echo "--- tag genome retry (failed 24 Oct: SSL) $(date +%H:%M)"; python v7_data.py genome 2>&1 | tail -6; fi
      if [ -n "${YOUTUBE_API_KEY:-}" ]; then
        echo "--- youtube channels $(date +%H:%M)"; python youtube_analysis.py channels 2>&1 | tail -4
        echo "--- youtube fetch $(date +%H:%M)";    python youtube_analysis.py fetch 2>&1 | tail -4
        echo "--- youtube match $(date +%H:%M)";    python youtube_analysis.py match 2>&1 | tail -6
      else echo "(no YOUTUBE_API_KEY: youtube skipped)"; fi
      echo "--- H4 pageviews $(date +%H:%M)"; python fetch_v6_data.py pageviews --workers 1 2>&1 | tail -4
      echo "--- done $(date +%H:%M)"; } >> "$NETLOG" 2>&1 ) &
  BG_PID=$!; echo "  background pid $BG_PID -> $NETLOG"
}

# ---- shared helpers for the A/B nights
V7_OFF="ALIAS_IMDB=0 RECEPTION=0 TV=0 PEOPLE=0 REC_CLEAN=0 IMDB=0 GUARD=0 MODERATION=0 MEM_EMBED=0 CTX_BUILDER=0 PROMPT_ORDER=0 FAST=0 SEMCACHE=0 SHORTS=0 CLARIFY=0 ISSUES=0 YT_COMMENTS=0 TV_REVIEWS=0"
D_ON="TV=1 PEOPLE=1 REC_CLEAN=1 ALIAS_IMDB=1"                  # Thursday's combined retrieval arm
latest() { ls -t results/stage_scores_*_"$1"_"$2".json 2>/dev/null | head -1; }   # latest <split> <tag>
stage2() {                                         # stage2 <tag> <env...>: tune + check
  local tag=$1; shift
  for sp in tune check; do env $EVAL_ENV $V7_OFF "$@" python stage_scores.py --questions sets/$sp.json --tag "$tag" | tail -4; done
}
stagerule() {                                      # stagerule <off tag> <on tag>
  for sp in tune check; do
    local a b; a=$(latest $sp "$1"); b=$(latest $sp "$2")
    if [ -n "$a" ] && [ -n "$b" ]; then echo "  $sp:"; python v7_rules.py stage "$a" "$b"; else echo "  $sp: ($1 / $2 not run)"; fi
  done
}
anrule() { [ -f "results/v3eval_$1.json" ] && [ -f "results/v3eval_$2.json" ] && python v7_rules.py regression "results/v3eval_$1.json" "results/v3eval_$2.json" || echo "  ($1 / $2 not run)"; }

# ---- Thu 29 Oct
step_tvbuild()    { python tv.py build | tail -6; return ${PIPESTATUS[0]}; }
step_aliasbuild() { python aliases.py build | tail -6; return ${PIPESTATUS[0]}; }
step_stageoff()   { stage2 v7off; }
step_staged()     { stage2 v7d $D_ON; }
step_tvab() {
  env $EVAL_ENV $V7_OFF python eval_v3.py --questions sets/tv.json --tag tv_off --answers | tail -10
  env $EVAL_ENV $V7_OFF TV=1 python eval_v3.py --questions sets/tv.json --tag tv_on --answers | tail -10
}
step_analyticd() {
  [ -f results/v3eval_an_rec_off.json ] || env $EVAL_ENV $V7_OFF python eval_v3.py --questions sets/analytic.json --tag an_rec_off --answers | tail -4
  env $EVAL_ENV $V7_OFF $D_ON python eval_v3.py --questions sets/analytic.json --tag an_d --answers | tail -14
}
step_peoplecheck() { env $EVAL_ENV PEOPLE=1 python people.py check sets/people.json | tail -6; }
step_disccheck()   { env $EVAL_ENV REC_CLEAN=1 python rec_clean.py check sets/discovery.json | tail -6; }
step_fastcheck()   { python fastpath.py check sets/analytic.json | tail -6; }
step_semcheck()    { SEMCACHE=1 python web/semcache.py check | tail -3; }
step_recall()      { python memory_eval.py | tail -12; }
step_attack() {
  env $EVAL_ENV $V7_OFF python attack_eval.py --tag off | tail -4
  env $EVAL_ENV $V7_OFF GUARD=1 MODERATION=1 python attack_eval.py --tag on | tail -4
}
step_blocks()  { python moderation.py falseblocks --tag v7 | tail -4; }
step_guardan() { env $EVAL_ENV $V7_OFF GUARD=1 python eval_v3.py --questions sets/analytic.json --tag an_guard --answers | tail -14; }
step_thurules() {
  echo "  D combined arm ($D_ON) vs off, tune / check (lose <= 1 each):"; stagerule v7off v7d
  echo "  D combined arm, analytic (lose <= 1, grounding not lower):"; anrule an_rec_off an_d
  echo "  TV set:"; [ -f results/v3eval_tv_on.json ] && python v7_rules.py tv results/v3eval_tv_on.json --off results/v3eval_tv_off.json
  echo "  GUARD on the analytic set:"; anrule an_rec_off an_guard
  echo "  attacks:"; [ -f results/attack_on.json ] && python v7_rules.py attack results/attack_on.json
  [ -f results/attack_off.json ] && python v7_rules.py attack results/attack_off.json | tail -1 | sed 's/^/  (v6 behaviour) /'
  echo "  moderation false blocks:"; [ -f results/moderation_v7.json ] && python v7_rules.py blocks results/moderation_v7.json
  echo "  recall by meaning:"; [ -f results/recall_on.json ] && python v7_rules.py recall results/recall_off.json results/recall_on.json
  return 0
}

# ---- Fri 30 Oct
step_speed() { bash speed_ab.sh 2>&1 | tail -20; }
step_ctxab() {
  env $EVAL_ENV $V7_OFF CTX_BUILDER=1 PROMPT_ORDER=1 python eval_v3.py --questions sets/analytic.json --tag an_ctx --answers | tail -14
  env $EVAL_ENV $V7_OFF python stage_scores.py --questions sets/multiturn.json --tag mt_off | tail -4
  env $EVAL_ENV $V7_OFF CTX_BUILDER=1 PROMPT_ORDER=1 python stage_scores.py --questions sets/multiturn.json --tag mt_ctx | tail -4
}
step_recentab()  { stage2 v7norecent RECENT=0; }
# 30 Oct: the two switches Friday's bisect blamed, each alone, after their fixes (tv.py routing guards, aliases.py IMDb
# alternate-title guards); the routing / alias checks first (seconds), then tune + check + analytic
step_tvfix() {
  python tv.py routecheck sets/tune.json sets/check.json sets/tv.json 2>&1 | head -40
  stage2 v7tv2 TV=1
  env $EVAL_ENV $V7_OFF TV=1 python eval_v3.py --questions sets/tv.json --tag tv_on2 --answers | tail -10
  env $EVAL_ENV $V7_OFF TV=1 python eval_v3.py --questions sets/analytic.json --tag an_tv2 --answers | tail -14
}
step_aliasfix() {
  python aliases.py imdbcheck sets/tune.json sets/check.json 2>&1 | head -30
  stage2 v7alias2 ALIAS_IMDB=1
  env $EVAL_ENV $V7_OFF ALIAS_IMDB=1 python eval_v3.py --questions sets/analytic.json --tag an_alias2 --answers | tail -14
}
step_confid()    { python confidence.py fit --identify | tail -12; }
step_sweep32()   { timeout 3h python personal.py sweep32 2000 | tail -14; }
step_metrics()   { local f; f=$(latest tune v7off); [ -n "$f" ] && python metrics.py "$f" $(latest check v7off) || echo "  (no v7off stage run)"; }
step_h4()        { python performance.py fit | tail -14; return ${PIPESTATUS[0]}; }
step_nightlydry(){ bash nightly.sh --dry | tail -30; }
step_ytrefresh() { need YOUTUBE_API_KEY || return 1; python youtube_analysis.py refresh | tail -4; }
step_frirules() {
  echo "  TV alone after the 30 Oct fix, tune / check (lose <= 1 each):"; stagerule v7off v7tv2
  echo "  TV alone, analytic (lose <= 1, grounding not lower):"; anrule an_rec_off an_tv2
  echo "  TV set after the fix:"; [ -f results/v3eval_tv_on2.json ] && python v7_rules.py tv results/v3eval_tv_on2.json --off results/v3eval_tv_off.json
  echo "  ALIAS_IMDB alone after the 30 Oct fix, tune / check:"; stagerule v7off v7alias2
  echo "  ALIAS_IMDB alone, analytic:"; anrule an_rec_off an_alias2
  echo "  PEOPLE + REC_CLEAN (30 Oct afternoon run), tune / check:"; stagerule v7off2 v7pr
  echo "  PEOPLE + REC_CLEAN, analytic:"; anrule an_rec_off an_pr
  echo "  context builder + prompt order, analytic:"; anrule an_rec_off an_ctx
  local a b; a=$(ls -t results/stage_scores_*_multiturn_mt_off.json 2>/dev/null | head -1); b=$(ls -t results/stage_scores_*_multiturn_mt_ctx.json 2>/dev/null | head -1)
  echo "  context builder + prompt order, multi-turn:"; [ -n "$a" ] && [ -n "$b" ] && python v7_rules.py stage "$a" "$b"
  echo "  recent films off (RECENT=0) vs on, tune / check:"; stagerule v7off v7norecent
  echo "  speed:"; tail -12 results/speed_ab.txt 2>/dev/null
  return 0
}

# ---- Sat 31 Oct: split a failed combined arm into one arm per switch
step_bisect() {
  if stagerule v7off v7d 2>&1 | grep -q FAIL || anrule an_rec_off an_d 2>&1 | grep -q FAIL; then
    for sw in $D_ON; do echo "  --- $sw alone"; stage2 "v7_${sw%%=*}" "$sw"; done
  else echo "  Thursday's combined arm passed: nothing to split"; fi
}
step_ytsample()  { python youtube_analysis.py sample 50 | tail -3; }
step_judgecheck(){ python judge_check.py sample results/v3eval_an_rec_on.json results/v3eval_rec_on.json results/v3eval_tv_on.json 2>&1 | tail -3; }
step_satrules() {
  for sw in $D_ON; do local t="v7_${sw%%=*}"; [ -n "$(latest tune $t)" ] && { echo "  $sw alone:"; stagerule v7off "$t"; }; done
  return 0
}

# ---- Mon 2 Nov (1 Nov rewrite): the switches in v7_lock.env (python v7_decide.py) together vs every v7 switch off.
# config.yaml on the server has no v7 switch lines, so the lock is passed explicitly (later values win).
lock() { grep -v '^#' v7_lock.env 2>/dev/null | tr '\n' ' '; }
locked() { [[ " $(lock) " == *" $1=1 "* ]]; }
evq() {                                            # evq <set> <tag> <env...>: eval_v3 with answers + judge
  local set=$1 tag=$2; shift 2
  env $EVAL_ENV $V7_OFF "$@" python eval_v3.py --questions "sets/$set.json" --tag "$tag" --answers | tail -14
}
step_lockshow() { [ -f v7_lock.env ] || { echo "  no v7_lock.env: run python v7_decide.py"; return 1; }
  echo "  v7_lock.env: $(lock)"; tail -n 4 results/v7_decisions.txt 2>/dev/null; return 0; }
step_allon() {
  [ -f v7_lock.env ] || { echo "  no v7_lock.env: run python v7_decide.py first"; return 1; }
  local L; L=$(lock); echo "  lock: $L"
  stage2 v7all $L
  evq analytic an_off5
  evq analytic an_all $L
  env $EVAL_ENV $V7_OFF $L python stage_scores.py --questions sets/multiturn.json --tag mt_all | tail -4
  if locked TV; then evq tv tv_all $L; fi
  if locked SHORTS; then evq shorts sh_all $L; fi
}
step_allonrules() {
  echo "  all locked switches together vs off, tune / check (off = Sunday night's v7off3):"; stagerule v7off3 v7all
  echo "  analytic (same-day off an_off5):"; anrule an_off5 an_all
  local a b; a=$(ls -t results/stage_scores_*_multiturn_mt_off3.json 2>/dev/null | head -1); b=$(ls -t results/stage_scores_*_multiturn_mt_all.json 2>/dev/null | head -1)
  echo "  multi-turn:"; [ -n "$a" ] && [ -n "$b" ] && python v7_rules.py stage "$a" "$b"
  [ -f results/v3eval_tv_all.json ] && { echo "  TV set:"; python v7_rules.py tv results/v3eval_tv_all.json; }
  [ -f results/v3eval_sh_all.json ] && { echo "  shorts set:"; python v7_rules.py shorts results/v3eval_sh_all.json results/v3eval_sh_off.json; }
  echo "  (a FAIL here: Monday night, re-run with one switch at a time removed from v7_lock.env)"
  return 0
}

# ---- Sun 1 Nov (day)
step_cardsfix() {                                  # reception_cards.py: RT % with an unknown review count left out
  python reception_cards.py build | tail -4
  python reception_cards.py gaps --side critics --limit 5 2>&1 | tail -5
  python reception_cards.py gaps --side audiences --limit 5 2>&1 | tail -5
}
step_tvreport()   { [ -f results/v3eval_tv_on2.json ] && python tv.py report results/v3eval_tv_on2.json results/v3eval_tv_off.json; }
step_routechecks() {
  echo "  TV routing after the 1 Nov film-title guard (a07, a21 should now be '-' on the new side):"
  python tv.py routecheck sets/analytic.json sets/tune.json sets/check.json sets/tv.json 2>&1 | head -70
  echo "  short-film routing (film sets should be ~0):"
  python shorts.py routecheck sets/tune.json sets/check.json sets/analytic.json sets/tv.json sets/shorts.json 2>&1 | head -40
}
step_shortscov()  { python shorts.py coverage; }
step_a11diff()    { python evaldiff.py results/v3eval_an_off2.json results/v3eval_an_alias2.json a11,a24; }
step_sameday() {
  evq analytic an_off3
  evq analytic an_alias3 ALIAS_IMDB=1
  evq analytic an_pr3 PEOPLE=1 REC_CLEAN=1
}
step_sunrules() {
  echo "  ALIAS_IMDB alone: tune / check (30 Oct):"; stagerule v7off v7alias2
  echo "  ALIAS_IMDB alone: analytic, same afternoon:"; anrule an_off3 an_alias3
  echo "  PEOPLE + REC_CLEAN: tune / check (30 Oct):"; stagerule v7off2 v7pr
  echo "  PEOPLE + REC_CLEAN: analytic, same afternoon:"; anrule an_off3 an_pr3
  return 0
}

# ---- Sun 1 Nov (night)
step_bgviews() {                                   # H4 pageviews stopped 29 Oct; network only, resumes
  ( echo "--- pageviews restart $(date +%H:%M)"; python fetch_v6_data.py pageviews --workers 1 2>&1 | tail -4
    echo "--- pageviews done $(date +%H:%M)" ) >> "$NETLOG" 2>&1 &
  echo "  pageviews in the background (pid $!, log $NETLOG)"
}
step_stagebase()  { stage2 v7off3; }
step_anbase() {
  evq analytic an_off4
  evq tv tv_off3
  evq shorts sh_off
  env $EVAL_ENV $V7_OFF python stage_scores.py --questions sets/multiturn.json --tag mt_off3 | tail -4
}
step_tvtest() {
  stage2 v7tv3 TV=1
  evq tv tv_on3 TV=1
  evq analytic an_tv3 TV=1
  python tv.py report results/v3eval_tv_on3.json results/v3eval_tv_off3.json | tail -45
}
step_shortstest() {
  python shorts.py coverage
  stage2 v7sh SHORTS=1
  evq shorts sh_on SHORTS=1
  evq analytic an_sh SHORTS=1
}
step_ctxsplit() {                                  # 30 Oct: together they failed; each alone now
  evq analytic an_cb CTX_BUILDER=1
  evq analytic an_po PROMPT_ORDER=1
  env $EVAL_ENV $V7_OFF CTX_BUILDER=1 python stage_scores.py --questions sets/multiturn.json --tag mt_cb | tail -4
  env $EVAL_ENV $V7_OFF PROMPT_ORDER=1 python stage_scores.py --questions sets/multiturn.json --tag mt_po | tail -4
}
step_sunnightrules() { python v7_decide.py; echo "  speed:"; tail -12 results/speed_ab.txt 2>/dev/null; return 0; }

# ---- Tue 3 Nov (day)
step_tests()       { python -m unittest discover -s tests 2>&1 | tail -6; }
step_sealedcheck() { python check_sealed_v7.py; }
step_freezecheck() {
  echo "  v7_lock.env: $(lock)"
  git status --short 2>/dev/null | head -20
  echo "  at 18:00: git add -A && git commit -m 'v7 lock' && git tag v7.0-lock ; tonight: bash run_final_v7.sh"
}

# ---- Tue 3 Nov: re-tests of the 3 Nov fixes. Base = the locked set (v7_lock.env as written 1 Nov); each arm adds one
# fixed switch to it; every arm and its base run the same day. Rules: v7_rules.py (3 Nov replacements for RECEPTION,
# SHORTS and MEM_EMBED, written before these results); decisions: python v7_decide.py --fix.
BASE_LOCK=${BASE_LOCK:-}
base() { if [ -n "$BASE_LOCK" ]; then echo "$BASE_LOCK"; elif [ -f v7_lock_base.env ]; then grep -v '^#' v7_lock_base.env | tr '\n' ' '; else lock; fi; }
step_fixtests() {
  [ -f v7_lock_base.env ] || cp v7_lock.env v7_lock_base.env       # the 1 Nov lock = the base for every 3 Nov arm
  echo "  base (locked set): $(base)"
  python -m unittest discover -s tests 2>&1 | tail -4
}
step_fixbase() {
  local B; B=$(base)
  stage2 lkbase $B
  evq analytic an_lk $B
  env $EVAL_ENV $V7_OFF $B python stage_scores.py --questions sets/multiturn.json --tag mt_lk | tail -4
  evq tv tv_lk $B
  evq shorts sh_lk $B
  evq reception rec_lk $B
}
step_fixtv() {
  local B; B=$(base)
  stage2 lktv $B TV=1
  evq tv tv_lk_tv $B TV=1
  evq analytic an_lk_tv $B TV=1
  python tv.py report results/v3eval_tv_lk_tv.json results/v3eval_tv_lk.json | tail -40
}
step_fixclar() {
  local B; B=$(base)
  evq tv tv_lk_cl $B TV=1 CLARIFY=1
  evq analytic an_lk_cl $B TV=1 CLARIFY=1
  env $EVAL_ENV $V7_OFF $B TV=1 CLARIFY=1 python clarify_check.py | tail -16
}
step_fixsh() {
  local B; B=$(base)
  stage2 lksh $B SHORTS=1
  evq shorts sh_lk_sh $B SHORTS=1
  evq analytic an_lk_sh $B SHORTS=1
}
step_fixcb() {
  local B; B=$(base)
  evq analytic an_lk_cb $B CTX_BUILDER=1
  env $EVAL_ENV $V7_OFF $B CTX_BUILDER=1 python stage_scores.py --questions sets/multiturn.json --tag mt_lk_cb | tail -4
}
step_fixpo() {
  local B; B=$(base)
  evq analytic an_lk_po $B PROMPT_ORDER=1
  env $EVAL_ENV $V7_OFF $B PROMPT_ORDER=1 python stage_scores.py --questions sets/multiturn.json --tag mt_lk_po | tail -4
}
step_fixrec() {
  local B; B=$(base)
  evq reception rec_lk_rec $B RECEPTION=1
  python claimcheck.py eval results/v3eval_rec_lk.json | tail -2
  python claimcheck.py eval results/v3eval_rec_lk_rec.json | tail -2
  python pairwise.py results/v3eval_rec_lk.json results/v3eval_rec_lk_rec.json --tag rec_lk | tail -3
  evq analytic an_lk_rec $B RECEPTION=1
}
step_fixmem()   { python memory_eval.py --pick | tail -14; }
step_fixrules() { python v7_decide.py --fix; }
step_fixall() {
  [ -f v7_lock.env ] || { echo "  no v7_lock.env"; return 1; }
  local L; L=$(lock); echo "  lock after the 3 Nov rules: $L"
  stage2 lkall $L
  evq analytic an_lkall $L
  env $EVAL_ENV $V7_OFF $L python stage_scores.py --questions sets/multiturn.json --tag mt_lkall | tail -4
  locked TV && evq tv tv_lkall $L
  locked SHORTS && evq shorts sh_lkall $L
  locked RECEPTION && evq reception rec_lkall $L
  return 0
}
step_fixallrules() {
  echo "  everything locked together vs the 1 Nov locked set (Tuesday's base arms):"; stagerule lkbase lkall
  echo "  analytic:"; anrule an_lk an_lkall
  local a b; a=$(ls -t results/stage_scores_*_multiturn_mt_lk.json 2>/dev/null | head -1); b=$(ls -t results/stage_scores_*_multiturn_mt_lkall.json 2>/dev/null | head -1)
  echo "  multi-turn:"; [ -n "$a" ] && [ -n "$b" ] && python v7_rules.py stage "$a" "$b"
  [ -f results/v3eval_tv_lkall.json ] && { echo "  TV set:"; python v7_rules.py tv results/v3eval_tv_lkall.json --off results/v3eval_tv_lk.json; }
  [ -f results/v3eval_sh_lkall.json ] && { echo "  shorts set:"; python v7_rules.py shorts2 results/v3eval_sh_lkall.json results/v3eval_sh_lk.json; }
  [ -f results/v3eval_rec_lkall.json ] && { echo "  reception set (passes):"; anrule rec_lk rec_lkall; }
  echo "  (a FAIL here: Wednesday morning, drop the switch whose own arm was weakest and re-run before the sealed run)"
  return 0
}

# ---- Wed 4 Nov: new data + new switches (ISSUES, YT_COMMENTS, TV_REVIEWS, PERF). Base = the lock as it is now.
step_featfetch() {                                 # network only, both at once; waits for both
  ( need MB_CONTACT && python v7_data.py themes --sleep 1.0 2>&1 | tail -4 ) > logs/feat_themes.log 2>&1 &
  local p1=$!
  ( need YOUTUBE_API_KEY && python youtube_analysis.py restats 2>&1 | tail -3 && \
    python youtube_analysis.py comments 2>&1 | tail -3 && python youtube_analysis.py stats ) > logs/feat_youtube.log 2>&1 &
  local p2=$!
  wait $p1 $p2
  echo "  themes:"; cat logs/feat_themes.log; echo "  youtube:"; cat logs/feat_youtube.log
  pyq "select kind, count(*), count(distinct tmdb_id) from wiki_themes group by kind" \
      "select 'comments', count(*), count(distinct video_key) from yt_analysis_comments"
}
step_featprep() {
  if [ "${SENT_YTC:-0}" = 1 ]; then                # only after the API project accepted the Analytics & Reporting use case
    SENT_SOURCES=ytc python sentiment.py run --sources ytc 2>&1 | tail -3
  else echo "  comment sentiment skipped (SENT_YTC=1 turns it on after the YouTube use-case acceptance)"; fi
  python evidence.py build 2>&1 | tail -4
}
step_featbase() {
  local L; L=$(lock); echo "  base (lock): $L"
  evq reception rec_fb $L
  evq issues is_fb $L
  evq success su_fb $L
  evq analytic an_fb $L
  locked TV && evq tv tv_fb $L
  return 0
}
step_featissues() {
  local L; L=$(lock)
  evq issues is_on $L ISSUES=1
  evq reception rec_is $L ISSUES=1
  evq analytic an_is $L ISSUES=1
  python pairwise.py results/v3eval_is_fb.json results/v3eval_is_on.json --tag is | tail -1
}
step_featyt() {
  local L; L=$(lock)
  evq reception rec_yt $L YT_COMMENTS=1
  evq analytic an_yt $L YT_COMMENTS=1
  python claimcheck.py eval results/v3eval_rec_yt.json | tail -1
  python pairwise.py results/v3eval_rec_fb.json results/v3eval_rec_yt.json --tag yt | tail -1
}
step_feattv() { local L; L=$(lock); locked TV && evq tv tv_tr $L TV_REVIEWS=1; return 0; }
step_featperf() {
  local L; L=$(lock)
  evq success su_pf $L PERF=1
  evq analytic an_pf $L PERF=1
  python pairwise.py results/v3eval_su_fb.json results/v3eval_su_pf.json --tag pf | tail -1
}
step_featrules() { python v7_decide.py --feat; }

step_summary() {
{
  echo "v7 night summary  $(date)"
  echo; echo "--- step results"
  for s in $STEPS; do [ "$s" = summary ] || echo "  $s: ${STATUS[$s]:-not run}"; done
  echo; echo "--- disk / GPU"; df -h . | tail -1; du -sh facts.db data 2>/dev/null
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || true
  echo; echo "--- critic statements (R2)"; python critics.py stats 2>&1
  echo; echo "--- sentiment (R3)"; python sentiment.py stats 2>&1
  echo; echo "--- flags (R5)"
  pyq "select 'bursts (rows, films, low = bombing):', count(*), count(distinct tmdb_id), sum(direction='low') from review_bursts" \
      "select 'flags (joke, in a burst, mismatch, irony scored):', sum(joke), sum(burst), sum(mismatch), count(irony_p) from review_flags"
  echo; echo "--- reception cards (R12, R13, E2, E4)"; python reception_cards.py stats 2>&1
  echo "  biggest critic-audience gaps:"; python reception_cards.py gaps --side critics --limit 5 2>&1 | tail -5
  python reception_cards.py gaps --side audiences --limit 5 2>&1 | tail -5
  for r in thurules frirules satrules allonrules sunrules sunnightrules fixrules fixallrules featrules; do
    if [[ " $STEPS " == *" $r "* ]]; then echo; echo "--- rules: $r"; "step_$r" 2>&1; fi
  done
  if [ -f results/v3eval_rec_on.json ]; then
    echo; echo "--- reception A/B (R15 rule) and analytic regression"; step_rules 2>&1
    echo; python evidence.py stats 2>&1; python evidence.py hit sets/reception.json 2>&1 | tail -1
  fi
  echo; echo "--- aspects (R4): films covered by source"
  pyq "select source_type, count(distinct tmdb_id), count(*) from film_aspects group by source_type" \
      "select 'films with a TMDB review:', count(distinct tmdb_id) from reviews"
  echo; python v7_data.py coverage 2>&1
  echo; echo "--- background network jobs (netbg)"; tail -20 "$NETLOG" 2>/dev/null
  echo; echo "--- files for you"; ls -la results/review_labels.csv results/label_reviews.html results/critic_outlet_check.csv results/reception_card_check.md results/video_match_check.csv results/judge_check.html 2>/dev/null
  echo; echo "--- jobs still running"; pgrep -af "v7_data|fetch_v6_data|reviews_analyze|fetch_free_data|sentiment.py|critics.py|youtube_analysis|ollama serve" | grep -v -e pgrep -e run_v7_night || echo "  none"
  echo; echo "--- check and rule results in tonight's log (RULE / PASS / FAIL lines)"
  tail -n +"$LOG_START" "$LOG" | grep -E "RULE|PASS|FAIL|rule[ :]|\(rule" | grep -v "^ *[0-9]*:" | tail -40 || true
  echo; echo "--- errors in tonight's log"; tail -n +"$LOG_START" "$LOG" | grep -nE "Traceback|Error|error:" | grep -v "^[0-9]*:[0-9]*:" | uniq -c -f1 | tail -15 || true
} > "$SUM" 2>&1
cat "$SUM"
}

for s in $STEPS; do
  if declare -F "step_$s" >/dev/null; then
    echo "=== step $s $(date +%H:%M)"
    t0=$(date +%s)
    if "step_$s"; then STATUS[$s]="ok ($(( ($(date +%s) - t0) / 60 )) min)"; else STATUS[$s]="FAILED or skipped (see log)"; fi
  else echo "unknown step: $s"; fi
done
echo "=== run_v7_night.sh finished $(date)"
