#!/usr/bin/env python3
"""
v6 free data (20 Oct night): every free source worth adding, into facts.db. movies.db and wiki.db
are not changed. Network only, no GPU: safe while the site and the pageviews job are running
(facts.db is WAL; every write is a short transaction).

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python fetch_free_data.py <command> [--limit N] [--again]

    command    key (env var)            what it stores                                  cost / time
    ---------  -----------------------  ----------------------------------------------  ----------------------
    tmdb       TMDB_API_KEY (have it)   trailers + teasers (YouTube keys), where to      1 request per film,
                                        watch (JustWatch data via TMDB), age ratings     ~32k, ~45 min
                                        (US/GB certifications)
    omdb       OMDB_API_KEY (free key)  Rotten Tomatoes %, Metacritic, IMDb rating,      free key 1,000/day:
                                        MPAA rating, awards text, US box office          run once a day; films
                                                                                         missing critic scores first
    guardian   GUARDIAN_API_KEY (free)  every Guardian film review since 1999: star      ~150 requests, minutes
                                        rating, standfirst, critic, URL (+ text with
                                        --body, for aspect analysis only)
    nyt        NYT_API_KEY (have it)    NYT film reviews: headline, summary, critic,     10/min, 4,000/day;
                                        URL, matched by the review's film keyword        shares the news key's
                                                                                         quota; resumable by month
    youtube    YOUTUBE_API_KEY (free)   trailer stats + title/description (videos.list,  10,000 units/day; trailers
                                        1 unit per 50 videos); --reviews N: N films'     ~700 units; each review
                                        top YouTube review videos (search.list, 100      search 100 units
                                        units each); --comments N: top 100 comments on
                                        N films' main trailers (1 unit each)
    netflix    none                     Netflix "What We Watched" engagement reports:    seconds
                                        hours viewed + views per film, per half-year.
                                        No --file: the four reports 2023 Jul-Dec ..
                                        2025 Jan-Jun as one CSV (TidyTuesday 2025-07-29);
                                        --file report.xlsx --period 2025H2 for a newer one
    movielens  none                     MovieLens 32M (32M ratings, 87k films, 239 MB)   ~5 min
                                        -> ml32_stats (n ratings, mean, tag counts)
    coverage                            how many films have each signal

Tables (facts.db):
    film_videos(tmdb_id, video_key, site, type, name, official, published_at, size, lang)
    watch_providers(tmdb_id, region, kind, provider_id, provider_name, priority, fetched_at)
    certifications(tmdb_id, region, certification, release_type, release_date)
    tmdb_extra_done(tmdb_id, fetched_at)
    omdb(imdb_id, tmdb_id, rt_pct, mc_score, imdb_rating, imdb_votes, rated, awards, box_office_us,
         production, status, raw_json, fetched_at)
    critic_reviews(review_key, source, tmdb_id, title_guess, headline, critic, stars, published, url,
         summary, body, match, fetched_at)                    Guardian + NYT professional reviews
    yt_videos(video_key, kind, tmdb_id, title, description, channel, published_at, views, likes,
         comments, duration, fetched_at)                       kind = trailer | review
    yt_comments(comment_id, video_key, tmdb_id, text, likes, published_at, fetched_at)
    netflix_views(period, title, tmdb_id, release_date, hours, runtime, views, global_, match)
    ml32_stats(tmdb_id, n_ratings, mean, n_tags, top_tags_json)

Leakage note for the box-office predictor: YouTube views, likes and comment counts are TODAY's
numbers (after release). Use them for "how much buzz" answers only. Safe pre-release features are
in film_videos: how many trailers/teasers and how early the first one came out (published_at is
before release), and comments whose published_at is before the release date.

Terms: TMDB non-commercial with credit, and watch-provider data must be credited to JustWatch
(the site does this). OMDb: free key. Guardian Open Platform: free developer key, non-commercial.
NYT APIs: non-commercial. YouTube Data API: data is refreshed or dropped after 30 days (re-run
`youtube --again` monthly). No video files or transcripts are downloaded: YouTube's terms do not
allow that, and the official captions endpoint only works for the channel's owner.
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile

import facts_db as F
import fetch_v6_data as V      # http_json, run_pool, tmdb_key, all_film_ids, UA, FAILED

HERE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()

DDL = """
create table if not exists film_videos(tmdb_id integer, video_key text, site text, type text, name text,
  official integer, published_at text, size integer, lang text, primary key (tmdb_id, video_key));
create index if not exists film_videos_key on film_videos(video_key);
create table if not exists watch_providers(tmdb_id integer, region text, kind text, provider_id integer,
  provider_name text, priority integer, fetched_at text, primary key (tmdb_id, region, kind, provider_id));
create table if not exists certifications(tmdb_id integer, region text, certification text,
  release_type integer, release_date text);
create index if not exists cert_film on certifications(tmdb_id, region);
create table if not exists tmdb_extra_done(tmdb_id integer primary key, fetched_at text);
create table if not exists omdb(imdb_id text primary key, tmdb_id integer, rt_pct real, mc_score real,
  imdb_rating real, imdb_votes integer, rated text, awards text, box_office_us real, production text,
  status text, raw_json text, fetched_at text);
