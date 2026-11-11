#!/usr/bin/env bash
# R17 (v7, 27 Oct): nightly updates - new releases and reviews in, YouTube data refreshed within 30 days, then only
# the changed rows rebuilt. Run by cron (below) from Mon 2 Nov; a dry run first: `bash nightly.sh --dry`.
#
#   one-time setup on the server:
#     cat > ~/.movie-buff.env <<'EOF'          # keys stay out of git
#     export TMDB_API_KEY=...  OMDB_API_KEY=...  GUARDIAN_API_KEY=...  NYT_API_KEY=...  YOUTUBE_API_KEY=...
#     export MB_CONTACT='you@example.edu'
#     EOF
#     chmod 600 ~/.movie-buff.env
#     crontab -e      # add the line:
#     17 4 * * * cd ~/movie-buff/rag && bash nightly.sh >> logs/nightly.log 2>&1
#
# Steps (each logs how many rows it touched; the test is that a second night touches only new rows):
#   fetch     TMDB now playing / upcoming / stale recent films (+ their first page of reviews); new films of this
#             year and last; OMDb daily batch; new Guardian and NYT reviews; Wikipedia reception text of pages whose
#             revision changed; YouTube: allow-listed channels' new videos, refresh of anything >= 25 days old,
#             top comments under matched videos (4 Nov); Sundays: Wikipedia themes / controversy sections (4 Nov)
#   purge     YouTube API policy: delete YouTube rows not refreshed within 30 days
#   rebuild   critic statements (changed pages only), sentiment (new or changed texts only), flags, aspects (new
#             reviews, <= 1 h), money table, reception cards, evidence index (changed units only), summaries
#             (changed inputs only, <= 1 h)
#   report    results/nightly_<date>.txt and the "touched" counts
# The search indexes (movies.db, wiki.db) are NOT changed at night: adding new films to search is add_recent.py's
# job and needs its own check (NIGHTLY_SEARCH=1 adds them, off by default).
set -u -o pipefail
cd "$(dirname "$0")"
[ -f ~/.movie-buff.env ] && source ~/.movie-buff.env
[ -f keys.env ] && { set -a; source keys.env; set +a; }     # round 27: GUARDIAN / FRED keys used by the sync rounds (git-ignored)
[ -f ~/venvs/movie-rag/bin/activate ] && source ~/venvs/movie-rag/bin/activate
DRY=0; [ "${1:-}" = "--dry" ] && DRY=1
DAY=$(date +%F)
REP=results/nightly_$DAY.txt
mkdir -p results logs
exec > >(tee -a "$REP") 2>&1
echo "=== nightly $DAY $(date +%H:%M)  dry=$DRY"
if [ "$DRY" = 0 ] && pgrep -f "run_v7_night.sh|run_final_v8|stage_scores|eval_v3" >/dev/null; then
  echo "an evaluation or night run is going: nightly update skipped tonight"; exit 0
fi
run() {                        # run "label" cmd... : time it, keep its last lines, never stop the night
  local label=$1; shift
  echo "--- $label ($(date +%H:%M))"
  if [ "$DRY" = 1 ]; then echo "  (dry) $*"; return 0; fi
  local t0=$(date +%s)
  "$@" 2>&1 | tail -6
  echo "  [$label: exit ${PIPESTATUS[0]}, $(( $(date +%s) - t0 ))s]"
}
YEAR=$(date +%Y)

