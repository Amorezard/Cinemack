"""TVmaze for the website (11 Nov 2026, round 25; install AFTER the v8 sealed run).

TVmaze's public API is free, needs no key, and is licensed CC BY-SA 4.0 (https://www.tvmaze.com/api): any use with
credit + a link to TVmaze, and what we pass on from it carries the same licence. Rate limit: at least 20 calls / 10 s
per IP; we stay at 1 call / 0.6 s and back off on 429.

Everything goes into a separate file, logs/tvmaze.db, which only the website reads (never facts.db, the answer
pipeline or the evaluations). facts.db is opened read-only, only to list the shows we know (tmdb_id, imdb_id, tvdb_id).

    python tools/fetch_tvmaze.py map        [--max N]   match our shows to TVmaze ids (IMDb id, then TheTVDB id)
    python tools/fetch_tvmaze.py episodes   [--max N]   every episode of matched shows (running shows first)
    python tools/fetch_tvmaze.py schedule               all future episodes TVmaze knows (one call, /schedule/full)
    python tools/fetch_tvmaze.py updates                re-fetch shows TVmaze changed in the last day (nightly)
    python tools/fetch_tvmaze.py status

Tables (logs/tvmaze.db):
  meta(key, value)
  show(tvmaze_id PK, tmdb_id, imdb_id, name, status, premiered, ended, network, web_channel, country, timezone,
       air_time, air_days, runtime, rating, image, summary, url, official_site, updated, fetched_at)
  map_done(tmdb_id PK, tvmaze_id, how, fetched_at)        how = imdb | tvdb | none
  episode(ep_id PK, tvmaze_id, season, number, name, type, airdate, airtime, airstamp, runtime, rating, image,
          summary, url)
  episodes_done(tvmaze_id PK, n, show_updated, fetched_at)
  schedule(ep_id PK, tvmaze_id, season, number, name, airdate, airtime, airstamp, runtime, web)  (replaced each run)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG / "facts.db"))
DB = os.environ.get("TVMAZE_DB", str(RAG / "logs" / "tvmaze.db"))
API = os.environ.get("TVMAZE_API", "https://api.tvmaze.com")
GAP = float(os.environ.get("TVMAZE_GAP", "0.6"))
UA = "cinemack-research/1 (university project; data CC BY-SA TVmaze)"
STATS = {"requests": 0, "not_found": 0, "errors": 0}
DDL = """
create table if not exists meta(key text primary key, value text);
create table if not exists show(tvmaze_id integer primary key, tmdb_id integer, imdb_id text, name text, status text,
  premiered text, ended text, network text, web_channel text, country text, timezone text, air_time text,
  air_days text, runtime integer, rating real, image text, summary text, url text, official_site text,
  updated integer, fetched_at text);
create index if not exists show_tmdb on show(tmdb_id);
create table if not exists map_done(tmdb_id integer primary key, tvmaze_id integer, how text, fetched_at text);
create table if not exists episode(ep_id integer primary key, tvmaze_id integer, season integer, number integer,
  name text, type text, airdate text, airtime text, airstamp text, runtime integer, rating real, image text,
  summary text, url text);
create index if not exists episode_show on episode(tvmaze_id, season, number);
create index if not exists episode_date on episode(airdate);
create table if not exists episodes_done(tvmaze_id integer primary key, n integer, show_updated integer, fetched_at text);
create table if not exists schedule(ep_id integer primary key, tvmaze_id integer, season integer, number integer,
  name text, airdate text, airtime text, airstamp text, runtime integer, web integer);
create index if not exists schedule_date on schedule(airdate);
"""
_last = [0.0]


def get(path):
    """JSON or None (404). Raises nothing: a failure after retries returns False."""
    for attempt in range(6):
        wait = GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        STATS["requests"] += 1
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:          # follows the 301 of /lookup/shows
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                STATS["not_found"] += 1
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(2 + 3 * attempt)
                continue
            STATS["errors"] += 1
            print(f"  {path.split('?')[0]}: HTTP {e.code}", flush=True)
            return False
        except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError):
            time.sleep(2 + 3 * attempt)
    STATS["errors"] += 1
    print(f"  {path.split('?')[0]}: gave up", flush=True)
    return False


def text(s, n=600):
    """TVmaze summaries are HTML: tags out, entities decoded, clipped."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def img(o):
    o = o or {}
    return o.get("medium") or o.get("original")


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=60)
    c.execute("pragma journal_mode=wal")
    c.executescript(DDL)
    return c


