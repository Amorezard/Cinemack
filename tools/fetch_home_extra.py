"""Fetch what Home's TV rows and the Release Calendar need from TMDB into logs/tmdb_extra.db (10 Nov 2026, round 18).

Read-only on facts.db (the v8 experiment's fact store is never written). Writes a fresh snapshot to a temporary file and
swaps it in only when the fetch looks complete, so a failed or partial run leaves the previous snapshot in place.

    TMDB_API_KEY=... python tools/fetch_home_extra.py [--pages 10] [--dry]

  1. TV top rated     /tv/top_rated, `--pages` pages (20 shows each): rating, votes, genres, poster
  2. New seasons      /discover/tv with an episode aired in the last 35 days (20+ votes, most popular first, 5 pages),
                      plus facts.db shows with a season dated in that window; /tv/{id} for each: seasons with dates
  3. Film artwork     /movie/{id} for films on the US now-playing / upcoming lists or with a US release in the
                      calendar's window (-40 .. +75 days) that facts.db has no poster or runtime for (at most 300)
The key is read from the environment and never printed; a v4 read token (long) is sent as a Bearer header.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG / "facts.db"))
OUT = os.environ.get("TMDB_EXTRA_DB", str(RAG / "logs" / "tmdb_extra.db"))
API = "https://api.themoviedb.org/3"
TV_GENRE = {10759: ("Action & Adventure", ["Action", "Adventure"]), 16: ("Animation", ["Animation"]),
            35: ("Comedy", ["Comedy"]), 80: ("Crime", ["Crime"]), 99: ("Documentary", ["Documentary"]),
            18: ("Drama", ["Drama"]), 10751: ("Family", ["Family"]), 10762: ("Kids", ["Family"]),
            9648: ("Mystery", ["Mystery"]), 10763: ("News", []), 10764: ("Reality", []),
            10765: ("Sci-Fi & Fantasy", ["Science Fiction", "Fantasy"]), 10766: ("Soap", []), 10767: ("Talk", []),
            10768: ("War & Politics", ["War"]), 37: ("Western", ["Western"])}
KEY = os.environ.get("TMDB_API_KEY", "").strip()
N = {"requests": 0, "errors": 0}


def get(path, **params):
    if not KEY:
        raise SystemExit("TMDB_API_KEY is not set")
    headers = {"Accept": "application/json", "User-Agent": "cinemack-home-extra/1"}
    if len(KEY) > 40:
        headers["Authorization"] = f"Bearer {KEY}"
    else:
        params["api_key"] = KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        N["requests"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                time.sleep(0.03)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After") or 2))
                continue
            if e.code == 404:
                return None
            N["errors"] += 1
            print(f"  {path}: HTTP {e.code}", flush=True)       # the URL holds the key: never print it
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            N["errors"] += 1
            print(f"  {path}: {type(e).__name__}", flush=True)
            time.sleep(1 + attempt)
    return None


def genres(ids):
    names, mapped = [], []
    for g in ids:
        n, m = TV_GENRE.get(g, (None, []))
        if n:
            names.append(n)
        mapped += [x for x in m if x not in mapped]
    return names, mapped


def show_row(x, rank=None):
    gid = x.get("genre_ids") or [g.get("id") for g in x.get("genres") or []]
    names, mapped = genres(gid)
    year = (x.get("first_air_date") or "")[:4]
    nets = [n.get("name") for n in x.get("networks") or [] if n.get("name")]
    return (x["id"], x.get("name") or x.get("original_name"), int(year) if year.isdigit() else None, x.get("vote_average"),
            x.get("vote_count") or 0, x.get("popularity"), json.dumps(mapped), json.dumps(names), x.get("poster_path"),
            x.get("backdrop_path"), json.dumps(nets), x.get("original_language"), rank)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    f = sqlite3.connect(f"file:{FACTS}?mode=ro", uri=True)
    shows, seasons, art = {}, {}, {}

    # 1. top rated TV
    rank = 0
    for page in range(1, a.pages + 1):
        j = get("/tv/top_rated", language="en-US", page=page)
        for x in (j or {}).get("results") or []:
            rank += 1
            shows[x["id"]] = show_row(x, rank)
    n_top = rank
    print(f"top rated TV: {n_top} shows", flush=True)

    # 2. shows with a season that premiered recently
    a35, z = (today - dt.timedelta(days=35)).isoformat(), today.isoformat()
    cand = []
    for page in range(1, 6):
        j = get("/discover/tv", language="en-US", sort_by="popularity.desc", page=page, **{"air_date.gte": a35, "air_date.lte": z,
                "vote_count.gte": 20, "without_genres": "10763,10767,10766"})
        cand += [x["id"] for x in (j or {}).get("results") or []]
    cand += [r[0] for r in f.execute("select distinct tmdb_id from tv_seasons where season_number > 0 and "
                                     "substr(air_date, 1, 10) between ? and ?", (a35, z))]
    cand = list(dict.fromkeys(cand))[:200]
    for sid in cand:
        d = get(f"/tv/{sid}", language="en-US")
        if not d:
            continue
        keep_rank = shows[sid][-1] if sid in shows else None
        shows[sid] = show_row(d, keep_rank)
        for s in d.get("seasons") or []:
            if s.get("season_number"):
                seasons[(sid, s["season_number"])] = (sid, s["season_number"], s.get("air_date"), s.get("episode_count"), s.get("poster_path"))
    recent = sum(1 for (_s, _n), v in seasons.items() if v[2] and a35 <= v[2][:10] <= z)
    print(f"new-season candidates: {len(cand)} shows, {recent} seasons premiered in the last 35 days", flush=True)

    # 3. film artwork / runtime gaps
    lo, hi = (today - dt.timedelta(days=40)).isoformat(), (today + dt.timedelta(days=75)).isoformat()
    ids = [r[0] for r in f.execute("select distinct tmdb_id from lists where region = 'US' and list_name in ('now_playing', 'upcoming') "
                                   "and fetched_at = (select max(fetched_at) from lists l2 where l2.list_name = lists.list_name and l2.region = 'US')")]
    ids += [r[0] for r in f.execute("select distinct tmdb_id from certifications where region = 'US' and release_type in (2, 3, 4) "
                                    "and substr(release_date, 1, 10) between ? and ?", (lo, hi))]
    ids = list(dict.fromkeys(ids))
    have_p = {r[0] for r in f.execute("select tmdb_id from film_images where poster_path is not null and tmdb_id in (%s)" % (",".join(map(str, ids)) or "0"))}
    have_r = {r[0] for r in f.execute("select tmdb_id from films where runtime > 0 and tmdb_id in (%s)" % (",".join(map(str, ids)) or "0"))}
    gaps = [i for i in ids if i not in have_p or i not in have_r][:300]
    for fid in gaps:
        d = get(f"/movie/{fid}", language="en-US")
        if d:
            art[fid] = (fid, d.get("poster_path"), d.get("backdrop_path"), d.get("runtime") or None)
    print(f"films: {len(ids)} listed or dated, {len(gaps)} missing a poster or runtime, {sum(1 for v in art.values() if v[1])} posters "
          f"and {sum(1 for v in art.values() if v[3])} runtimes found", flush=True)
    print(f"requests: {N['requests']}, errors: {N['errors']}", flush=True)

    if n_top < 40:
        print("too few top-rated shows came back: the previous snapshot is kept", flush=True)
        return 1
    if a.dry:
        print("dry run: nothing written", flush=True)
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""
create table meta(key text primary key, value text);
create table tv_show(tmdb_id integer primary key, name text, first_year integer, vote_average real, vote_count integer,
  popularity real, genres_json text, tv_genres_json text, poster_path text, backdrop_path text, networks_json text,
  original_language text, top_rank integer);
create table tv_season(tmdb_id integer, season_number integer, air_date text, episode_count integer, poster_path text,
  primary key (tmdb_id, season_number));
create index tv_season_date on tv_season(air_date);
create table film_art(tmdb_id integer primary key, poster_path text, backdrop_path text, runtime integer);
""")
    c.executemany("insert into tv_show values (?,?,?,?,?,?,?,?,?,?,?,?,?)", shows.values())
    c.executemany("insert into tv_season values (?,?,?,?,?)", seasons.values())
    c.executemany("insert into film_art values (?,?,?,?)", art.values())
    c.executemany("insert into meta values (?,?)", [("fetched_at", time.strftime("%Y-%m-%dT%H:%M:%S")), ("top_rated", str(n_top)),
                                                     ("shows", str(len(shows))), ("seasons", str(len(seasons))), ("films", str(len(art)))])
    c.commit()
    c.close()
    os.replace(tmp, OUT)
    print(f"wrote {OUT}: {len(shows)} shows, {len(seasons)} seasons, {len(art)} films", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
