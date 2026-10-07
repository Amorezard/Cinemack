#!/usr/bin/env python3
"""
v3 data: structured film data from the TMDB API into facts.db (movies.db is not changed).

One request per film (append_to_response) gives: budget, revenue, runtime, release dates
(incl. the US theatrical date), countries, companies, franchise (collection), full cast
with characters, the key crew (director, writers, producers, cinematographer, composer,
editor), keywords, IMDb/Wikidata ids and the first page of user reviews.

    source ~/venvs/movie-rag/bin/activate && export TMDB_API_KEY=...
    python fetch_tmdb.py catalog --limit 20     # smoke test
    python fetch_tmdb.py catalog                # every film in movies.db (~31k requests, ~40 min)
    python fetch_tmdb.py current                # now playing + upcoming + films released since
                                                # the Wikipedia snapshot (2023-01-01)
    python fetch_tmdb.py refresh                # re-fetch only what is stale (facts_db.FRESHNESS)
    python fetch_tmdb.py extra                  # 7 Oct: well-known films NOT in movies.db (any year),
                                                # for facts, box office, awards and rankings

Resumable: a film with tmdb_fetched_at set is skipped by `catalog` (use --again to redo).
Network only, no GPU: safe to run while an evaluation is using Ollama.

Terms: TMDB API, free for non-commercial use with attribution ("This product uses the TMDB
API but is not endorsed or certified by TMDB"). Rate limit ~40-50 requests/s; 8 workers
stay well under it and back off on 429.
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

import facts_db as F

BASE = "https://api.themoviedb.org/3"
APPEND = "credits,keywords,reviews,release_dates,external_ids"
CREW_JOBS = {"Director", "Screenplay", "Writer", "Story", "Novel", "Author", "Producer",
             "Executive Producer", "Director of Photography", "Original Music Composer",
             "Music", "Editor", "Characters"}
MAX_CAST = 15
SNAPSHOT = "2023-01-01"          # the Wikipedia corpus date; newer films need TMDB

_local = threading.local()
_lock = threading.Lock()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def clean_key(raw):
    """Accept the credential however it was pasted: with quotes, with 'Bearer ' or
    'Authorization: Bearer ' in front, or with a trailing newline/space."""
    k = (raw or "").strip().strip("'\"").strip()
    for prefix in ("authorization:", "bearer"):
        if k.lower().startswith(prefix):
            k = k[len(prefix):].strip()
    return k.strip("'\"").strip()


def get(path, key, params=None, retries=5):
    # Either kind of TMDB credential works: the v3 "API Key" (32 hex characters, sent as
    # api_key=...) or the v4 "API Read Access Token" (a long eyJ... string, sent as a Bearer header).
    if key.startswith("eyJ"):
        p, headers = dict(params or {}), {"Authorization": f"Bearer {key}", "accept": "application/json"}
    else:
        p, headers = {"api_key": key, **(params or {})}, None
    for attempt in range(retries):
        try:
            r = session().get(BASE + path, params=p, headers=headers, timeout=20)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        if r.status_code == 401:
            try:
                why = r.json().get("status_message", "")
            except ValueError:
                why = r.text[:120]
            raise SystemExit(f"401 from TMDB: {why!r}. The credential was sent as "
                             f"{'a Bearer token' if headers else 'api_key='}; it starts {key[:4]!r} and has "
                             f"{len(key)} characters (a v3 API Key is 32, a v4 Read Access Token starts 'eyJ' "
                             f"and is ~200+)")
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)) + 0.5)
            continue
        time.sleep(1.0 * (attempt + 1))
    return None


def us_theatrical(rd):
    """Earliest US theatrical (type 3) or limited (2) release date, else None."""
    for c in (rd or {}).get("results", []):
        if c.get("iso_3166_1") == "US":
            dates = sorted(x.get("release_date", "")[:10] for x in c.get("release_dates", [])
                           if x.get("type") in (2, 3) and x.get("release_date"))
            if dates:
                return dates[0]
    return None


def store(conn, d, tier, in_catalog):
    """Write one /movie/{id} payload. Caller holds the lock."""
    mid = d["id"]
    ts = F.now()
    rd = (d.get("release_date") or "")[:10] or None
    year = int(rd[:4]) if rd and rd[:4].isdigit() else None
    ext = d.get("external_ids") or {}
    coll = d.get("belongs_to_collection") or {}
    conn.execute("""insert or replace into films(tmdb_id, imdb_id, wikidata_id, wiki_doc_id, title,
        original_title, release_date, us_release_date, year, runtime, budget, revenue, status,
        original_language, overview, tagline, genres_json, countries_json, companies_json,
        collection_id, collection_name, popularity, vote_average, vote_count, in_catalog, tier,
        tmdb_fetched_at) values (?,?,?,(select wiki_doc_id from films where tmdb_id=?),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (mid, d.get("imdb_id") or ext.get("imdb_id"), ext.get("wikidata_id"), mid,
                  d.get("title"), d.get("original_title"), rd, us_theatrical(d.get("release_dates")),
                  year, d.get("runtime") or None, d.get("budget") or None, d.get("revenue") or None,
                  d.get("status"), d.get("original_language"), d.get("overview"), d.get("tagline"),
                  json.dumps([g["name"] for g in d.get("genres", [])]),
                  json.dumps([c.get("iso_3166_1") for c in d.get("production_countries", [])]),
                  json.dumps([c.get("name") for c in d.get("production_companies", [])]),
                  coll.get("id"), coll.get("name"), d.get("popularity"), d.get("vote_average"),
                  d.get("vote_count"), int(in_catalog), tier, ts))
    conn.execute("delete from credits where tmdb_id = ?", (mid,))
    conn.execute("delete from keywords where tmdb_id = ?", (mid,))
    conn.execute("delete from facts where tmdb_id = ? and source = 'tmdb'", (mid,))
    cr = d.get("credits") or {}
    people, rows = {}, []
    for c in sorted(cr.get("cast", []), key=lambda c: c.get("order", 999))[:MAX_CAST]:
        people[c["id"]] = (c.get("name"), c.get("popularity"), c.get("known_for_department"))
        rows.append((mid, c["id"], "cast", "Actor", "Acting", c.get("character"), c.get("order")))
    for c in cr.get("crew", []):
        if c.get("job") in CREW_JOBS:
            people[c["id"]] = (c.get("name"), c.get("popularity"), c.get("known_for_department"))
            rows.append((mid, c["id"], "crew", c.get("job"), c.get("department"), None, None))
    conn.executemany("insert or replace into people values (?,?,?,?)",
                     [(pid, *v) for pid, v in people.items()])
    conn.executemany("insert into credits values (?,?,?,?,?,?,?)", rows)
    conn.executemany("insert into keywords values (?,?,?)",
                     [(mid, k["id"], k["name"]) for k in (d.get("keywords") or {}).get("keywords", [])])
    for r in (d.get("reviews") or {}).get("results", []):
        ad = r.get("author_details") or {}
        conn.execute("insert or replace into reviews values (?,?,?,?,?,?,?,?,?)",
                     (r["id"], mid, "tmdb", r.get("author"), ad.get("rating"),
                      (r.get("created_at") or "")[:10], r.get("url"), r.get("content"), ts))
    for field in ("budget", "revenue"):
        if d.get(field):
            conn.execute("insert into facts values (?,?,?,?,?,?,?,?,?,?)",
                         (mid, field, float(d[field]), None, "USD", "tmdb",
                          f"https://www.themoviedb.org/movie/{mid}", ts[:10], ts, F.RELIABILITY["tmdb"]))


