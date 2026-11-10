#!/usr/bin/env bash
# Round 19 data load (10 Nov 2026), run in tmux "data19" by todo.sh: TV refresh, every episode, film artwork / logos /
# US services, fresh IMDb datasets, then the Home TV sidecar. Writes facts.db (WAL: the site keeps reading meanwhile).
# Refuses while an evaluation runs. Full backup first when the disk has room (archive/facts_pre_r19.db).
set -u
cd ~/movie-buff/rag || exit 1
source ~/venvs/movie-rag/bin/activate 2>/dev/null
set -a; [ -f ~/.movie-buff.env ] && source ~/.movie-buff.env; [ -f keys.env ] && source keys.env; set +a
LOG=results/data_r19.txt
exec > >(tee -a $LOG) 2>&1
echo "=== data r19 $(date)"
if pgrep -f "eval_v3|stage_scores|run_final_v8|run_v8_tune|run_v7_night" >/dev/null; then echo "an evaluation is running: facts.db not touched"; exit 1; fi
[ -n "${TMDB_API_KEY:-}${TMDB_TOKEN:-}" ] || { echo "no TMDB key in ~/.movie-buff.env or keys.env"; exit 1; }
size=$(stat -c %s facts.db); free=$(df --output=avail -B1 . | tail -1)
echo "facts.db $((size/1048576)) MB, disk free $((free/1048576)) MB"
mkdir -p archive
if [ "$free" -gt $((size * 3)) ]; then
  python -c "import sqlite3; s=sqlite3.connect('file:facts.db?mode=ro', uri=True); d=sqlite3.connect('archive/facts_pre_r19.db'); s.backup(d); d.close(); print('backup: archive/facts_pre_r19.db')"
else
  echo "not enough room for a full copy: backing up the tables this load rewrites"
  python - <<'PY'
import sqlite3
s = sqlite3.connect("file:facts.db?mode=ro", uri=True); d = sqlite3.connect("archive/facts_pre_r19_tables.db")
for (t,) in s.execute("select name from sqlite_master where type='table' and (name like 'tv\\_%' escape '\\' or name in ('film_images','watch_providers','imdb_ratings','imdb_episodes'))"):
    sql = s.execute("select sql from sqlite_master where name = ?", (t,)).fetchone()[0]
    d.execute(f"drop table if exists {t}"); d.execute(sql)
    rows = s.execute(f"select * from {t}")
    while True:
        b = rows.fetchmany(50000)
        if not b: break
        d.executemany(f"insert into {t} values ({','.join('?' * len(b[0]))})", b)
    d.commit(); print("  backed up", t)
PY
fi
step() { echo "--- $1 ($(date +%H:%M))"; shift; local t0=$(date +%s); "$@" 2>&1 | grep -v "^\s*$" | tail -12; echo "  [exit ${PIPESTATUS[0]}, $(( $(date +%s) - t0 ))s]"; }
step "TV shows + seasons refresh (TMDB, up to 8000 shows)" python v7_data.py tv --again --workers 8
step "episodes (every season)" python tools/fetch_episodes.py all --max-requests 60000
step "film artwork, logos, US services: calendar window" python tools/fetch_film_data.py window
step "film posters: catalogue films with none" python tools/fetch_film_data.py catalog --max 6000
step "IMDb datasets (films, shows, episodes)" python v7_data.py imdb
step "episodes: status with IMDb join" python tools/fetch_episodes.py status
step "Home TV sidecar" python tools/fetch_home_extra.py --pages 10
echo "=== data r19 done $(date)"
