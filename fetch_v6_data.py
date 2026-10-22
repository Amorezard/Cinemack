#!/usr/bin/env python3
"""
v6 data (20 Oct): posters and cast photos, IMDb ratings, and Wikipedia pageviews, all into facts.db.
movies.db and wiki.db are not changed. Network only, no GPU: safe while the site is running.

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    export TMDB_API_KEY=...                 # same key fetch_tmdb.py uses
    python fetch_v6_data.py images --limit 20    # smoke test
    python fetch_v6_data.py images               # every film in facts.db + movies.db (~32k requests, ~40 min)
    python fetch_v6_data.py imdb                 # IMDb non-commercial title.ratings.tsv.gz (~8 MB), joined on imdb_id
    export MB_CONTACT='your email or project URL'  # Wikimedia throttles anonymous clients (HTTP 429)
    python fetch_v6_data.py pageviews            # Wikipedia views in the 28 days BEFORE release, films released
                                                 # since Aug 2015 (~8-10k requests, ~20-30 min)
    python fetch_v6_data.py coverage             # how many films have each signal

Tables it writes (facts.db):
    film_images(tmdb_id, poster_path, backdrop_path, fetched_at)     TMDB image paths; the browser loads
    cast_images(tmdb_id, person_id, name, character, ord, profile_path)  them from image.tmdb.org, so no
                                                                     image is stored on the server
    imdb_ratings(imdb_id, rating, votes, fetched_at)                 IMDb non-commercial dataset
    pageviews(tmdb_id, article, release_date, pv_pre28, pv_pre7, days, fetched_at)
                                                                     pre-release attention only: nothing
                                                                     from release day on is stored, so the
                                                                     predictor cannot leak post-release data

Terms: TMDB API free for non-commercial use with credit (the site's About page). IMDb datasets:
personal and non-commercial use only. Wikimedia REST API: free, descriptive User-Agent, <100 req/s.

Resumable: `images` and `pageviews` skip films already done (use --again to redo).
"""
import argparse
import csv
import datetime
import gzip
import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import facts_db as F

HERE = os.path.dirname(os.path.abspath(__file__))
# Wikimedia throttles (HTTP 429) clients whose User-Agent has no way to contact the owner, so put
# your email or a project URL in MB_CONTACT:  export MB_CONTACT='you@example.edu'
CONTACT = os.environ.get("MB_CONTACT", "").strip()
UA = ("MovieBuff/6 (student research project, Merrimack College" + (f"; {CONTACT}" if CONTACT else "")
      + ") python-urllib")
IMDB_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
PV_API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
          "{article}/daily/{start}/{end}")
PV_FIRST_DAY = datetime.date(2015, 7, 1)      # the pageviews API starts here
MAX_CAST = 8

DDL = """
create table if not exists film_images(tmdb_id integer primary key, poster_path text, backdrop_path text,
  fetched_at text);
create table if not exists cast_images(tmdb_id integer, person_id integer, name text, character text,
  ord integer, profile_path text, primary key (tmdb_id, person_id));
create table if not exists imdb_ratings(imdb_id text primary key, rating real, votes integer, fetched_at text);
create table if not exists pageviews(tmdb_id integer primary key, article text, release_date text,
  pv_pre28 integer, pv_pre7 integer, days integer, fetched_at text);
"""

_lock = threading.Lock()
FAILED = object()          # http_json gave up (network); distinct from None = 404


def connect(path=None):
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


_SHOWN = {"n": 0}


