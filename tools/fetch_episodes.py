"""Every episode of the shows in facts.db (10 Nov 2026, round 19): TMDB /tv/{id}/season/{n} -> facts.db tv_episodes.

    python tools/fetch_episodes.py all       [--max-requests 40000] [--workers 8]   first run: every season of every show
    python tools/fetch_episodes.py recent    seasons that aired in the last 150 days, are dated later, or are undated
                                             seasons of shows still in production (nightly)
    python tools/fetch_episodes.py status

Order: shows in production first, then by TMDB votes, so a capped run covers what people watch now. A finished season
(aired over a year ago and already fetched) is never asked again; recent and future seasons are refreshed. Specials
(season 0) are skipped. Per episode: air date, title, synopsis, runtime, still image, TMDB rating and votes, and
TMDB's episode type (standard / finale / mid_season). IMDb episode ratings are already in facts.db (imdb_episodes +
imdb_ratings, keyed by the show's IMDb id and season/episode numbers) and are joined when read, not copied.
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmdb_http as T  # noqa: E402

RAG = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG / "facts.db"))
DDL = """
create table if not exists tv_episodes(tmdb_id integer, season_number integer, episode_number integer, name text,
  air_date text, overview text, runtime integer, still_path text, vote_average real, vote_count integer,
  episode_type text, fetched_at text, primary key (tmdb_id, season_number, episode_number));
create index if not exists tv_episodes_date on tv_episodes(air_date);
create table if not exists tv_episodes_done(tmdb_id integer, season_number integer, n integer, fetched_at text,
  primary key (tmdb_id, season_number));
"""


def plan(conn, mode):
    today = dt.date.today()
    old = (today - dt.timedelta(days=365)).isoformat()
    recent = (today - dt.timedelta(days=150)).isoformat()
    cols = set(T.columns(conn, "tv_shows"))
    order = []
    if "in_production" in cols:
        order.append("coalesce(t.in_production, 0) desc")
    order.append("coalesce(t.vote_count, 0) desc, t.tmdb_id")
    rows = conn.execute(f"select s.tmdb_id, s.season_number, s.air_date, d.fetched_at, {'t.in_production' if 'in_production' in cols else '0'} "
                        f"from tv_seasons s join tv_shows t on t.tmdb_id = s.tmdb_id left join tv_episodes_done d "
                        f"on d.tmdb_id = s.tmdb_id and d.season_number = s.season_number where s.season_number > 0 "
                        f"order by {', '.join(order)}, s.season_number").fetchall()
    out = []
    for sid, sn, air, done, prod in rows:
        a = (air or "")[:10]
        live = (not a and prod) or (a and a >= recent)
        if mode == "recent":
            if live:
                out.append((sid, sn))
        elif not done or live or (a and a >= old and done[:10] < (today - dt.timedelta(days=7)).isoformat()):
            out.append((sid, sn))
    return out


def fetch(sid, sn):
    d = T.get(f"/tv/{sid}/season/{sn}", language="en-US")
    if d is T.FAILED:
        return sid, sn, None
    eps = []
    for e in (d or {}).get("episodes") or []:
        if e.get("episode_number") is None:
            continue
        eps.append({"tmdb_id": sid, "season_number": sn, "episode_number": e["episode_number"], "name": e.get("name"),
                    "air_date": e.get("air_date") or None, "overview": e.get("overview") or None, "runtime": e.get("runtime"),
                    "still_path": e.get("still_path"), "vote_average": e.get("vote_average"), "vote_count": e.get("vote_count"),
                    "episode_type": e.get("episode_type")})
    return sid, sn, eps


def run(conn, todo, workers, cap):
    todo = todo[:cap]
    print(f"seasons to fetch: {len(todo):,}", flush=True)
    t0, n_eps, n_done, fail = time.time(), 0, 0, 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fetch, s, n) for s, n in todo]
        buf = []
        for i, f in enumerate(as_completed(futs), 1):
            sid, sn, eps = f.result()
            if eps is None:
                fail += 1
            else:
                buf.append((sid, sn, eps))
            if len(buf) >= 200 or i == len(futs):
                ts = time.strftime("%Y-%m-%dT%H:%M:%S")
                for s, n, es in buf:
                    conn.execute("delete from tv_episodes where tmdb_id = ? and season_number = ?", (s, n))
                    for e in es:
                        e["fetched_at"] = ts
                        T.insert(conn, "tv_episodes", e)
                    conn.execute("insert or replace into tv_episodes_done values (?,?,?,?)", (s, n, len(es), ts))
                    n_eps += len(es)
                    n_done += 1
                conn.commit()
                buf = []
            if i % 2000 == 0:
                print(f"  {i:,}/{len(futs):,} seasons, {n_eps:,} episodes, {time.time() - t0:.0f}s", flush=True)
    print(f"done: {n_done:,} seasons, {n_eps:,} episodes, {fail} failed (retried next run), {time.time() - t0:.0f}s; "
          f"requests {T.STATS['requests']:,}, errors {T.STATS['errors']}", flush=True)


def status(conn):
    today = dt.date.today().isoformat()
    q = lambda s, p=(): conn.execute(s, p).fetchone()[0]  # noqa: E731
    print("episodes:", f"{q('select count(*) from tv_episodes'):,}", "| shows:", q("select count(distinct tmdb_id) from tv_episodes"),
          "| seasons done:", f"{q('select count(*) from tv_episodes_done'):,}", "of", f"{q('select count(*) from tv_seasons where season_number > 0'):,}")
    print("dated:", f"{q('select count(*) from tv_episodes where air_date is not null'):,}",
          "| airing in the next 14 days:", q("select count(*) from tv_episodes where air_date between ? and date(?, '+14 day')", (today, today)),
          "| with a still:", f"{q('select count(*) from tv_episodes where still_path is not null'):,}")
    try:
        n = q("select count(*) from tv_episodes e join tv_shows t on t.tmdb_id = e.tmdb_id join imdb_episodes i on i.parent = t.imdb_id "
              "and i.season = e.season_number and i.episode = e.episode_number join imdb_ratings r on r.imdb_id = i.tconst")
        print("episodes with an IMDb rating:", f"{n:,}")
    except sqlite3.Error as e:
        print("IMDb join not available:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["all", "recent", "status"])
    ap.add_argument("--max-requests", type=int, default=40000)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    conn = sqlite3.connect(FACTS, timeout=60)
    conn.execute("pragma journal_mode=WAL")
    conn.executescript(DDL)
    if a.cmd != "status":
        run(conn, plan(conn, a.cmd), a.workers, a.max_requests)
    status(conn)


if __name__ == "__main__":
    main()