create index if not exists omdb_film on omdb(tmdb_id);
create table if not exists critic_reviews(review_key text primary key, source text, tmdb_id integer,
  title_guess text, headline text, critic text, stars real, published text, url text, summary text,
  body text, match real, fetched_at text);
create index if not exists critic_reviews_film on critic_reviews(tmdb_id, source);
create table if not exists yt_videos(video_key text primary key, kind text, tmdb_id integer, title text,
  description text, channel text, published_at text, views integer, likes integer, comments integer,
  duration text, fetched_at text);
create index if not exists yt_videos_film on yt_videos(tmdb_id, kind);
create table if not exists yt_review_done(tmdb_id integer primary key, fetched_at text);
create table if not exists yt_comments(comment_id text primary key, video_key text, tmdb_id integer,
  text text, likes integer, published_at text, fetched_at text);
create index if not exists yt_comments_film on yt_comments(tmdb_id);
create table if not exists netflix_views(period text, title text, tmdb_id integer, release_date text,
  hours real, runtime text, views real, global_ text, match text, primary key (period, title));
create table if not exists ml32_stats(tmdb_id integer primary key, n_ratings integer, mean real,
  n_tags integer, top_tags_json text);
create table if not exists nyt_meta(review_key text primary key, kicker text, critics_pick integer, type_of_material text,
  word_count integer, section text, fetched_at text);
create table if not exists fetch_progress(source text, unit text, status text, n integer, fetched_at text,
  primary key (source, unit));
"""


def connect(path=None):
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


DENIED = object()      # 400/401/403: bad key, quota used up, or a refused query - do not retry


def api_get(url, retries=4, label="API"):
    """GET -> parsed JSON; None on 404; DENIED on 400/401/403 (the body is printed once);
    V.FAILED after repeated network errors / 429s / 5xx."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": V.UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            body = ""
            try:
                body = e.read()[:400].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            if e.code in (400, 401, 403):
                print(f"  {label} HTTP {e.code}: {body}", flush=True)
                try:
                    return {"__denied__": e.code, **json.loads(body)}
                except ValueError:
                    return {"__denied__": e.code}
            wait = 2 ** attempt * (5 if e.code == 429 else 1)
            print(f"  {label} HTTP {e.code}, waiting {wait}s", flush=True)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001 - network hiccup
            print(f"  {label} {e!r}", flush=True)
            time.sleep(2 * (attempt + 1))
    return V.FAILED


def denied(d):
    return isinstance(d, dict) and "__denied__" in d


def key_or_exit(name, how):
    k = (os.environ.get(name) or "").strip().strip("'\"")
    if not k:
        sys.exit(f"export {name}=... first ({how})")
    return k


def norm(t):
    """Title key for matching: no accents, punctuation, case or leading article."""
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"^(the|a|an) ", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def title_index(conn):
    """norm(title) -> [(tmdb_id, year, vote_count)] for every film in facts.db (title + original title)."""
    idx = {}
    for fid, t, ot, y, vc in conn.execute("select tmdb_id, title, original_title, year, vote_count from films"):
        for name in {t, ot}:
            if name:
                idx.setdefault(norm(name), []).append((fid, y, vc or 0))
    return idx


def pick(idx, title, year, lo=-2, hi=1):
    """Best film for a title near a year: (tmdb_id, match) or (None, None).
    match 1.0 = title + year window; 0.5 = only film with that title (re-release / old film)."""
    cands = idx.get(norm(title)) or []
    if not cands:
        return None, None
    if year:
        near = [c for c in cands if c[1] and year + lo <= c[1] <= year + hi]
        if near:
            return max(near, key=lambda c: c[2])[0], 1.0
    big = [c for c in cands if c[2] >= 50]
    if len(big) == 1:
        return big[0][0], 0.5
    return None, None


def progress(conn, source, unit, status, n=0):
    with _lock:
        conn.execute("insert or replace into fetch_progress values (?,?,?,?,?)", (source, unit, status, n, F.now()))
        conn.commit()


def done_units(conn, source):
    return {u for u, s in conn.execute("select unit, status from fetch_progress where source = ?", (source,))
            if s == "done"}