def fetch_many(conn, key, ids, tier_of, catalog, workers, label):
    done = {"n": 0, "ok": 0, "missing": 0}
    t0 = time.time()
    pending = []

    def work(mid):
        d = get(f"/movie/{mid}", key, {"append_to_response": APPEND, "language": "en-US"})
        with _lock:
            done["n"] += 1
            if d:
                done["ok"] += 1
                pending.append(d)
            else:
                done["missing"] += 1
            n = done["n"]
            if n % 50 == 0 or n == len(ids):
                el = time.time() - t0
                rate = n / el if el else 0
                print(f"  {label}: {n:,}/{len(ids):,} ok={done['ok']:,} missing={done['missing']:,} "
                      f"{rate:.1f}/s ~{(len(ids) - n) / max(rate, 1e-6) / 60:.0f} min left", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(ids), 400):
            list(pool.map(work, ids[i:i + 400]))
            with _lock:
                batch, pending[:] = list(pending), []
            for d in batch:
                store(conn, d, tier_of(d), d["id"] in catalog)
            conn.commit()
    return done


def catalog_ids(movies_db):
    conn = sqlite3.connect(f"file:{movies_db}?mode=ro", uri=True)
    ids = [r[0] for r in conn.execute("select id from movies order by vote_count desc")]
    conn.close()
    return ids