# ---- fetch
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb current"  python fetch_tmdb.py current --pages 10
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb stale"    python fetch_tmdb.py refresh
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb new films" python fetch_tmdb.py extra --year-from $((YEAR - 1)) --min-votes 20 --pages 5
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb home extras (TV rows, artwork; logs/tmdb_extra.db)" python tools/fetch_home_extra.py --pages 10   # 10 Nov, round 18
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb episodes (recent + upcoming seasons)" python tools/fetch_episodes.py recent   # round 19
[ -n "${TMDB_API_KEY:-}" ] && run "tmdb film artwork, logos, US services (calendar window)" python tools/fetch_film_data.py window   # round 19
[ -n "${OMDB_API_KEY:-}" ] && run "omdb"          python fetch_free_data.py omdb
[ -n "${GUARDIAN_API_KEY:-}" ] && run "guardian"  python fetch_free_data.py guardian
# round 27 (11 Nov): pf5 B - Guardian articles before each film's release, ~400 a night after the reviews (the key
# allows 500 calls a day; the step stops by itself on HTTP 429). About 20 nights to cover the 2000-2015 training films.
[ -n "${GUARDIAN_API_KEY:-}" ] && run "guardian pre-release articles (pf5 B)" python pf5_data.py press --max "${PRESS_NIGHT:-400}"
# round 27: TVmaze schedule (one call) and the shows TVmaze changed in the last day (logs/tvmaze.db, website only)
[ -f logs/tvmaze.db ] && ! tmux has-session -t tvmaze 2>/dev/null && run "tvmaze schedule" python tools/fetch_tvmaze.py schedule
[ -f logs/tvmaze.db ] && ! tmux has-session -t tvmaze 2>/dev/null && run "tvmaze updates" python tools/fetch_tvmaze.py updates
[ -n "${NYT_API_KEY:-}" ] && run "nyt"            python fetch_free_data.py nyt --limit 60
run "wikipedia reception (changed pages)"         python v7_data.py reception --again
if [ -n "${YOUTUBE_API_KEY:-}" ]; then
  run "youtube channels: new videos"              python youtube_analysis.py fetch --max-pages 2
  run "youtube channels: refresh"                 python youtube_analysis.py refresh
  run "youtube comments (new / >= 25 days old)"   python youtube_analysis.py comments     # 4 Nov
  if [ "$(date +%u)" = 7 ]; then run "youtube trailers: weekly refresh" python fetch_free_data.py youtube --again; fi
fi
if [ "$(date +%u)" = 7 ]; then                   # 4 Nov: themes / controversy sections, pages whose revision changed
  run "wikipedia themes (weekly)"                 python v7_data.py themes
fi
if [ "${NIGHTLY_SEARCH:-0}" = 1 ]; then
  run "recent films: wiki"     python add_recent.py wiki
  run "recent films: catalog"  python add_recent.py catalog
fi

# ---- purge (YouTube API policy: refresh or delete within 30 days)
run "youtube purge" python - <<'PY'
import datetime, sqlite3, facts_db as F
c = F.connect()
cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat(timespec="seconds")
for t in ("yt_videos", "yt_comments", "yt_analysis", "yt_analysis_comments"):
    try:
        n = c.execute(f"delete from {t} where fetched_at < ?", (cut,)).rowcount
        print(f"  {t}: {n} rows older than 30 days deleted")
    except sqlite3.Error as e:
        print(f"  {t}: {e}")
c.commit()
PY

# ---- rebuild only what changed
run "critic statements"   python critics.py run
if [ "${SENT_YTC:-0}" = 1 ]; then                # 4 Nov: + YouTube comments, after the API use-case acceptance
  run "sentiment"         env SENT_SOURCES=tmdb,tv,critic,ytc python sentiment.py run --sources tmdb,tv,critic,ytc
else
  run "sentiment"         python sentiment.py run
fi
run "flags"               python sentiment.py flags
run "aspects (<= 1 h)"    timeout 1h python reviews_analyze.py --kinds audience,critic --films 40000
run "aspects aggregate"   python reviews_analyze.py aggregate
run "money table"         python tools.py build
run "youtube match"       python youtube_analysis.py match
run "reception cards"     python reception_cards.py build
run "evidence index"      python evidence.py build
run "summaries (<= 1 h)"  timeout 1h python reception_cards.py summaries --films 3000
run "reception cards"     python reception_cards.py build

# ---- report: what each stage touched (a second night should touch only new rows)
echo "--- touched tonight"
grep -E "to extract|texts to score|new or changed|summaries written|texts to analyse|new videos|pages to fetch|unchanged" "$REP" \
  | sed 's/^/  /' | tail -20
echo "=== nightly done $(date +%H:%M)"