def facts_shows():
    """(tmdb_id, imdb_id, tvdb_id) of our shows, in production first, then by TMDB votes. Read-only."""
    f = sqlite3.connect(f"file:{FACTS}?mode=ro", uri=True)
    cols = {r[1] for r in f.execute("pragma table_info(tv_shows)")}
    order = []
    if "in_production" in cols:
        order.append("coalesce(in_production, 0) desc")
    if "status" in cols:
        order.append("case when status in ('Returning Series', 'In Production') then 0 else 1 end")
    if "vote_count" in cols:
        order.append("coalesce(vote_count, 0) desc")
    tv = "tvdb_id" if "tvdb_id" in cols else "null"
    rows = f.execute(f"select tmdb_id, imdb_id, {tv} from tv_shows" + (" order by " + ", ".join(order) if order else "")).fetchall()
    f.close()
    return rows


def show_row(s, tmdb_id, imdb_id):
    net, web = s.get("network") or {}, s.get("webChannel") or {}
    country = ((net.get("country") or web.get("country") or {}) or {})
    sch = s.get("schedule") or {}
    return (s["id"], tmdb_id, imdb_id or (s.get("externals") or {}).get("imdb"), s.get("name"), s.get("status"),
            s.get("premiered"), s.get("ended"), net.get("name"), web.get("name"), country.get("code"),
            country.get("timezone"), sch.get("time") or None, ",".join(sch.get("days") or []) or None,
            s.get("runtime") or s.get("averageRuntime"), (s.get("rating") or {}).get("average"), img(s.get("image")),
            text(s.get("summary")), s.get("url"), s.get("officialSite"), s.get("updated"),
            dt.datetime.now().isoformat(timespec="seconds"))


def save_show(c, s, tmdb_id, imdb_id):
    c.execute("insert or replace into show values (" + ",".join("?" * 21) + ")", show_row(s, tmdb_id, imdb_id))


def cmd_map(c, a):
    done = {r[0] for r in c.execute("select tmdb_id from map_done")}
    todo = [r for r in facts_shows() if r[0] not in done and (r[1] or r[2])]
    if a.max:
        todo = todo[:a.max]
    print(f"map: {len(todo)} shows to look up ({len(done)} already done)", flush=True)
    n_ok = 0
    for i, (tmdb_id, imdb_id, tvdb_id) in enumerate(todo, 1):
        s, how = None, "none"
        if imdb_id:
            s = get(f"/lookup/shows?imdb={imdb_id}")
            how = "imdb" if s else how
        if not s and tvdb_id:
            s = get(f"/lookup/shows?thetvdb={int(tvdb_id)}")
            how = "tvdb" if s else how
        if s is False:
            continue                                       # network trouble: retried on the next run
        if s:
            save_show(c, s, tmdb_id, imdb_id)
            n_ok += 1
        c.execute("insert or replace into map_done values (?,?,?,?)", (tmdb_id, s["id"] if s else None, how,
                                                                      dt.date.today().isoformat()))
        if i % 200 == 0:
            c.commit()
            print(f"  {i}/{len(todo)}  matched {n_ok}  ({STATS['requests']} requests)", flush=True)
    c.commit()
    print(f"map: matched {n_ok} of {len(todo)}", flush=True)


def save_episodes(c, sid, eps):
    c.execute("delete from episode where tvmaze_id = ?", (sid,))
    c.executemany("insert or replace into episode values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [(e["id"], sid, e.get("season"), e.get("number"), e.get("name"), e.get("type"), e.get("airdate") or None,
                    e.get("airtime") or None, e.get("airstamp"), e.get("runtime"), (e.get("rating") or {}).get("average"),
                    img(e.get("image")), text(e.get("summary"), 400), e.get("url")) for e in eps])