def tier_for(d):
    rd = (d.get("release_date") or "")[:10]
    today = datetime.date.today().isoformat()
    if not rd or rd > today:
        return "upcoming"
    return "recent" if rd >= SNAPSHOT else "catalog"


def cmd_catalog(a, key, conn):
    ids = catalog_ids(a.movies)
    if not a.again:
        have = {r[0] for r in conn.execute("select tmdb_id from films where tmdb_fetched_at is not null")}
        ids = [i for i in ids if i not in have]
    if a.limit:
        ids = ids[:a.limit]
    print(f"catalog: {len(ids):,} films to fetch")
    cat = set(catalog_ids(a.movies))
    st = fetch_many(conn, key, ids, tier_for, cat, a.workers, "catalog")
    F.set_meta(conn, "catalog_fetched_at", F.now())
    conn.commit()
    print(f"catalog done: {st}")


def list_ids(key, path, params, pages):
    out = []
    for p in range(1, pages + 1):
        d = get(path, key, {**params, "page": p})
        if not d:
            break
        out += [r["id"] for r in d.get("results", [])]
        if p >= (d.get("total_pages") or 1):
            break
    return out


def cmd_current(a, key, conn):
    today = datetime.date.today()
    ts = F.now()
    lists = {
        "now_playing": list_ids(key, "/movie/now_playing", {"region": "US"}, 5),
        "upcoming": list_ids(key, "/movie/upcoming", {"region": "US"}, 10),
        # Released since the Wikipedia snapshot, well-known enough to be asked about.
        "since_snapshot": list_ids(key, "/discover/movie", {
            "primary_release_date.gte": SNAPSHOT, "primary_release_date.lte": today.isoformat(),
            "sort_by": "vote_count.desc", "vote_count.gte": 50, "include_adult": "false"}, a.pages),
        # Announced, dated, and drawing attention: next 12 months.
        "announced": list_ids(key, "/discover/movie", {
            "primary_release_date.gte": today.isoformat(),
            "primary_release_date.lte": (today + datetime.timedelta(days=365)).isoformat(),
            "sort_by": "popularity.desc", "include_adult": "false"}, 10),
    }
    for name, ids in lists.items():
        conn.execute("delete from lists where list_name = ?", (name,))
        conn.executemany("insert into lists values (?,?,?,?,?)",
                         [(name, "US", mid, i + 1, ts) for i, mid in enumerate(ids)])
        print(f"  list {name}: {len(ids)} films")
    conn.commit()
    want = list(dict.fromkeys(m for ids in lists.values() for m in ids))
    cat = set(catalog_ids(a.movies))
    st = fetch_many(conn, key, want, tier_for, cat, a.workers, "current")
    for name in ("now_playing", "upcoming"):
        conn.executemany("update films set tier = ? where tmdb_id = ? and tier != 'catalog'",
                         [(name, mid) for mid in lists[name]])
    F.set_meta(conn, "current_fetched_at", ts)
    conn.commit()
    print(f"current done: {st}")