# ------------------------------------------------------------------ tmdb: videos, providers, certifications
def cmd_tmdb(a, conn):
    key = V.tmdb_key()
    ids = V.all_film_ids(conn, a.movies)
    if not a.again:
        have = {r[0] for r in conn.execute("select tmdb_id from tmdb_extra_done")}
        ids = [i for i in ids if i not in have]
    if a.limit:
        ids = ids[:a.limit]
    regions = [r.strip().upper() for r in a.regions.split(",") if r.strip()]
    print(f"tmdb: {len(ids):,} films (videos, where to watch in {','.join(regions)}, certifications)", flush=True)
    if key.startswith("eyJ"):
        headers, auth = {"Authorization": f"Bearer {key}"}, ""
    else:
        headers, auth = None, f"&api_key={key}"

    def work(mid):
        d = V.http_json(f"https://api.themoviedb.org/3/movie/{mid}?append_to_response=videos,watch/providers,"
                        f"release_dates&language=en-US&include_video_language=en,null{auth}", headers)
        if d is V.FAILED:
            return None
        if d is None:
            return (mid, [], [], [])
        vids = []
        for v in (d.get("videos") or {}).get("results") or []:
            if v.get("site") == "YouTube" and v.get("key"):
                vids.append((mid, v["key"], "YouTube", v.get("type"), (v.get("name") or "")[:200],
                             1 if v.get("official") else 0, v.get("published_at"), v.get("size"),
                             v.get("iso_639_1")))
        provs = []
        res = (d.get("watch/providers") or {}).get("results") or {}
        for reg in regions:
            r = res.get(reg) or {}
            for kind in ("flatrate", "free", "ads", "rent", "buy"):
                for p in r.get(kind) or []:
                    provs.append((mid, reg, kind, p.get("provider_id"), p.get("provider_name"),
                                  p.get("display_priority")))
        certs = []
        for r in (d.get("release_dates") or {}).get("results") or []:
            reg = r.get("iso_3166_1")
            if reg not in set(regions) | {"US", "GB"}:
                continue
            for rd in r.get("release_dates") or []:
                if rd.get("certification"):
                    certs.append((mid, reg, rd["certification"].strip(), rd.get("type"),
                                  (rd.get("release_date") or "")[:10]))
        return (mid, vids, provs, certs)

    def store(rows):
        ts = F.now()
        with _lock:
            for mid, vids, provs, certs in rows:
                conn.execute("delete from film_videos where tmdb_id = ?", (mid,))
                conn.execute("delete from watch_providers where tmdb_id = ?", (mid,))
                conn.execute("delete from certifications where tmdb_id = ?", (mid,))
                conn.executemany("insert or replace into film_videos values (?,?,?,?,?,?,?,?,?)", vids)
                conn.executemany("insert or replace into watch_providers values (?,?,?,?,?,?,?)",
                                 [(*p, ts) for p in provs])
                conn.executemany("insert into certifications values (?,?,?,?,?)", certs)
                conn.execute("insert or replace into tmdb_extra_done values (?,?)", (mid, ts))
            conn.commit()

    st = V.run_pool(ids, work, a.workers, "tmdb", store)
    F.set_meta(conn, "tmdb_extra_fetched_at", F.now())
    conn.commit()
    print(f"tmdb done: {st}")


# ------------------------------------------------------------------ omdb
def _num(s, pct=False):
    if not s or s == "N/A":
        return None
    s = s.replace(",", "").replace("$", "").strip()
    if pct:
        s = s.rstrip("%")
    s = s.split("/")[0]
    try:
        return float(s)
    except ValueError:
        return None


def cmd_omdb(a, conn):
    key = key_or_exit("OMDB_API_KEY", "free key: https://www.omdbapi.com/apikey.aspx")
    have = set() if a.again else {r[0] for r in conn.execute("select imdb_id from omdb where status in ('ok','missing')")}
    try:
        no_critics = {r[0] for r in conn.execute("select tmdb_id from critic_scores where rt_pct is not null "
                                                 "or mc_score is not null")}
    except sqlite3.Error:
        no_critics = set()
    rows = conn.execute("select tmdb_id, imdb_id, in_catalog, vote_count from films where imdb_id like 'tt%'").fetchall()
    todo = [r for r in rows if r[1] not in have]
    # films without critic scores first, then the catalogue, then by votes
    todo.sort(key=lambda r: (r[0] in no_critics, -(r[2] or 0), -(r[3] or 0)))
    budget = a.limit or a.max
    todo = todo[:budget]
    print(f"omdb: {len(todo):,} films this run (daily budget {budget:,}); "
          f"{sum(1 for r in todo if r[0] not in no_critics):,} have no critic scores yet", flush=True)
    stop = {"hit": False}

    def work(item):
        fid, imdb_id, _c, _v = item
        if stop["hit"]:
            return None
        d = api_get(f"https://www.omdbapi.com/?i={imdb_id}&apikey={urllib.parse.quote(key)}&tomatoes=true",
                    retries=3, label="OMDb")
        if d is V.FAILED or d is None:
            return None
        if denied(d) and d.get("Response") != "False":
            stop["hit"] = True
            return None
        if d.get("Response") == "False":
            err = d.get("Error") or ""
            if "limit" in err.lower() or "invalid api key" in err.lower():
                stop["hit"] = True
                print(f"  OMDb says: {err} - stopping; run again tomorrow", flush=True)
                return None
            return (imdb_id, fid, None, None, None, None, None, None, None, None, "missing", json.dumps(d)[:2000])
        rt = next((_num(r.get("Value"), pct=True) for r in d.get("Ratings") or [] if r.get("Source") == "Rotten Tomatoes"), None)
        mc = _num(d.get("Metascore"))
        return (imdb_id, fid, rt, mc, _num(d.get("imdbRating")), _num(d.get("imdbVotes")), d.get("Rated"),
                None if d.get("Awards") == "N/A" else d.get("Awards"), _num(d.get("BoxOffice")),
                None if d.get("Production") == "N/A" else d.get("Production"), "ok", json.dumps(d)[:6000])

    def store(rs):
        ts = F.now()
        with _lock:
            conn.executemany("insert or replace into omdb values (?,?,?,?,?,?,?,?,?,?,?,?,?)", [(*r, ts) for r in rs])
            conn.commit()

    st = V.run_pool(todo, work, min(a.workers, 4), "omdb", store, batch=100)
    F.set_meta(conn, "omdb_fetched_at", F.now())
    conn.commit()
    print(f"omdb done: {st}")