def cmd_episodes(c, a):
    rows = c.execute("select s.tvmaze_id, s.updated from show s left join episodes_done d on d.tvmaze_id = s.tvmaze_id "
                     "where d.tvmaze_id is null or d.show_updated is null or d.show_updated < s.updated "
                     "order by case s.status when 'Running' then 0 when 'In Development' then 1 when 'To Be Determined' then 2 else 3 end, "
                     "coalesce(s.rating, 0) desc").fetchall()
    if a.max:
        rows = rows[:a.max]
    print(f"episodes: {len(rows)} shows", flush=True)
    n_eps = 0
    for i, (sid, upd) in enumerate(rows, 1):
        eps = get(f"/shows/{sid}/episodes?specials=1")
        if eps is False:
            continue
        eps = eps or []
        save_episodes(c, sid, eps)
        n_eps += len(eps)
        c.execute("insert or replace into episodes_done values (?,?,?,?)", (sid, len(eps), upd, dt.date.today().isoformat()))
        if i % 200 == 0:
            c.commit()
            print(f"  {i}/{len(rows)}  episodes {n_eps}", flush=True)
    c.commit()
    print(f"episodes: {n_eps} episodes from {len(rows)} shows", flush=True)


def cmd_schedule(c, a):
    full = get("/schedule/full")
    if not full:
        print("schedule: no data (kept the previous one)")
        return
    ours = {r[0] for r in c.execute("select tvmaze_id from show")}
    rows = []
    for e in full:
        sid = ((e.get("_embedded") or {}).get("show") or {}).get("id")
        if sid in ours and e.get("airdate"):
            web = 1 if ((e["_embedded"]["show"].get("webChannel")) and not e["_embedded"]["show"].get("network")) else 0
            rows.append((e["id"], sid, e.get("season"), e.get("number"), e.get("name"), e.get("airdate"),
                         e.get("airtime") or None, e.get("airstamp"), e.get("runtime"), web))
    with c:
        c.execute("delete from schedule")
        c.executemany("insert or replace into schedule values (?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("insert or replace into meta values ('schedule_at', ?)", (dt.datetime.now().isoformat(timespec="seconds"),))
    print(f"schedule: {len(full)} future episodes on TVmaze, {len(rows)} for our shows", flush=True)


def cmd_updates(c, a):
    ch = get("/updates/shows?since=day")
    if not ch:
        print("updates: none")
        return
    known = {r[0]: (r[1], r[2], r[3]) for r in c.execute("select tvmaze_id, tmdb_id, imdb_id, updated from show")}
    todo = [int(k) for k, ts in ch.items() if int(k) in known and (known[int(k)][2] or 0) < ts]
    print(f"updates: {len(ch)} changed on TVmaze, {len(todo)} of ours", flush=True)
    for sid in todo:
        s = get(f"/shows/{sid}")
        if s:
            save_show(c, s, known[sid][0], known[sid][1])
    c.commit()
    cmd_episodes(c, argparse.Namespace(max=0))


def cmd_status(c, a):
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    n = {k: q(s) for k, s in {
        "looked": "select count(*) from map_done", "matched": "select count(*) from show",
        "imdb": "select count(*) from map_done where how = 'imdb'", "tvdb": "select count(*) from map_done where how = 'tvdb'",
        "eps": "select count(*) from episode", "eshows": "select count(*) from episodes_done",
        "summ": "select count(*) from episode where summary != ''", "time": "select count(*) from episode where airtime is not null",
        "sched": "select count(*) from schedule", "sched_at": "select max(value) from meta where key = 'schedule_at'"}.items()}
    print(f"tvmaze.db {DB}")
    print(f"  looked up {n['looked']}, matched {n['matched']} (imdb {n['imdb']}, tvdb {n['tvdb']})")
    print(f"  episodes {n['eps']} from {n['eshows']} shows; with summary {n['summ']}, with air time {n['time']}")
    print(f"  schedule {n['sched']} future episodes (fetched {n['sched_at']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["map", "episodes", "schedule", "updates", "status"])
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    c = db()
    t0 = time.time()
    {"map": cmd_map, "episodes": cmd_episodes, "schedule": cmd_schedule, "updates": cmd_updates, "status": cmd_status}[a.cmd](c, a)
    if a.cmd != "status":
        with c:
            c.execute("insert or replace into meta values ('fetched_at', ?)", (dt.datetime.now().isoformat(timespec="seconds"),))
        print(f"{a.cmd}: {STATS} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