def http_json(url, headers=None, retries=6):
    """GET -> parsed JSON, None on 404, FAILED after repeated failures."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                                       **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 401:
                raise SystemExit(f"401 from {url.split('?')[0]}: check TMDB_API_KEY")
            if _SHOWN["n"] < 2:                 # say once or twice what the server objected to
                _SHOWN["n"] += 1
                try:
                    body = e.read()[:300].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    body = ""
                print(f"  HTTP {e.code} from {url.split('?')[0][:90]}: {body}", flush=True)
            if e.code == 429:
                try:
                    wait = float(e.headers.get("Retry-After") or 0)
                except ValueError:
                    wait = 0
                time.sleep(max(wait, 2 ** attempt) + 0.5)    # 1, 2, 4, 8, 16, 32 s
                continue
            last = e
        except Exception as e:  # noqa: BLE001 - network hiccup
            last = e
        time.sleep(1.5 * (attempt + 1))
    print(f"  giving up on {url.split('?')[0]}: {last!r}", flush=True)
    return FAILED


def run_pool(items, work, workers, label, store, batch=400):
    """work(item) -> result or None, in threads; store(conn-side list of results) every `batch` items."""
    done = {"n": 0, "ok": 0}
    t0 = time.time()
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(work, chunk))
        good = [r for r in results if r]
        store(good)
        done["n"] += len(chunk)
        done["ok"] += len(good)
        el = time.time() - t0
        rate = done["n"] / el if el else 0
        print(f"  {label}: {done['n']:,}/{len(items):,} ok={done['ok']:,} {rate:.1f}/s "
              f"~{(len(items) - done['n']) / max(rate, 1e-6) / 60:.0f} min left", flush=True)
    return done


# ------------------------------------------------------------------ images
def tmdb_key():
    raw = (os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_TOKEN") or "").strip().strip("'\"")
    for prefix in ("authorization:", "bearer"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
    if not raw:
        sys.exit("export TMDB_API_KEY=... first (the same key fetch_tmdb.py uses)")
    return raw


def all_film_ids(conn, movies_db):
    ids = [r[0] for r in conn.execute("select tmdb_id from films order by vote_count desc")]
    seen = set(ids)
    if os.path.exists(movies_db):
        m = sqlite3.connect(f"file:{movies_db}?mode=ro", uri=True)
        for (i,) in m.execute("select id from movies order by vote_count desc"):
            if i not in seen:
                seen.add(i)
                ids.append(i)
        m.close()
    return ids


def cmd_images(a, conn):
    key = tmdb_key()
    ids = all_film_ids(conn, a.movies)
    if not a.again:
        have = {r[0] for r in conn.execute("select tmdb_id from film_images")}
        ids = [i for i in ids if i not in have]
    if a.limit:
        ids = ids[:a.limit]
    print(f"images: {len(ids):,} films to fetch")
    if key.startswith("eyJ"):
        headers, auth = {"Authorization": f"Bearer {key}"}, ""
    else:
        headers, auth = None, f"&api_key={key}"

    def work(mid):
        d = http_json(f"https://api.themoviedb.org/3/movie/{mid}?append_to_response=credits&language=en-US{auth}",
                      headers)
        if d is FAILED:
            return None                         # try again on the next run
        if d is None:
            return (mid, None, None, [])        # 404: remember it, so it is not asked again
        cast = sorted((d.get("credits") or {}).get("cast", []), key=lambda c: c.get("order", 999))[:MAX_CAST]
        return (mid, d.get("poster_path"), d.get("backdrop_path"),
                [(mid, c["id"], c.get("name"), c.get("character"), c.get("order"), c.get("profile_path"))
                 for c in cast])

    def store(rows):
        ts = F.now()
        with _lock:
            conn.executemany("insert or replace into film_images values (?,?,?,?)",
                             [(m, p, b, ts) for m, p, b, _ in rows])
            for m, _, _, cast in rows:
                conn.execute("delete from cast_images where tmdb_id = ?", (m,))
                conn.executemany("insert or replace into cast_images values (?,?,?,?,?,?)", cast)
            conn.commit()

    st = run_pool(ids, work, a.workers, "images", store)
    F.set_meta(conn, "images_fetched_at", F.now())
    conn.commit()
    print(f"images done: {st}")


# ------------------------------------------------------------------ imdb
def cmd_imdb(a, conn):
    print(f"downloading {IMDB_URL} ...", flush=True)
    req = urllib.request.Request(IMDB_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    print(f"  {len(raw) / 1e6:.1f} MB", flush=True)
    want = {r[0] for r in conn.execute("select imdb_id from films where imdb_id like 'tt%'")}
    ts = F.now()
    rows, total = [], 0
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            total += 1
            if row["tconst"] in want:
                try:
                    rows.append((row["tconst"], float(row["averageRating"]), int(row["numVotes"]), ts))
                except ValueError:
                    pass
    conn.execute("delete from imdb_ratings")
    conn.executemany("insert into imdb_ratings values (?,?,?,?)", rows)
    F.set_meta(conn, "imdb_fetched_at", ts)
    conn.commit()
    print(f"imdb done: {total:,} titles in the file, {len(rows):,} of our {len(want):,} films with an IMDb id matched")


# ------------------------------------------------------------------ pageviews
def wiki_titles(wiki_db):
    """film_id -> article name as it appears in the URL (from wiki.db pages)."""
    out = {}
    if not os.path.exists(wiki_db):
        sys.exit(f"{wiki_db} not found")
    w = sqlite3.connect(f"file:{wiki_db}?mode=ro", uri=True)
    for fid, title, url in w.execute("select film_id, title, url from pages where film_id is not null"):
        art = None
        if url and "/wiki/" in url:
            art = urllib.parse.unquote(url.split("/wiki/", 1)[1].split("#")[0])
        elif title:
            art = title
        if art and fid not in out:
            out[fid] = art.replace(" ", "_")
    w.close()
    return out


def cmd_pageviews(a, conn):
    titles = wiki_titles(a.wiki)
    first = (PV_FIRST_DAY + datetime.timedelta(days=28)).isoformat()
    today = datetime.date.today().isoformat()
    rows = conn.execute("select tmdb_id, coalesce(us_release_date, release_date) from films "
                        "where coalesce(us_release_date, release_date) between ? and ?", (first, today)).fetchall()
    todo = [(fid, rd) for fid, rd in rows if fid in titles]
    if not a.again:
        have = {r[0] for r in conn.execute("select tmdb_id from pageviews")}
        todo = [t for t in todo if t[0] not in have]
    if a.limit:
        todo = todo[:a.limit]
    print(f"pageviews: {len(rows):,} films released {first}..{today}; {len(todo):,} with a Wikipedia page to fetch",
          flush=True)
    if not CONTACT:
        print("  note: MB_CONTACT is not set, so Wikimedia may answer 429 (see the top of this file)", flush=True)

    def work(item):
        fid, rd = item
        try:
            day = datetime.date.fromisoformat(rd[:10])
        except ValueError:
            return None
        start = (day - datetime.timedelta(days=28)).strftime("%Y%m%d")
        end = (day - datetime.timedelta(days=1)).strftime("%Y%m%d")    # the day BEFORE release
        art = urllib.parse.quote(titles[fid], safe="")
        time.sleep(0.05)                                                # stay far below Wikimedia's limits
        d = http_json(PV_API.format(article=art, start=start, end=end))
        if d is FAILED:
            return None
        items = (d or {}).get("items") or []
        views = {it["timestamp"][:8]: it.get("views", 0) for it in items}
        last7 = {(day - datetime.timedelta(days=k)).strftime("%Y%m%d") for k in range(1, 8)}
        return (fid, titles[fid], rd[:10], sum(views.values()), sum(v for t, v in views.items() if t in last7),
                len(views))

    def store(rs):
        ts = F.now()
        with _lock:
            conn.executemany("insert or replace into pageviews values (?,?,?,?,?,?,?)", [(*r, ts) for r in rs])
            conn.commit()

    st = run_pool(todo, work, a.workers, "pageviews", store, batch=200)
    F.set_meta(conn, "pageviews_fetched_at", F.now())
    conn.commit()
    print(f"pageviews done: {st}")


# ------------------------------------------------------------------ coverage
def cmd_coverage(a, conn):
    def one(sql, *p):
        try:
            return conn.execute(sql, p).fetchone()[0] or 0
        except sqlite3.Error:
            return 0
    cat = one("select count(*) from films where in_catalog = 1")
    allf = one("select count(*) from films")
    print("coverage (facts.db)")
    print(f"  films: {allf:,} (catalogue {cat:,})")
    pc = one("select count(*) from films f join film_images i using(tmdb_id) where f.in_catalog = 1 "
             "and i.poster_path is not null")
    pa = one("select count(*) from film_images where poster_path is not null")
    print(f"  posters: catalogue {pc:,}/{cat:,} = {100 * pc / max(cat, 1):.1f}% (goal 95%); all films {pa:,}")
    print(f"  backdrops: {one('select count(*) from film_images where backdrop_path is not null'):,}")
    print(f"  cast photos: {one('select count(*) from cast_images where profile_path is not null'):,} "
          f"for {one('select count(distinct tmdb_id) from cast_images'):,} films")
    ni = one("select count(*) from films where imdb_id like 'tt%'")
    nr = one("select count(*) from films f join imdb_ratings r on r.imdb_id = f.imdb_id")
    print(f"  IMDb ratings: {nr:,} of {ni:,} films with an IMDb id")
    npv = one("select count(*) from pageviews")
    npos = one("select count(*) from pageviews where days > 0")
    print(f"  pageviews: {npv:,} films checked, {npos:,} with views before release; "
          f"median 28-day views {median(conn):,}")
    for k in ("images_fetched_at", "imdb_fetched_at", "pageviews_fetched_at"):
        print(f"  meta {k} = {F.get_meta(conn, k)}")


def median(conn):
    try:
        xs = sorted(r[0] for r in conn.execute("select pv_pre28 from pageviews where days > 0"))
    except sqlite3.Error:
        return 0
    return xs[len(xs) // 2] if xs else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["images", "imdb", "pageviews", "coverage"])
    ap.add_argument("--movies", default=os.path.join(HERE, "movies.db"))
    ap.add_argument("--wiki", default=os.environ.get("WIKI_DB") or os.path.join(HERE, "wiki.db"))
    ap.add_argument("--facts", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--again", action="store_true")
    a = ap.parse_args()
    if a.cmd == "pageviews" and a.workers > 2:
        a.workers = 2                        # Wikimedia asks for gentle clients
    conn = connect(a.facts)
    t0 = time.time()
    {"images": cmd_images, "imdb": cmd_imdb, "pageviews": cmd_pageviews, "coverage": cmd_coverage}[a.cmd](a, conn)
    if a.cmd != "coverage":
        print(f"took {(time.time() - t0) / 60:.1f} min")
        cmd_coverage(a, conn)


if __name__ == "__main__":
    main()