# ------------------------------------------------------------------ guardian
G_API = "https://content.guardianapis.com/search"
R_G1 = re.compile(r"^\s*(?:film|dvd|blu-ray|dvd and blu-ray)?\s*reviews?\s*[:\-–—]\s*(.+?)\s*$", re.I)
R_G2 = re.compile(r"^\s*(.+?)\s*[–—:\-]?\s+review\b", re.I)


def guardian_title(headline):
    h = re.sub(r"<[^>]+>", "", headline or "").strip()
    m = R_G1.match(h)
    if m:
        return m.group(1).split(" – ")[0].strip(" '‘’\"“”")
    m = R_G2.match(h)
    if m:
        return m.group(1).strip(" '‘’\"“”–-")
    return h.split(" – ")[0].strip(" '‘’\"“”")


GENERIC_TAGS = {"Film", "Drama", "Comedy", "Thriller", "Horror", "Documentary", "Animation", "Action and adventure",
                "Science fiction and fantasy", "Romance", "Crime", "World cinema", "Family films", "Musicals",
                "Biopics", "War films", "Westerns", "Independent", "Hollywood", "Culture", "Awards and prizes",
                "Oscars", "Cannes", "Venice film festival", "Berlin film festival", "Sundance", "Toronto film festival"}


def cmd_guardian(a, conn):
    key = key_or_exit("GUARDIAN_API_KEY", "free developer key: https://open-platform.theguardian.com/access/")
    idx = title_index(conn)
    start = a.since or F.get_meta(conn, "guardian_last_date") or "1999-01-01"
    if a.again:
        start = a.since or "1999-01-01"
    fields = "headline,trailText,byline,starRating,shortUrl" + (",bodyText" if a.body else "")
    page, pages, total, matched = 1, None, 0, 0
    print(f"guardian: film reviews from {start}{' with text' if a.body else ''}", flush=True)
    last_date = start
    while pages is None or page <= pages:
        q = urllib.parse.urlencode({"section": "film", "tag": "tone/reviews", "from-date": start[:10],
                                    "order-by": "oldest", "page-size": 200, "page": page,
                                    "show-fields": fields, "show-tags": "keyword", "api-key": key})
        d = api_get(f"{G_API}?{q}", label="Guardian")
        if d is V.FAILED or not d or denied(d):
            print("  stopped early (see the message above); run again to continue", flush=True)
            break
        r = d.get("response") or {}
        pages = r.get("pages") or 0
        rows = []
        ts = F.now()
        for it in r.get("results") or []:
            fl = it.get("fields") or {}
            pub = (it.get("webPublicationDate") or "")[:10]
            t = guardian_title(fl.get("headline") or it.get("webTitle"))
            yr = int(pub[:4]) if pub[:4].isdigit() else None
            fid, m = pick(idx, t, yr)
            if not fid:
                # 21 Oct: older headlines rarely name the film ("Bleak house"), but the Guardian tags each review
                # with a keyword for the film itself (film/<slug>). Only a title + year-window match counts.
                for tg in it.get("tags") or []:
                    if tg.get("type") == "keyword" and str(tg.get("id", "")).startswith("film/") \
                            and tg.get("webTitle") not in GENERIC_TAGS:
                        f2, m2 = pick(idx, tg["webTitle"], yr)
                        if f2 and m2 == 1.0:
                            fid, m, t = f2, 1.0, tg["webTitle"]
                            break
            matched += 1 if fid else 0
            stars = fl.get("starRating")
            rows.append(("guardian:" + it["id"], "guardian", fid, t, fl.get("headline") or it.get("webTitle"),
                         fl.get("byline"), float(stars) if stars not in (None, "") else None, pub,
                         fl.get("shortUrl") or it.get("webUrl"),
                         re.sub(r"<[^>]+>", "", fl.get("trailText") or "")[:600],
                         fl.get("bodyText") if a.body else None, m, ts))
            last_date = max(last_date, pub)
        with _lock:
            conn.executemany("insert or replace into critic_reviews values (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            F.set_meta(conn, "guardian_last_date", last_date)
            conn.commit()
        total += len(rows)
        print(f"  page {page}/{pages}: {total:,} reviews, {matched:,} matched to a film (up to {last_date})", flush=True)
        page += 1
        time.sleep(1.0)
        if a.limit and total >= a.limit:
            break
    print(f"guardian done: {total:,} reviews, {matched:,} matched")


# ------------------------------------------------------------------ nyt
NYT_API = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
R_NYT = re.compile(r"[‘'\"“](.+?)[’'\"”]\s*(?:Review|review)\b")
FQ_NEW = 'section.name:("Movies") AND typeOfMaterials:("Review")'
FQ_OLD = 'section_name:("Movies") AND type_of_material:("Review")'


def nyt_titles(doc):
    """Candidate film titles from an Article Search doc: the creative-work keyword, else the quoted
    title in the headline ('‘Dune’ Review: ...')."""
    out = []
    for k in doc.get("keywords") or []:
        name = (k.get("name") or "").lower()
        val = k.get("value") or ""
        if name in ("creative_works", "creativeworks", "title") and re.search(r"\((movie|film)\)\s*$", val, re.I):
            out.append(re.sub(r"\s*\((movie|film)\)\s*$", "", val, flags=re.I))
    h = doc.get("headline") or {}
    main = h.get("main") if isinstance(h, dict) else str(h)
    m = R_NYT.search(main or "")
    if m:
        out.append(m.group(1))
    return out


def cmd_nyt(a, conn):
    key = key_or_exit("NYT_API_KEY", "the key news.py uses")
    idx = title_index(conn)
    done = set() if a.again else done_units(conn, "nyt")
    today = datetime.date.today()
    months = []
    y, m = today.year, today.month
    while (y, m) >= (a.from_year, 1):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months = [x for x in months if x not in done or x >= today.strftime("%Y-%m")]   # this month: always redo
    calls = {"n": 0}
    fq = {"v": FQ_NEW}
    print(f"nyt: {len(months)} months to fetch (newest first), 10 requests/min", flush=True)
    total = matched = 0

    def get(params):
        calls["n"] += 1
        time.sleep(6.2)                                   # NYT: 10 requests per minute
        return api_get(f"{NYT_API}?{urllib.parse.urlencode(params)}", label="NYT")

    for ym in months:
        if a.limit and calls["n"] >= a.limit:
            break
        y, m = int(ym[:4]), int(ym[5:])
        last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
        rows, page, ok = [], 0, True
        meta_rows_all = []
        while page < 100:
            d = get({"fq": fq["v"], "begin_date": f"{y:04d}{m:02d}01", "end_date": f"{y:04d}{m:02d}{last:02d}",
                     "page": page, "sort": "oldest", "api-key": key})
            if denied(d) and d["__denied__"] == 400 and fq["v"] == FQ_NEW and page == 0 and not rows:
                print("  NYT refused the new field names; using the older ones", flush=True)
                fq["v"] = FQ_OLD
                continue
            if denied(d) and d["__denied__"] in (401, 403):
                print("  NYT key refused or the daily quota is used up: stopping; run again tomorrow", flush=True)
                return
            if d is V.FAILED or d is None or denied(d):
                ok = False
                break
            resp = d.get("response") or {}
            docs = resp.get("docs") or []
            ts = F.now()
            meta_rows = []                                  # v7 R6 (27 Oct): kicker = "Critic's Pick", material, length
            for doc in docs:
                hk = doc.get("headline") or {}
                kick = (hk.get("kicker") if isinstance(hk, dict) else None) or None
                meta_rows.append(("nyt:" + (doc.get("_id") or doc.get("uri") or doc.get("web_url")), kick,
                                  1 if kick and re.search(r"critic.?s pick", kick, re.I) else 0,
                                  doc.get("type_of_material"), doc.get("word_count"),
                                  doc.get("section_name") or doc.get("section"), ts))
            for doc in docs:
                pub = (doc.get("pub_date") or "")[:10]
                fid = mt = None
                guess = None
                for t in nyt_titles(doc):
                    guess = guess or t
                    fid, mt = pick(idx, t, y)
                    if fid:
                        break
                h = doc.get("headline") or {}
                b = doc.get("byline") or {}
                rows.append(("nyt:" + (doc.get("_id") or doc.get("uri") or doc.get("web_url")), "nyt", fid, guess,
                             h.get("main") if isinstance(h, dict) else str(h),
                             re.sub(r"^By\s+", "", (b.get("original") if isinstance(b, dict) else str(b or "")) or "") or None,
                             None, pub,
                             doc.get("web_url") or doc.get("url"),
                             (doc.get("abstract") or doc.get("summary") or doc.get("snippet") or "")[:600], None, mt, ts))
            meta_rows_all += meta_rows
            meta = resp.get("meta") or resp.get("metadata") or {}
            hits = meta.get("hits") or 0
            if not docs or (page + 1) * 10 >= hits:
                break
            page += 1
        with _lock:
            conn.executemany("insert or replace into critic_reviews values (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.executemany("insert or replace into nyt_meta values (?,?,?,?,?,?,?)", meta_rows_all)
            conn.commit()
        meta_rows_all = []
        progress(conn, "nyt", ym, "done" if ok else "partial", len(rows))
        total += len(rows)
        matched += sum(1 for r in rows if r[2])
        print(f"  {ym}: {len(rows)} reviews ({sum(1 for r in rows if r[2])} matched); total {total:,}, "
              f"{calls['n']} calls", flush=True)
        if calls["n"] >= 3900:
            print("  near the 4,000/day limit: stopping; run again tomorrow", flush=True)
            break
    print(f"nyt done: {total:,} reviews, {matched:,} matched")


# ------------------------------------------------------------------ youtube
YT = "https://www.googleapis.com/youtube/v3/"


def main_trailers(conn, limit=None, catalogue_first=True):
    """tmdb_id -> the official English trailer published earliest (else any trailer / teaser)."""
    rows = conn.execute("""select v.tmdb_id, v.video_key, v.type, v.official, v.published_at, f.vote_count
        from film_videos v join films f using(tmdb_id) where v.site = 'YouTube'
        and v.type in ('Trailer','Teaser')""").fetchall()
    best = {}
    for fid, key, typ, off, pub, vc in rows:
        rank = (typ != "Trailer", not off, pub or "9999")
        if fid not in best or rank < best[fid][0]:
            best[fid] = (rank, key, vc or 0)
    order = sorted(best.items(), key=lambda kv: -kv[1][2])
    return [(fid, v[1]) for fid, v in order][:limit or None]


def cmd_youtube(a, conn):
    key = key_or_exit("YOUTUBE_API_KEY", "free: Google Cloud console -> enable YouTube Data API v3 -> API key")
    units = {"n": 0}

    def call(path, **p):
        p["key"] = key
        d = api_get(f"{YT}{path}?{urllib.parse.urlencode(p)}", retries=3, label="YouTube")
        if denied(d):
            reason = json.dumps(d)[:300]
            if "quota" in reason.lower():
                print("  YouTube daily quota used up: stopping; it resets at midnight Pacific", flush=True)
                raise SystemExit(0)
            if path == "commentThreads":            # comments turned off on this video
                return None
            raise SystemExit("  YouTube refused the request (key or API not enabled?): " + reason)
        return None if d is V.FAILED else d

    def stats(keys, kind, fid_of):
        ts = F.now()
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            d = call("videos", part="snippet,statistics,contentDetails", id=",".join(chunk), maxResults=50)
            units["n"] += 1
            if not d:
                continue
            rows = []
            for it in d.get("items") or []:
                sn, s = it.get("snippet") or {}, it.get("statistics") or {}
                rows.append((it["id"], kind, fid_of.get(it["id"]), sn.get("title"), (sn.get("description") or "")[:4000],
                             sn.get("channelTitle"), sn.get("publishedAt"), int(s.get("viewCount", 0) or 0),
                             int(s.get("likeCount", 0) or 0), int(s.get("commentCount", 0) or 0),
                             (it.get("contentDetails") or {}).get("duration"), ts))
            with _lock:
                conn.executemany("insert or replace into yt_videos values (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                conn.commit()
            if (i // 50) % 20 == 0:
                print(f"  {kind} stats: {min(i + 50, len(keys)):,}/{len(keys):,} ({units['n']} units)", flush=True)

    # 1) every main trailer: stats + title + description (1 unit per 50)
    tr = main_trailers(conn, a.limit)
    if not a.again:
        have = {r[0] for r in conn.execute("select video_key from yt_videos where kind = 'trailer'")}
        tr = [t for t in tr if t[1] not in have]
    print(f"youtube: {len(tr):,} trailers to look up", flush=True)
    stats([k for _f, k in tr], "trailer", {k: f for f, k in tr})

    # 2) review videos for the most-voted films (search.list = 100 units each)
    if a.reviews:
        done = {r[0] for r in conn.execute("select tmdb_id from yt_review_done")}
        films = [r for r in conn.execute("select tmdb_id, title, year from films where in_catalog = 1 and year "
                                         "is not null order by vote_count desc limit 20000") if r[0] not in done]
        films = films[:a.reviews]
        found = {}
        for n, (fid, title, year) in enumerate(films, 1):
            d = call("search", part="snippet", q=f"{title} {year} movie review", type="video", maxResults=5,
                     relevanceLanguage="en", safeSearch="moderate")
            units["n"] += 100
            if d is None:
                break
            for it in d.get("items") or []:
                vid = (it.get("id") or {}).get("videoId")
                if vid and norm(title) in norm(it["snippet"].get("title")):
                    found[vid] = fid
            with _lock:
                conn.execute("insert or replace into yt_review_done values (?,?)", (fid, F.now()))
                conn.commit()
            if n % 10 == 0:
                print(f"  review searches: {n}/{len(films)} ({units['n']:,} units)", flush=True)
            if units["n"] > 9300:
                print("  near the 10,000-unit daily quota: stopping review searches", flush=True)
                break
        stats(list(found), "review", found)

    # 3) top comments on main trailers (1 unit per film): audience reaction text
    if a.comments:
        have = {r[0] for r in conn.execute("select distinct video_key from yt_comments")}
        todo = [(f, k) for f, k in main_trailers(conn) if k not in have][:a.comments]
        for n, (fid, k) in enumerate(todo, 1):
            d = call("commentThreads", part="snippet", videoId=k, maxResults=100, order="relevance", textFormat="plainText")
            units["n"] += 1
            rows = []
            for it in (d or {}).get("items") or []:
                c = ((it.get("snippet") or {}).get("topLevelComment") or {})
                s = c.get("snippet") or {}
                rows.append((c.get("id"), k, fid, (s.get("textDisplay") or "")[:1500], s.get("likeCount"),
                             s.get("publishedAt"), F.now()))
            with _lock:
                conn.executemany("insert or replace into yt_comments values (?,?,?,?,?,?,?)", rows)
                conn.commit()
            if n % 100 == 0:
                print(f"  comments: {n}/{len(todo)} trailers ({units['n']:,} units)", flush=True)
            if units["n"] > 9800:
                break
    F.set_meta(conn, "youtube_fetched_at", F.now())
    conn.commit()
    print(f"youtube done: about {units['n']:,} quota units used")


# ------------------------------------------------------------------ netflix
NETFLIX_TT = ("https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2025/2025-07-29/movies.csv")


def netflix_tidytuesday(conn):
    """21 Oct: the four Netflix engagement reports (2023 Jul-Dec .. 2025 Jan-Jun), films only, as the CSV the
    TidyTuesday project made from Netflix's own spreadsheets (columns: source, report, title,
    available_globally, release_date, hours_viewed, runtime, views)."""
    print(f"downloading {NETFLIX_TT} ...", flush=True)
    req = urllib.request.Request(NETFLIX_TT, headers={"User-Agent": V.UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8")
    idx = title_index(conn)
    rows, total, matched, periods = [], 0, 0, set()
    for row in csv.DictReader(io.StringIO(text)):
        title = (row.get("title") or "").strip()
        if not title:
            continue
        total += 1
        period = (row.get("report") or "unknown").strip()
        periods.add(period)
        rd = (row.get("release_date") or "").strip()[:10] or None
        yr = int(rd[:4]) if rd and rd[:4].isdigit() else None
        base = re.sub(r"\s*//.*$", "", title)
        fid, m = pick(idx, base, yr, lo=-1, hi=0)
        matched += 1 if fid else 0
        rows.append((period, title, fid, rd, _flt(row.get("hours_viewed")), row.get("runtime") or None,
                     _flt(row.get("views")), row.get("available_globally") or None, None if m is None else str(m)))
    with _lock:
        for p_ in periods:
            conn.execute("delete from netflix_views where period = ?", (p_,))
        conn.executemany("insert or replace into netflix_views values (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    print(f"netflix (TidyTuesday CSV): {total:,} film rows in {len(periods)} reports, {matched:,} matched to facts.db")


def cmd_netflix(a, conn):
    if not a.file:
        return netflix_tidytuesday(conn)
    if not os.path.exists(a.file):
        sys.exit("netflix: download the report xlsx from about.netflix.com (What We Watched), then\n"
                 "  python fetch_free_data.py netflix --file What_We_Watched_2025H1.xlsx --period 2025H1")
    try:
        import openpyxl
    except ImportError:
        sys.exit("pip install openpyxl")
    period = a.period or re.sub(r"\D", "", os.path.basename(a.file))[:6] or "unknown"
    idx = title_index(conn)
    wb = openpyxl.load_workbook(a.file, read_only=True, data_only=True)
    rows, total, matched = [], 0, 0
    for ws in wb.worksheets:
        if "tv" in ws.title.lower() or "show" in ws.title.lower():
            continue                                          # films only
        head = None
        for r in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in r]
            if head is None:
                low = [c.lower() for c in cells]
                if "title" in low and any("hours" in c for c in low):
                    head = {c: i for i, c in enumerate(low)}
                continue
            col = lambda *names: next((r[head[n]] for n in head if any(x in n for x in names)), None)  # noqa: E731
            title = col("title")
            if not title:
                continue
            title = str(title).strip()
            total += 1
            rd = col("release date")
            rd = rd.isoformat()[:10] if hasattr(rd, "isoformat") else (str(rd)[:10] if rd else None)
            yr = int(rd[:4]) if rd and rd[:4].isdigit() else None
            base = re.sub(r"\s*//.*$", "", title)             # "Title // Original title"
            fid, m = pick(idx, base, yr, lo=-1, hi=0)
            matched += 1 if fid else 0
            rows.append((period, title, fid, rd, _flt(col("hours")), str(col("runtime") or "") or None,
                         _flt(col("views")), str(col("global") or "") or None, None if m is None else str(m)))
    with _lock:
        conn.execute("delete from netflix_views where period = ?", (period,))
        conn.executemany("insert or replace into netflix_views values (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    print(f"netflix {period}: {total:,} films in the report, {matched:,} matched to facts.db")


def _flt(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ movielens 32M
ML_URL = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"


def cmd_movielens(a, conn):
    path = os.path.join(HERE, "data", "movielens", "ml-32m.zip")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        print(f"downloading {ML_URL} (239 MB) ...", flush=True)
        req = urllib.request.Request(ML_URL, headers={"User-Agent": V.UA})
        with urllib.request.urlopen(req, timeout=600) as r, open(path + ".part", "wb") as out:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                out.write(b)
        os.replace(path + ".part", path)
    z = zipfile.ZipFile(path)

    def read(name):
        member = next(n for n in z.namelist() if n.endswith("/" + name))
        return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"))

    to_tmdb = {r["movieId"]: int(r["tmdbId"]) for r in read("links.csv") if (r.get("tmdbId") or "").isdigit()}
    agg = {}
    for n, r in enumerate(read("ratings.csv")):
        t = to_tmdb.get(r["movieId"])
        if t:
            s = agg.setdefault(t, [0, 0.0])
            s[0] += 1
            s[1] += float(r["rating"])
        if n % 5_000_000 == 0 and n:
            print(f"  {n:,} ratings read", flush=True)
    tags = {}
    for r in read("tags.csv"):
        t = to_tmdb.get(r["movieId"])
        if t and r.get("tag"):
            d = tags.setdefault(t, {})
            k = r["tag"].strip().lower()
            d[k] = d.get(k, 0) + 1
    rows = []
    for t, (n, tot) in agg.items():
        tg = tags.get(t, {})
        top = sorted(tg.items(), key=lambda kv: -kv[1])[:12]
        rows.append((t, n, tot / n, sum(tg.values()), json.dumps(top)))
    with _lock:
        conn.execute("delete from ml32_stats")
        conn.executemany("insert into ml32_stats values (?,?,?,?,?)", rows)
        F.set_meta(conn, "ml32_built_at", F.now())
        conn.commit()
    print(f"movielens 32M: {len(rows):,} films with ratings; {sum(1 for r in rows if r[3]):,} with tags")


# ------------------------------------------------------------------ coverage
def cmd_coverage(a, conn):
    def one(sql, *p):
        try:
            return conn.execute(sql, p).fetchone()[0] or 0
        except sqlite3.Error:
            return 0
    cat = one("select count(*) from films where in_catalog = 1")
    print(f"free-data coverage (facts.db; catalogue {cat:,} films)")
    print(f"  tmdb extra checked: {one('select count(*) from tmdb_extra_done'):,}")
    print(f"  films with a YouTube trailer/teaser: "
          f"{one('select count(distinct tmdb_id) from film_videos where type in (?,?)', 'Trailer', 'Teaser'):,}")
    print(f"  films with US where-to-watch: {one('select count(distinct tmdb_id) from watch_providers where region = ?', 'US'):,}")
    print(f"  films with a US certification: {one('select count(distinct tmdb_id) from certifications where region = ?', 'US'):,}")
    print(f"  omdb: {one('select count(*) from omdb where status = ?', 'ok'):,} ok; RT {one('select count(*) from omdb where rt_pct is not null'):,}; "
          f"Metacritic {one('select count(*) from omdb where mc_score is not null'):,}")
    try:
        newc = one("""select count(*) from omdb o where (o.rt_pct is not null or o.mc_score is not null) and o.tmdb_id not in
                      (select tmdb_id from critic_scores where rt_pct is not null or mc_score is not null)""")
        print(f"    films that had no critic score before: {newc:,}")
    except sqlite3.Error:
        pass
    for src in ("guardian", "nyt"):
        print(f"  {src} reviews: {one('select count(*) from critic_reviews where source = ?', src):,}, "
              f"matched {one('select count(*) from critic_reviews where source = ? and tmdb_id is not null', src):,} "
              f"({one('select count(distinct tmdb_id) from critic_reviews where source = ?', src):,} films)")
    print(f"  youtube trailers with stats: {one('select count(*) from yt_videos where kind = ?', 'trailer'):,}; "
          f"review videos {one('select count(*) from yt_videos where kind = ?', 'review'):,}; "
          f"comments {one('select count(*) from yt_comments'):,}")
    print(f"  netflix rows: {one('select count(*) from netflix_views'):,} (matched {one('select count(*) from netflix_views where tmdb_id is not null'):,})")
    print(f"  movielens 32M films: {one('select count(*) from ml32_stats'):,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tmdb", "omdb", "guardian", "nyt", "youtube", "netflix", "movielens", "coverage"])
    ap.add_argument("--movies", default=os.path.join(HERE, "movies.db"))
    ap.add_argument("--facts", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--regions", default="US", help="tmdb: where-to-watch regions, e.g. US,GB")
    ap.add_argument("--max", type=int, default=950, help="omdb: requests this run (free key: 1,000/day)")
    ap.add_argument("--since", help="guardian: first publication date, YYYY-MM-DD")
    ap.add_argument("--body", action="store_true", help="guardian: also store review text (analysis only)")
    ap.add_argument("--from-year", type=int, default=1990, help="nyt: oldest year")
    ap.add_argument("--reviews", type=int, default=0, help="youtube: films to search review videos for (100 units each)")
    ap.add_argument("--comments", type=int, default=0, help="youtube: trailers to fetch top comments for (1 unit each)")
    ap.add_argument("--file", help="netflix: report .xlsx")
    ap.add_argument("--period", help="netflix: e.g. 2025H1")
    a = ap.parse_args()
    conn = connect(a.facts)
    t0 = time.time()
    globals()[f"cmd_{a.cmd}"](a, conn)
    if a.cmd != "coverage":
        print(f"took {(time.time() - t0) / 60:.1f} min")
        cmd_coverage(a, conn)


if __name__ == "__main__":
    main()