def cmd_extra(a, key, conn):
    """Films outside the 30,856-film catalogue that people still ask about: for every release
    year, TMDB's most-voted films (vote_count >= --min-votes), up to --pages x 20 per year.
    They are not searchable by plot (movies.db and wiki.db are unchanged) but give facts,
    money, credits, awards (via their Wikidata id) and rows in rankings and careers.
    Stored with tier 'extra' (or recent/upcoming) and in_catalog = 0."""
    have = {r[0] for r in conn.execute("select tmdb_id from films where tmdb_fetched_at is not null")}
    cat = set(catalog_ids(a.movies))
    want = []
    last = datetime.date.today().year
    for y in range(a.year_from, last + 1):
        ids = list_ids(key, "/discover/movie", {
            "primary_release_year": y, "sort_by": "vote_count.desc",
            "vote_count.gte": a.min_votes, "include_adult": "false"}, a.pages)
        new = [i for i in ids if i not in have and i not in cat]
        want += new
        print(f"  {y}: {len(ids)} listed, {len(new)} new", flush=True)
    want = list(dict.fromkeys(want))
    if a.limit:
        want = want[:a.limit]
    print(f"extra: {len(want):,} films to fetch")

    def tier_extra(d):
        t = tier_for(d)
        return "extra" if t == "catalog" else t
    st = fetch_many(conn, key, want, tier_extra, cat, a.workers, "extra")
    F.set_meta(conn, "extra_fetched_at", F.now())
    conn.commit()
    print(f"extra done: {st}")


def cmd_refresh(a, key, conn):
    """Only what is stale: upcoming/now playing daily, films from the last year weekly,
    everything else monthly (ratings, review counts). Static fields never change, but
    one request returns them anyway."""
    today = datetime.date.today()
    rules = [("tier in ('upcoming','now_playing')", F.FRESHNESS["fast"]),
             (f"release_date >= '{(today - datetime.timedelta(days=365)).isoformat()}'", F.FRESHNESS["recent"]),
             ("1=1", F.FRESHNESS["slow"])]
    ids, seen = [], set()
    for where, days in rules:
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
        for (mid,) in conn.execute(f"select tmdb_id from films where {where} and "
                                   f"(tmdb_fetched_at is null or tmdb_fetched_at < ?)", (cutoff,)):
            if mid not in seen:
                seen.add(mid)
                ids.append(mid)
    if a.limit:
        ids = ids[:a.limit]
    print(f"refresh: {len(ids):,} stale films")
    cat = set(catalog_ids(a.movies))
    st = fetch_many(conn, key, ids, tier_for, cat, a.workers, "refresh")
    F.set_meta(conn, "refreshed_at", F.now())
    conn.commit()
    print(f"refresh done: {st}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["catalog", "current", "refresh", "extra"])
    ap.add_argument("--movies", default="movies.db")
    ap.add_argument("--facts", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pages", type=int, default=50, help="discover pages for `current` (20 films each)")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--min-votes", type=int, default=100, help="`extra`: TMDB votes a film needs")
    ap.add_argument("--year-from", type=int, default=1930, help="`extra`: first release year")
    a = ap.parse_args()
    key = clean_key(os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_TOKEN") or "")
    if not key:
        sys.exit("export TMDB_API_KEY=... first")
    conn = F.connect(a.facts)
    t0 = time.time()
    {"catalog": cmd_catalog, "current": cmd_current, "refresh": cmd_refresh, "extra": cmd_extra}[a.cmd](a, key, conn)
    print(f"took {(time.time() - t0) / 60:.1f} min")
    F.status(a.facts)


if __name__ == "__main__":
    main()
