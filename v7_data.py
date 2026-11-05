#!/usr/bin/env python3
"""
v7 data (23 Oct): the data the v7 plan builds on, into facts.db. movies.db and wiki.db are not changed.
Network + CPU only, no GPU: safe while the site is running (facts.db is WAL; writes are short transactions).

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    export TMDB_API_KEY=...  MB_CONTACT='you@example.edu'
    python v7_data.py <command> [--limit N] [--again]

    command     source (licence)                          what it stores                                    time
    ----------  ----------------------------------------  ------------------------------------------------  --------
    sources     -                                         data_sources: the licensing register (every         seconds
                                                          source, its licence, credit line, refresh days)
    register    -                                         (E6) the same register written into web/about.html  seconds
                                                          and README.md (between the register markers)
    tv          TMDB API (non-commercial, credit)         TV shows (D1): the most-voted shows (news and talk  ~15 min
                                                          shows left out), seasons, cast/creators, keywords,
                                                          trailers, age ratings, US where to watch, reviews
    tv-wiki     Wikipedia (CC BY-SA)                      each show's live article (Wikidata link, else a     ~1 h
                                                          search) -> tv_wiki; passages are built Saturday
    imdb        IMDb Non-Commercial Datasets              (D2) titles + title types, ratings, alternate       ~30 min,
                ("Data courtesy of IMDb.")                titles, directors/writers, principal cast/crew,     1.5 GB
                                                          people, episodes of our shows, for our films AND    download
                                                          shows only
    movielens   MovieLens 32M (GroupLens research         (R8) per film: rating histogram (10 half-star       ~10 min
                licence, cite Harper & Konstan 2015)      bins), mean, sd, Bayesian mean, share of low/high
                                                          ratings, bimodality, early vs later ratings; and
                                                          ratings per calendar year
    genome      MovieLens 25M tag genome (same licence;   (R9) the 25 most relevant of 1,128 tags per film    ~5 min,
                cite Vig, Sen & Riedl 2012)               ("atmospheric", "twist ending") + the full matrix    250 MB
                                                          as data/genome/genome.npz for recommendations
    reception   Wikipedia (CC BY-SA)                      (R2) the wikitext (with <ref> citations) of each    ~1 h
                                                          film's Reception / Critical response / Box office /
                                                          Legacy sections; Monday's extractor turns them into
                                                          critic statements with the outlet each one cites
    wd-scores   Wikidata (CC0)                            (E1, 27 Oct) review scores stated on each film's    ~10 min
                                                          item (P444): Rotten Tomatoes %, Metacritic, with
                                                          who / when / how many reviews -> wikidata_scores
    coverage    -                                         how many films / shows have each signal

Tables (facts.db). NOTE: TMDB TV ids and TMDB movie ids are different id spaces that overlap (tv 1399 is Game of
Thrones, movie 1399 is something else). Every tv_* table is keyed by the TV id and must never be joined to films.
    data_sources(source, what, tables, licence, credit, url, refresh_days, last_run)
    tv_list(tmdb_id, name, vote_count, rank, fetched_at)                      discover order
    tv_shows(tmdb_id, imdb_id, tvdb_id, wikidata_id, name, original_name, first_air_date, last_air_date, first_year,
             end_year, status, type, in_production, n_seasons, n_episodes, episode_runtime, original_language,
             origin_country_json, genres_json, networks_json, companies_json, created_by_json, overview, tagline,
             popularity, vote_average, vote_count, poster_path, backdrop_path, fetched_at)
    tv_seasons, tv_credits, tv_keywords, tv_videos, tv_ratings, tv_providers, tv_reviews, tv_wiki
    imdb_titles, imdb_ratings (existing table, now films + shows + their episodes), imdb_akas, imdb_crew,
    imdb_principals, imdb_names, imdb_episodes
    ml_dist(tmdb_id, n, mean, sd, bayes_mean, hist_json, pct_low, pct_high, bc, film_year, early_n, early_mean,
            late_n, late_mean, first_rating_year, last_rating_year)
    ml_year(tmdb_id, year, n, mean)
    film_tags(tmdb_id, tag, relevance, rank)                                  tag genome, top 25 per film
    wiki_reception(tmdb_id, pageid, revid, kind, heading, level, wikitext_z, refs_z, fetched_at)  zlib-compressed
    wiki_reception_done(pageid, tmdb_id, revid, status, n_sections, fetched_at)

Resumable: tv, tv-wiki and reception skip what is done (--again redoes; reception --again only refetches pages whose
revision changed). imdb / movielens / genome rebuild their tables from the downloaded files each run (downloads are
kept in data/ and reused for 7 days).
"""
import argparse
import bisect
import calendar
import csv
import gzip
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import zipfile
import zlib

import facts_db as F

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
_lock = threading.Lock()

try:                                    # network helpers shared with the v6 fetchers
    import fetch_v6_data as V
except Exception:  # noqa: BLE001 - tests run without the rest of the repo
    V = None

CONTACT = os.environ.get("MB_CONTACT", "").strip()
UA = ("MovieBuff/7 (student research project, Merrimack College" + (f"; {CONTACT}" if CONTACT else "")
      + ") python-urllib")

DDL = """
create table if not exists data_sources(source text primary key, what text, tables text, licence text, credit text,
  url text, refresh_days integer, last_run text);

create table if not exists tv_list(tmdb_id integer primary key, name text, vote_count integer, rank integer,
  fetched_at text);
create table if not exists tv_shows(tmdb_id integer primary key, imdb_id text, tvdb_id integer, wikidata_id text,
  name text, original_name text, first_air_date text, last_air_date text, first_year integer, end_year integer,
  status text, type text, in_production integer, n_seasons integer, n_episodes integer, episode_runtime integer,
  original_language text, origin_country_json text, genres_json text, networks_json text, companies_json text,
  created_by_json text, overview text, tagline text, popularity real, vote_average real, vote_count integer,
  poster_path text, backdrop_path text, fetched_at text);
create index if not exists tv_shows_name on tv_shows(name);
create index if not exists tv_shows_imdb on tv_shows(imdb_id);
create table if not exists tv_seasons(tmdb_id integer, season_number integer, name text, air_date text,
  episode_count integer, overview text, vote_average real, poster_path text, primary key (tmdb_id, season_number));
create table if not exists tv_credits(tmdb_id integer, person_id integer, name text, kind text, job text,
  character text, episodes integer, ord integer, profile_path text);
create index if not exists tv_credits_show on tv_credits(tmdb_id);
create index if not exists tv_credits_person on tv_credits(person_id);
create table if not exists tv_keywords(tmdb_id integer, keyword_id integer, name text);
create index if not exists tv_keywords_show on tv_keywords(tmdb_id);
create table if not exists tv_videos(tmdb_id integer, video_key text, type text, name text, official integer,
  published_at text, primary key (tmdb_id, video_key));
create table if not exists tv_ratings(tmdb_id integer, region text, rating text, primary key (tmdb_id, region));
create table if not exists tv_providers(tmdb_id integer, region text, kind text, provider_id integer,
  provider_name text, priority integer, primary key (tmdb_id, region, kind, provider_id));
create table if not exists tv_reviews(review_id text primary key, tmdb_id integer, author text, rating real,
  created_at text, url text, content text, fetched_at text);
create index if not exists tv_reviews_show on tv_reviews(tmdb_id);
create table if not exists tv_wiki(tmdb_id integer primary key, pageid integer, title text, url text, method text,
  extract text, fetched_at text, status text);

create table if not exists imdb_ratings(imdb_id text primary key, rating real, votes integer, fetched_at text);
create table if not exists imdb_titles(tconst text primary key, title_type text, primary_title text,
  original_title text, is_adult integer, start_year integer, end_year integer, runtime integer, genres text);
create table if not exists imdb_akas(tconst text, ordering integer, title text, region text, language text,
  types text, attributes text, is_original integer, primary key (tconst, ordering));
create index if not exists imdb_akas_title on imdb_akas(title);
create table if not exists imdb_crew(tconst text primary key, directors text, writers text);
create table if not exists imdb_principals(tconst text, ordering integer, nconst text, category text, job text,
  characters text, primary key (tconst, ordering));
create index if not exists imdb_principals_person on imdb_principals(nconst);
create table if not exists imdb_names(nconst text primary key, name text, birth_year integer, death_year integer,
  professions text, known_for text);
create index if not exists imdb_names_name on imdb_names(name);
create table if not exists imdb_episodes(tconst text primary key, parent text, season integer, episode integer);
create index if not exists imdb_episodes_parent on imdb_episodes(parent);

create table if not exists ml_dist(tmdb_id integer primary key, n integer, mean real, sd real, bayes_mean real,
  hist_json text, pct_low real, pct_high real, bc real, film_year integer, early_n integer, early_mean real,
  late_n integer, late_mean real, first_rating_year integer, last_rating_year integer);
create table if not exists ml_year(tmdb_id integer, year integer, n integer, mean real, primary key (tmdb_id, year));
create table if not exists film_tags(tmdb_id integer, tag text, relevance real, rank integer,
  primary key (tmdb_id, tag));
create index if not exists film_tags_tag on film_tags(tag, relevance);

create table if not exists wiki_reception(id integer primary key, tmdb_id integer, pageid integer, revid integer,
  kind text, heading text, level integer, wikitext_z blob, refs_z blob, fetched_at text);
create index if not exists wiki_reception_film on wiki_reception(tmdb_id, kind);
create table if not exists wiki_reception_done(pageid integer primary key, tmdb_id integer, revid integer,
  status text, n_sections integer, fetched_at text);
"""


def connect(path=None):
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


def touch_source(conn, source):
    with _lock:
        conn.execute("update data_sources set last_run = ? where source = ?", (F.now(), source))
        conn.commit()


def one(conn, sql, *p):
    try:
        return conn.execute(sql, p).fetchone()[0] or 0
    except sqlite3.Error:
        return 0


# ------------------------------------------------------------------ sources (licensing register, R1 / E6)
SOURCES = [
    # source, what, tables, licence, credit line, url, refresh days
    ("tmdb", "film and TV metadata, cast, keywords, trailers, posters, age ratings, audience reviews",
     "films people credits keywords reviews film_videos film_images certifications tv_*",
     "TMDB API terms: free, non-commercial, attribution required",
     "This product uses the TMDB API but is not endorsed or certified by TMDB.",
     "https://www.themoviedb.org/api-terms-of-use", 30),
    ("justwatch", "where to watch (via TMDB)", "watch_providers tv_providers",
     "TMDB watch-provider data must credit JustWatch", "Streaming availability data by JustWatch.",
     "https://www.justwatch.com", 7),
    ("wikipedia", "lead, plot, production, reception and box-office text; reception wikitext with citations",
     "wiki.db sections wiki_reception recent_wiki tv_wiki",
     "CC BY-SA 4.0: credit and link each article, share alike",
     "Text from Wikipedia (CC BY-SA 4.0), linked under each answer.",
     "https://en.wikipedia.org/wiki/Wikipedia:Copyrights", 30),
    ("wikipedia-critics", "critic statements from Reception sections, each with the outlet / critic its <ref> cites",
     "critic_statements review_sentiment evidence",
     "CC BY-SA 4.0 for the Wikipedia sentence; the quoted outlet is credited and its review linked when the "
     "citation has a URL; short quotes only", "Critic statements as reported by Wikipedia (CC BY-SA 4.0), with "
     "the outlet each one cites.", "https://en.wikipedia.org/wiki/Wikipedia:Copyrights", 30),
    ("wikidata", "budget, box office, awards, ids, review scores stated on each item (P444)",
     "facts awards wikidata_scores", "CC0", "Data from Wikidata (CC0).",
     "https://www.wikidata.org/wiki/Wikidata:Licensing", 30),
    ("imdb", "ratings and votes, title types, alternate titles, directors, writers, principal cast, episodes",
     "imdb_ratings imdb_titles imdb_akas imdb_crew imdb_principals imdb_names imdb_episodes",
     "IMDb Non-Commercial Datasets: personal and non-commercial use only", "Data courtesy of IMDb.",
     "https://developer.imdb.com/non-commercial-datasets/", 7),
    ("movielens", "32M audience ratings: distributions and change over time; tag genome (25M)",
     "ml32_stats ml_dist ml_year film_tags",
     "GroupLens research licence: non-commercial, cite the dataset paper; do not redistribute",
     "MovieLens data: F. M. Harper and J. A. Konstan, ACM TiiS 5(4), 2015. Tag genome: Vig, Sen and Riedl, "
     "ACM TiiS 2(3), 2012.", "https://grouplens.org/datasets/movielens/", None),
    ("omdb", "Rotten Tomatoes %, Metacritic, IMDb rating, awards text", "omdb",
     "OMDb API: free key 1,000 requests/day (CC BY-NC 4.0 for the data)", "Scores via OMDb.",
     "https://www.omdbapi.com/legal.htm", 30),
    ("guardian", "film reviews: star rating, standfirst, critic, link",
     "critic_reviews (source = guardian)",
     "Guardian Open Platform developer key: non-commercial. Decision (E8, 27 Oct): stars and the one-line standfirst "
     "are shown and may go in prompts with a link; article bodies are NOT analysed by any model until the terms "
     "for text mining are confirmed", "Reviews from The Guardian (powered by the Guardian Open Platform), linked.",
     "https://open-platform.theguardian.com/access/", 7),
    ("nyt", "film reviews: critic, date, Critic's Pick, link (no text)", "critic_reviews nyt_meta (source = nyt)",
     "NYT Developer APIs: non-commercial, attribution. Decision (E8, 27 Oct): the NYT terms bar using content to "
     "ground or train AI without consent, so NYT text never goes into a prompt or through a model; only critic, date, "
     "Critic's Pick and a link are shown", "Data provided by The New York Times.",
     "https://developer.nytimes.com/terms", 7),
    ("youtube", "trailer statistics, review / analysis videos of allow-listed channels (title, description, "
     "chapters), public comments",
     "yt_videos yt_comments yt_channels yt_analysis yt_analysis_film yt_analysis_units",
     "YouTube API Services terms. Decision (E8): stored data is refreshed or deleted within 30 days (nightly.sh "
     "purge); no captions or transcripts; comments are counted, not analysed by a model",
     "Video data from YouTube, linked to each video.",
     "https://developers.google.com/youtube/terms/developer-policies", 30),
    ("netflix", "hours viewed and views per film per half-year", "netflix_views",
     "Netflix engagement reports (public); TidyTuesday CSV (CC0)", "Viewing figures: Netflix What We Watched.",
     "https://about.netflix.com/en/news/what-we-watched", 180),
    ("letterboxd", "ratings, distributions, reviews (only if API access is approved)", "-",
     "API by request only; terms forbid scraping. connectors/letterboxd.py stays APPROVED = False until written "
     "approval arrives", "-", "https://letterboxd.com/api-beta/", None),
    ("rottentomatoes", "official RT API (licensed, not used)", "-",
     "Licensed through a business proposal; scores come from OMDb and Wikipedia instead", "-",
     "https://www.rottentomatoes.com/help_desk/licensing", None),
]


def seed_sources(conn):
    with _lock:
        for s in SOURCES:
            conn.execute("""insert into data_sources(source, what, tables, licence, credit, url, refresh_days)
                values (?,?,?,?,?,?,?) on conflict(source) do update set what = excluded.what,
                tables = excluded.tables, licence = excluded.licence, credit = excluded.credit, url = excluded.url,
                refresh_days = excluded.refresh_days""", s)
        conn.commit()


REG_START, REG_END = "<!-- register:start -->", "<!-- register:end -->"


def register_html():
    import html as H
    rows = []
    for src, what, _tables, lic, credit, url, days in SOURCES:
        rows.append("<tr><td><a href=\"{}\">{}</a></td><td data-l=\"Uses\">{}</td><td data-l=\"Terms\">{}</td>"
                    "<td data-l=\"Credit\">{}</td><td data-l=\"Refresh (days)\">{}</td></tr>".format(
            H.escape(url), H.escape(src), H.escape(what), H.escape(lic), H.escape(credit if credit != "-" else "not used"),
            H.escape(str(days) if days else "-")))
    return ("<table class=\"reg\"><thead><tr><th>Source</th><th>What Movie Buff uses</th><th>Terms and our rule</th>"
            "<th>Credit shown</th><th>Refresh (days)</th></tr></thead><tbody>\n" + "\n".join(rows) + "\n</tbody></table>")


def register_md():
    esc = lambda x: str(x).replace("|", "\\|")  # noqa: E731
    out = ["| Source | What | Tables | Terms and our rule | Credit | Refresh (days) |", "|---|---|---|---|---|---|"]
    for src, what, tables, lic, credit, url, days in SOURCES:
        out.append(f"| [{src}]({url}) | {esc(what)} | `{tables}` | {esc(lic)} | {esc(credit)} | {days or '-'} |")
    return "\n".join(out)


def cmd_register(a, conn=None):
    """E6: write the licensing register into web/about.html and README.md between the register markers."""
    for path, body in ((os.path.join(HERE, "web", "about.html"), register_html()), (os.path.join(HERE, "README.md"),
                                                                                  register_md())):
        if not os.path.exists(path):
            print(f"  {path}: missing")
            continue
        s = open(path, encoding="utf-8").read()
        if REG_START not in s or REG_END not in s:
            print(f"  {path}: no {REG_START} / {REG_END} markers; not changed")
            continue
        new = s[: s.index(REG_START) + len(REG_START)] + "\n" + body + "\n" + s[s.index(REG_END):]
        if new != s:
            open(path, "w", encoding="utf-8").write(new)
        print(f"  {path}: register {'updated' if new != s else 'already current'} ({len(SOURCES)} sources)")


def cmd_sources(a, conn):
    seed_sources(conn)
    print("| Source | What | Licence | Credit | Refresh (days) | Last run |\n| --- | --- | --- | --- | --- | --- |")
    for r in conn.execute("select source, what, licence, credit, refresh_days, last_run from data_sources "
                          "order by source"):
        print("| " + " | ".join("" if x is None else str(x) for x in r) + " |")


# ------------------------------------------------------------------ downloads
def download(url, path, max_age_days=7, again=False):
    """url -> path (kept and reused while younger than max_age_days)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not again and time.time() - os.path.getmtime(path) < max_age_days * 86400:
        return path
    print(f"  downloading {url} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0, n = time.time(), 0
    contexts = [None]
    try:                                       # 24 Oct: files.grouplens.org failed the system CA check on the server
        import certifi
        import ssl
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        pass
    err = None
    for ctx in contexts:
        try:
            with urllib.request.urlopen(req, timeout=600, context=ctx) as r, open(path + ".part", "wb") as out:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
                    n += len(b)
                    if n % (100 << 20) < (1 << 20):
                        print(f"    {n / 1e6:,.0f} MB", flush=True)
            err = None
            break
        except urllib.error.URLError as e:
            err, n = e, 0
            print(f"    {'system' if ctx is None else 'certifi'} certificates: {e.reason}", flush=True)
    if err is not None:                        # last resort: curl or wget (their own CA stores), same URL, same file
        import shutil
        import subprocess
        tool = shutil.which("curl") or shutil.which("wget")
        if not tool:
            raise err
        cmd = ([tool, "-fL", "--retry", "3", "-A", UA, "-o", path + ".part", url] if tool.endswith("curl")
               else [tool, "-q", "-U", UA, "-O", path + ".part", url])
        print(f"    trying {os.path.basename(tool)}", flush=True)
        subprocess.run(cmd, check=True)
        n = os.path.getsize(path + ".part")
    os.replace(path + ".part", path)
    print(f"  {n / 1e6:,.1f} MB in {time.time() - t0:.0f}s", flush=True)
    return path


# ------------------------------------------------------------------ tv (TMDB)
TV_SKIP_GENRES = "10763,10767"            # News, Talk


def tmdb_auth():
    key = V.tmdb_key()
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, ""
    return None, f"&api_key={key}"


def tv_discover(a, conn, headers, auth):
    rows, page, pages = [], 1, 1
    while page <= min(pages, 500) and len(rows) < a.max_shows:
        d = V.http_json(f"https://api.themoviedb.org/3/discover/tv?sort_by=vote_count.desc&vote_count.gte="
                        f"{a.min_votes}&without_genres={TV_SKIP_GENRES}&include_adult=false&language=en-US"
                        f"&page={page}{auth}", headers)
        if d is V.FAILED or not d:
            break
        pages = d.get("total_pages") or 1
        for s in d.get("results") or []:
            rows.append((s["id"], s.get("name"), s.get("vote_count"), len(rows) + 1))
        if page % 50 == 0:
            print(f"  discover page {page}/{min(pages, 500)}: {len(rows):,} shows", flush=True)
        page += 1
    rows = rows[:a.max_shows]
    ts = F.now()
    with _lock:
        conn.execute("delete from tv_list")
        conn.executemany("insert or replace into tv_list values (?,?,?,?,?)", [(*r, ts) for r in rows])
        conn.commit()
    print(f"  discover: {len(rows):,} shows with >= {a.min_votes} votes (news and talk shows left out)", flush=True)
    return [r[0] for r in rows]


def _year(d):
    return int(d[:4]) if d and d[:4].isdigit() else None


def tv_rows(sid, d, regions):
    """One TMDB /tv/{id} reply (with appends) -> rows for every tv_* table."""
    ext = d.get("external_ids") or {}
    rt = d.get("episode_run_time") or []
    show = (sid, ext.get("imdb_id") or None, ext.get("tvdb_id"), ext.get("wikidata_id") or None, d.get("name"),
            d.get("original_name"), d.get("first_air_date") or None, d.get("last_air_date") or None,
            _year(d.get("first_air_date")),
            _year(d.get("last_air_date")) if d.get("status") in ("Ended", "Canceled") else None,
            d.get("status"), d.get("type"), 1 if d.get("in_production") else 0, d.get("number_of_seasons"),
            d.get("number_of_episodes"), int(sum(rt) / len(rt)) if rt else None, d.get("original_language"),
            json.dumps(d.get("origin_country") or []), json.dumps([g["name"] for g in d.get("genres") or []]),
            json.dumps([n.get("name") for n in d.get("networks") or []]),
            json.dumps([c.get("name") for c in d.get("production_companies") or []]),
            json.dumps([{"id": c.get("id"), "name": c.get("name")} for c in d.get("created_by") or []]),
            d.get("overview") or None, d.get("tagline") or None, d.get("popularity"), d.get("vote_average"),
            d.get("vote_count"), d.get("poster_path"), d.get("backdrop_path"))
    seasons = [(sid, s.get("season_number"), s.get("name"), s.get("air_date"), s.get("episode_count"),
                (s.get("overview") or None), s.get("vote_average"), s.get("poster_path"))
               for s in d.get("seasons") or []]
    credits = []
    for c in d.get("created_by") or []:
        credits.append((sid, c.get("id"), c.get("name"), "crew", "Creator", None, None, -1, c.get("profile_path")))
    ag = d.get("aggregate_credits") or {}
    for i, c in enumerate(sorted(ag.get("cast") or [], key=lambda c: c.get("order", 999))[:20]):
        roles = c.get("roles") or [{}]
        credits.append((sid, c.get("id"), c.get("name"), "cast", "Actor", roles[0].get("character"),
                        c.get("total_episode_count"), i, c.get("profile_path")))
    keep = {"Director", "Writer", "Screenplay", "Executive Producer", "Showrunner", "Original Music Composer",
            "Director of Photography", "Novel", "Comic Book", "Characters"}
    crew = []
    for c in ag.get("crew") or []:
        for j in c.get("jobs") or []:
            if j.get("job") in keep:
                crew.append((c.get("total_episode_count") or 0, c, j.get("job")))
    crew.sort(key=lambda x: -x[0])
    for n, c, job in crew[:30]:
        credits.append((sid, c.get("id"), c.get("name"), "crew", job, None, n, None, c.get("profile_path")))
    kws = [(sid, k.get("id"), k.get("name")) for k in (d.get("keywords") or {}).get("results") or []]
    vids = [(sid, v["key"], v.get("type"), (v.get("name") or "")[:200], 1 if v.get("official") else 0,
             v.get("published_at")) for v in (d.get("videos") or {}).get("results") or []
            if v.get("site") == "YouTube" and v.get("key")]
    rats = [(sid, r.get("iso_3166_1"), r.get("rating")) for r in (d.get("content_ratings") or {}).get("results") or []
            if r.get("rating") and r.get("iso_3166_1") in set(regions) | {"US", "GB"}]
    provs = []
    res = (d.get("watch/providers") or {}).get("results") or {}
    for reg in regions:
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            for p in (res.get(reg) or {}).get(kind) or []:
                provs.append((sid, reg, kind, p.get("provider_id"), p.get("provider_name"), p.get("display_priority")))
    revs = [("tv:" + r["id"], sid, r.get("author"), (r.get("author_details") or {}).get("rating"), r.get("created_at"),
             r.get("url"), r.get("content")) for r in (d.get("reviews") or {}).get("results") or [] if r.get("id")]
    return show, seasons, credits, kws, vids, rats, provs, revs


def cmd_tv(a, conn):
    headers, auth = tmdb_auth()
    ids = tv_discover(a, conn, headers, auth)
    if not a.again:
        have = {r[0] for r in conn.execute("select tmdb_id from tv_shows")}
        ids = [i for i in ids if i not in have]
    if a.limit:
        ids = ids[:a.limit]
    regions = [r.strip().upper() for r in a.regions.split(",") if r.strip()]
    print(f"tv: {len(ids):,} shows to fetch", flush=True)

    def work(sid):
        d = V.http_json(f"https://api.themoviedb.org/3/tv/{sid}?append_to_response=aggregate_credits,keywords,videos,"
                        f"content_ratings,watch/providers,external_ids,reviews&language=en-US"
                        f"&include_video_language=en,null{auth}", headers)
        if d is V.FAILED or d is None:
            return None
        return tv_rows(sid, d, regions)

    def store(results):
        ts = F.now()
        with _lock:
            for show, seasons, credits, kws, vids, rats, provs, revs in results:
                sid = show[0]
                for t in ("tv_seasons", "tv_credits", "tv_keywords", "tv_videos", "tv_ratings", "tv_providers"):
                    conn.execute(f"delete from {t} where tmdb_id = ?", (sid,))
                conn.execute("insert or replace into tv_shows values (" + ",".join("?" * 30) + ")", (*show, ts))
                conn.executemany("insert or replace into tv_seasons values (?,?,?,?,?,?,?,?)", seasons)
                conn.executemany("insert into tv_credits values (?,?,?,?,?,?,?,?,?)", credits)
                conn.executemany("insert into tv_keywords values (?,?,?)", kws)
                conn.executemany("insert or replace into tv_videos values (?,?,?,?,?,?)", vids)
                conn.executemany("insert or replace into tv_ratings values (?,?,?)", rats)
                conn.executemany("insert or replace into tv_providers values (?,?,?,?,?,?)", provs)
                conn.executemany("insert or replace into tv_reviews values (?,?,?,?,?,?,?,?)", [(*r, ts) for r in revs])
            conn.commit()

    st = V.run_pool(ids, work, a.workers, "tv", store, batch=200)
    F.set_meta(conn, "tv_fetched_at", F.now())
    touch_source(conn, "tmdb")
    print(f"tv done: {st}")


# ------------------------------------------------------------------ tv-wiki (Wikipedia)
TV_WORDS = ("television series", "tv series", "miniseries", "mini-series", "sitcom", "anime", "web series",
            "television program", "television show", "animated series", "drama series", "comedy series",
            "limited series", "anthology series", "soap opera", "docuseries", "reality")


def _get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            wait = 5 * (i + 1)
            if e.code == 429:
                try:
                    wait = max(wait, float(e.headers.get("Retry-After") or 0))
                except ValueError:
                    pass
            print(f"  HTTP {e.code} ({url[:80]}), waiting {wait:.0f}s", file=sys.stderr, flush=True)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            print(f"  {str(e)[:80]} ({url[:80]})", file=sys.stderr, flush=True)
            time.sleep(3 * (i + 1))
    return None


def looks_like_show(extract, year):
    lead = (extract or "")[:800].lower()
    return any(w in lead for w in TV_WORDS) and (not year or any(str(y) in lead for y in (year - 1, year, year + 1)))


def cmd_tv_wiki(a, conn):
    rows = conn.execute("select tmdb_id, name, first_year, wikidata_id from tv_shows order by vote_count desc").fetchall()
    done = set() if a.again else {r[0] for r in conn.execute("select tmdb_id from tv_wiki")}
    todo = [r for r in rows if r[0] not in done][: a.limit or None]
    print(f"tv-wiki: {len(rows):,} shows, {len(todo):,} to look up", flush=True)
    by_q = {}
    qids = [r[3] for r in todo if r[3]]
    for s in range(0, len(qids), 50):
        d = _get_json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": "|".join(qids[s:s + 50]), "props": "sitelinks",
             "sitefilter": "enwiki", "format": "json"}))
        for q, ent in ((d or {}).get("entities") or {}).items():
            t = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
            if t:
                by_q[q] = t
        time.sleep(0.5)
    stats = {"wikidata": 0, "search": 0, "none": 0}
    for i, (sid, name, year, qid) in enumerate(todo, 1):
        wt, method = by_q.get(qid), "wikidata"
        if not wt:
            method = "search"
            d = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": f"{name} TV series", "srlimit": 5, "format": "json"}))
            key = re.sub(r"\W+", " ", (name or "").lower()).strip()
            for hit in ((d or {}).get("query") or {}).get("search") or []:
                ht = re.sub(r"\s*\(.*?\)\s*$", "", hit["title"])
                if re.sub(r"\W+", " ", ht.lower()).strip() == key:
                    wt = hit["title"]
                    break
        page = None
        if wt:
            d = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "wiki",
                 "inprop": "url", "titles": wt, "redirects": 1, "format": "json", "formatversion": 2}))
            pages = ((d or {}).get("query") or {}).get("pages") or []
            if pages and not pages[0].get("missing"):
                p = pages[0]
                if method == "wikidata" or looks_like_show(p.get("extract"), year):
                    page = p
        ts = F.now()
        with _lock:
            if page:
                stats[method] += 1
                conn.execute("insert or replace into tv_wiki values (?,?,?,?,?,?,?,?)",
                             (sid, page.get("pageid"), page.get("title"), page.get("fullurl"), method,
                              page.get("extract") or "", ts, "ok"))
            else:
                stats["none"] += 1
                conn.execute("insert or replace into tv_wiki values (?,?,?,?,?,?,?,?)",
                             (sid, None, None, None, method, None, ts, "none"))
            if i % 25 == 0 or i == len(todo):
                conn.commit()
        if i % 100 == 0 or i == len(todo):
            print(f"  {i:,}/{len(todo):,}  " + ", ".join(f"{k} {v}" for k, v in stats.items()), flush=True)
        time.sleep(0.3)
    conn.commit()
    touch_source(conn, "wikipedia")


# ------------------------------------------------------------------ imdb (non-commercial datasets)
IMDB_BASE = "https://datasets.imdbws.com/"
IMDB_FILES = ["title.episode.tsv.gz", "title.basics.tsv.gz", "title.ratings.tsv.gz", "title.akas.tsv.gz",
              "title.crew.tsv.gz", "title.principals.tsv.gz", "name.basics.tsv.gz"]


def _n(x):
    return None if x == "\\N" or x == "" else x


def _i(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def tsv_rows(path, want=None, col=0):
    """Rows of an IMDb .tsv.gz whose column `col` is in `want` (all rows if want is None). Fast path: the key is
    checked before the line is split."""
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as f:
        f.readline()                                          # header
        for line in f:
            if want is not None:
                if col == 0:
                    key = line[:line.find("\t")]
                else:
                    key = line.split("\t", col + 1)[col]
                if key not in want:
                    continue
            yield line.rstrip("\n").split("\t")


def write_table(conn, table, rows, ncols, batch=50000, clear=True):
    """Replace a table's rows (clear=False: upsert only, so readers never see it empty)."""
    n = 0
    if clear:
        with _lock:
            conn.execute(f"delete from {table}")
            conn.commit()
    buf = []
    for r in rows:
        buf.append(r)
        if len(buf) >= batch:
            with _lock:
                conn.executemany(f"insert or replace into {table} values ({','.join('?' * ncols)})", buf)
                conn.commit()
            n += len(buf)
            buf = []
    if buf:
        with _lock:
            conn.executemany(f"insert or replace into {table} values ({','.join('?' * ncols)})", buf)
            conn.commit()
        n += len(buf)
    return n


def cmd_imdb(a, conn):
    d = os.path.join(DATA, "imdb")
    paths = {f: (os.path.join(d, f) if a.offline else download(IMDB_BASE + f, os.path.join(d, f), again=a.again))
             for f in IMDB_FILES}
    films = {r[0] for r in conn.execute("select imdb_id from films where imdb_id like 'tt%'")}
    shows = {r[0] for r in conn.execute("select imdb_id from tv_shows where imdb_id like 'tt%'")}
    print(f"imdb: our ids: {len(films):,} films, {len(shows):,} TV shows", flush=True)
    t0 = time.time()
    eps = [(r[0], r[1], _i(r[2]), _i(r[3])) for r in tsv_rows(paths["title.episode.tsv.gz"], shows, col=1)]
    n = write_table(conn, "imdb_episodes", iter(eps), 4)
    print(f"  episodes of our shows: {n:,} ({time.time() - t0:.0f}s)", flush=True)
    want = films | shows | {e[0] for e in eps}
    main = films | shows
    n = write_table(conn, "imdb_titles", ((r[0], _n(r[1]), _n(r[2]), _n(r[3]), _i(r[4]), _i(_n(r[5])), _i(_n(r[6])),
                                           _i(_n(r[7])), _n(r[8])) for r in tsv_rows(paths["title.basics.tsv.gz"], want)), 9)
    print(f"  titles: {n:,} ({time.time() - t0:.0f}s)", flush=True)
    ts = F.now()
    n = write_table(conn, "imdb_ratings", ((r[0], float(r[1]), int(r[2]), ts)
                                           for r in tsv_rows(paths["title.ratings.tsv.gz"], want)), 4, clear=False)
    print(f"  ratings: {n:,} (films, shows and their episodes) ({time.time() - t0:.0f}s)", flush=True)
    n = write_table(conn, "imdb_akas", ((r[0], _i(r[1]), r[2], _n(r[3]), _n(r[4]), _n(r[5]), _n(r[6]), _i(r[7]))
                                        for r in tsv_rows(paths["title.akas.tsv.gz"], main)), 8)
    print(f"  alternate titles: {n:,} ({time.time() - t0:.0f}s)", flush=True)
    people = set()
    crew = []
    for r in tsv_rows(paths["title.crew.tsv.gz"], main):
        crew.append((r[0], _n(r[1]), _n(r[2])))
        for x in (r[1], r[2]):
            if x != "\\N":
                people.update(x.split(","))
    n = write_table(conn, "imdb_crew", iter(crew), 3)
    print(f"  directors/writers rows: {n:,} ({time.time() - t0:.0f}s)", flush=True)

    def principals():
        for r in tsv_rows(paths["title.principals.tsv.gz"], main):
            people.add(r[2])
            yield (r[0], _i(r[1]), r[2], _n(r[3]), _n(r[4]), _n(r[5]))
    n = write_table(conn, "imdb_principals", principals(), 6)
    print(f"  principal cast/crew: {n:,} ({time.time() - t0:.0f}s)", flush=True)
    n = write_table(conn, "imdb_names", ((r[0], r[1], _i(_n(r[2])), _i(_n(r[3])), _n(r[4]), _n(r[5]))
                                         for r in tsv_rows(paths["name.basics.tsv.gz"], people)), 6)
    print(f"  people: {n:,} ({time.time() - t0:.0f}s)", flush=True)
    F.set_meta(conn, "imdb_datasets_loaded_at", F.now())
    conn.commit()
    touch_source(conn, "imdb")
    if not a.keep and not a.offline:
        for p in paths.values():
            if "principals" in p or "akas" in p:          # the two largest; the others are reused next week
                try:
                    os.remove(p)
                except OSError:
                    pass


# ------------------------------------------------------------------ movielens 32M: distributions (R8)
ML32_URL = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"
ML25_URL = "https://files.grouplens.org/datasets/movielens/ml-25m.zip"
BAYES_C = 50                    # prior strength (ratings) for the Bayesian mean
MIN_N = 100                     # below this, ml_dist is stored but tools must not generalise (E3)


def _zip_reader(z, name):
    member = next(n for n in z.namelist() if n.endswith("/" + name) or n == name)
    return io.TextIOWrapper(z.open(member), encoding="utf-8", newline="")


def ml_links(z):
    to_tmdb, year = {}, {}
    for r in csv.DictReader(_zip_reader(z, "links.csv")):
        if (r.get("tmdbId") or "").isdigit():
            to_tmdb[r["movieId"]] = int(r["tmdbId"])
    for r in csv.DictReader(_zip_reader(z, "movies.csv")):
        m = re.search(r"\((\d{4})\)\s*$", r.get("title") or "")
        if m:
            year[r["movieId"]] = int(m.group(1))
    return to_tmdb, year


def dist_stats(hist, n, global_mean):
    """hist = counts in 10 half-star bins (0.5 .. 5.0) -> mean, sd, bayes mean, share low/high, bimodality coef."""
    vals = [0.5 * (i + 1) for i in range(10)]
    mean = sum(v * c for v, c in zip(vals, hist)) / n
    var = sum(c * (v - mean) ** 2 for v, c in zip(vals, hist)) / n
    sd = math.sqrt(var)
    low = sum(hist[:4]) / n                     # 0.5 - 2.0 stars
    high = sum(hist[8:]) / n                    # 4.5 - 5.0 stars
    bc = None
    if n > 3 and sd > 0:
        m3 = sum(c * (v - mean) ** 3 for v, c in zip(vals, hist)) / n
        m4 = sum(c * (v - mean) ** 4 for v, c in zip(vals, hist)) / n
        g1 = m3 / sd ** 3                                        # sample skewness (adjusted)
        g1 = g1 * math.sqrt(n * (n - 1)) / (n - 2)
        g2 = m4 / sd ** 4 - 3                                    # excess kurtosis (adjusted)
        g2 = ((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3))
        bc = (g1 ** 2 + 1) / (g2 + 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))
    bayes = (BAYES_C * global_mean + mean * n) / (BAYES_C + n)
    return mean, sd, bayes, low, high, bc


def cmd_movielens(a, conn):
    path = os.path.join(DATA, "movielens", "ml-32m.zip")
    if not a.offline:
        download(ML32_URL, path, max_age_days=3650)
    z = zipfile.ZipFile(path)
    to_tmdb, fyear = ml_links(z)
    # year starts (UTC) for 1995..2030, so a timestamp's year is one bisect, not a datetime per rating
    starts = [calendar.timegm((y, 1, 1, 0, 0, 0)) for y in range(1995, 2031)]
    hist, years = {}, {}
    total = tot_sum = 0
    t0 = time.time()
    f = _zip_reader(z, "ratings.csv")
    f.readline()
    for k, line in enumerate(f):
        _u, m, r, ts = line.rstrip().split(",")
        t = to_tmdb.get(m)
        if t is None:
            continue
        r = float(r)
        b = min(max(int(round(r * 2)) - 1, 0), 9)
        h = hist.get(t)
        if h is None:
            h = hist[t] = [0] * 10
        h[b] += 1
        y = 1995 + bisect.bisect_right(starts, int(ts)) - 1
        key = (t, y)
        s = years.get(key)
        if s is None:
            years[key] = [1, r]
        else:
            s[0] += 1
            s[1] += r
        total += 1
        tot_sum += r
        if k % 5_000_000 == 0 and k:
            print(f"  {k:,} ratings read ({time.time() - t0:.0f}s)", flush=True)
    gmean = tot_sum / max(total, 1)
    film_year = {to_tmdb[m]: y for m, y in fyear.items() if m in to_tmdb}
    by_film = {}
    for (t, y), (n, s) in years.items():
        by_film.setdefault(t, []).append((y, n, s))
    rows = []
    for t, h in hist.items():
        n = sum(h)
        mean, sd, bayes, low, high, bc = dist_stats(h, n, gmean)
        ys = sorted(by_film.get(t, []))
        fy = film_year.get(t)
        early = late = (0, 0.0)
        if fy and fy >= 1996:                 # MovieLens starts in 1995: "early" means near release only after that
            e = [(n_, s_) for y, n_, s_ in ys if fy <= y <= fy + 2]
            lt = [(n_, s_) for y, n_, s_ in ys if y >= fy + 5]
            early = (sum(x[0] for x in e), sum(x[1] for x in e))
            late = (sum(x[0] for x in lt), sum(x[1] for x in lt))
        rows.append((t, n, round(mean, 4), round(sd, 4), round(bayes, 4), json.dumps(h), round(low, 4), round(high, 4),
                     None if bc is None else round(bc, 4), fy, early[0], round(early[1] / early[0], 4) if early[0] else None,
                     late[0], round(late[1] / late[0], 4) if late[0] else None, ys[0][0] if ys else None,
                     ys[-1][0] if ys else None))
    yrows = [(t, y, n, round(s / n, 4)) for (t, y), (n, s) in years.items()]
    write_table(conn, "ml_dist", iter(rows), 16)
    write_table(conn, "ml_year", iter(yrows), 4)
    F.set_meta(conn, "ml_dist_built_at", F.now())
    F.set_meta(conn, "ml_global_mean", round(gmean, 4))
    conn.commit()
    touch_source(conn, "movielens")
    big = sum(1 for r in rows if r[1] >= MIN_N)
    print(f"movielens: {total:,} ratings, {len(rows):,} films ({big:,} with >= {MIN_N} ratings), "
          f"{len(yrows):,} film-years; global mean {gmean:.3f} ({time.time() - t0:.0f}s)")


# ------------------------------------------------------------------ tag genome (R9)
def cmd_genome(a, conn):
    path = os.path.join(DATA, "movielens", "ml-25m.zip")
    if not a.offline:
        download(ML25_URL, path, max_age_days=3650)
    import numpy as np
    z = zipfile.ZipFile(path)
    to_tmdb, _y = ml_links(z)
    tags = {r["tagId"]: r["tag"] for r in csv.DictReader(_zip_reader(z, "genome-tags.csv"))}
    tag_ids = sorted(tags, key=int)
    col = {t: i for i, t in enumerate(tag_ids)}
    rows_of, mat = {}, []
    t0 = time.time()
    f = _zip_reader(z, "genome-scores.csv")
    f.readline()
    for line in f:
        m, tg, rel = line.rstrip().split(",")
        t = to_tmdb.get(m)
        if t is None:
            continue
        i = rows_of.get(t)
        if i is None:
            i = rows_of[t] = len(mat)
            mat.append(np.zeros(len(tag_ids), dtype=np.float32))
        mat[i][col[tg]] = float(rel)
    ids = sorted(rows_of, key=rows_of.get)
    M = np.vstack(mat) if mat else np.zeros((0, len(tag_ids)), dtype=np.float32)
    os.makedirs(os.path.join(DATA, "genome"), exist_ok=True)
    np.savez_compressed(os.path.join(DATA, "genome", "genome.npz"), tmdb_ids=np.array(ids, dtype=np.int64),
                        tags=np.array([tags[t] for t in tag_ids]), relevance=M)
    out = []
    for t, i in rows_of.items():
        top = np.argsort(-M[i])[:a.top]
        out.extend((t, tags[tag_ids[j]], round(float(M[i][j]), 4), r + 1) for r, j in enumerate(top))
    n = write_table(conn, "film_tags", iter(out), 4)
    F.set_meta(conn, "genome_built_at", F.now())
    conn.commit()
    touch_source(conn, "movielens")
    print(f"genome: {len(ids):,} films x {len(tag_ids):,} tags; film_tags {n:,} rows (top {a.top}); "
          f"matrix in data/genome/genome.npz ({time.time() - t0:.0f}s)")


# ------------------------------------------------------------------ reception wikitext (R2)
WANT_KINDS = ("reception", "box_office", "legacy")
_HEAD = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
_REF_DEF = re.compile(r"<ref\s+name\s*=\s*\"?([^\"/>]+?)\"?\s*>(.*?)</ref>", re.S | re.I)
_REF_USE = re.compile(r"<ref\s+name\s*=\s*\"?([^\"/>]+?)\"?\s*/>", re.I)


def heading_kind(h):
    try:
        import wiki_sections as WS
        return WS.kind_of(h)
    except Exception:  # noqa: BLE001 - tests run without the repo
        hl = h.lower()
        for k, words in (("box_office", ("box office", "commercial", "gross")),
                         ("reception", ("reception", "critical", "response", "reviews", "critics")),
                         ("legacy", ("legacy", "impact", "influence", "popular culture"))):
            if any(w in hl for w in words):
                return k
        return None


def reception_sections(wikitext):
    """[(kind, heading, level, text, refs{name: definition})] for every Reception / Box office / Legacy section,
    any heading level ("== Release ==" / "=== Critical response ===" counts). A section includes its subsections;
    a heading inside a section already taken is not taken again."""
    heads = [(m.start(), m.end(), len(m.group(1)), re.sub(r"<[^>]+>|\[\[|\]\]|'''?", "", m.group(2)).strip())
             for m in _HEAD.finditer(wikitext)]
    defs = {}
    for m in _REF_DEF.finditer(wikitext):
        defs.setdefault(m.group(1).strip(), m.group(0))
    out, taken_until = [], -1
    for i, (s, e, lvl, h) in enumerate(heads):
        if s < taken_until:
            continue
        k = heading_kind(h)
        if k not in WANT_KINDS:
            continue
        end = len(wikitext)
        for s2, _e2, lvl2, _h2 in heads[i + 1:]:
            if lvl2 <= lvl:
                end = s2
                break
        text = wikitext[e:end].strip()
        if len(text) < 80:
            continue
        local = {m.group(1).strip() for m in _REF_DEF.finditer(text)}
        refs = {n: defs[n] for n in {m.group(1).strip() for m in _REF_USE.finditer(text)} - local if n in defs}
        out.append((k, h, lvl, text, refs))
        taken_until = end
    return out


def film_pages(conn, wiki_db):
    """[(tmdb_id, pageid)] for every film linked to a Wikipedia page, most-voted first. TREC doc ids ARE page ids;
    recent films are 'live:<pageid>'."""
    if not os.path.exists(wiki_db):
        sys.exit(f"{wiki_db} not found")
    w = sqlite3.connect(f"file:{wiki_db}?mode=ro", uri=True)
    pid = {}
    for did, fid in w.execute("select doc_id, film_id from pages where film_id is not null"):
        d = str(did).replace("live:", "")
        if d.isdigit() and int(fid) not in pid:
            pid[int(fid)] = int(d)
    w.close()
    votes = dict(conn.execute("select tmdb_id, coalesce(vote_count, 0) from films"))
    return sorted(pid.items(), key=lambda kv: -votes.get(kv[0], 0))


def cmd_reception(a, conn):
    pages = film_pages(conn, a.wiki)
    done = {r[0]: r[1] for r in conn.execute("select pageid, revid from wiki_reception_done")}
    todo = pages if a.again else [p for p in pages if p[1] not in done]
    if a.limit:
        todo = todo[:a.limit]
    by_pid = {}
    for fid, p in todo:
        by_pid.setdefault(p, fid)                  # two films on one page: the more-voted one keeps it
    pids = list(by_pid)
    print(f"reception: {len(pages):,} films with a Wikipedia page; {len(pids):,} pages to fetch "
          f"({a.batch} per request)", flush=True)
    if not CONTACT:
        print("  note: set MB_CONTACT (your email) so Wikimedia can reach you instead of throttling", flush=True)
    stats = {"pages": 0, "with_sections": 0, "sections": 0, "missing": 0, "unchanged": 0}
    t0 = time.time()
    for s in range(0, len(pids), a.batch):
        chunk = pids[s:s + a.batch]
        params = {"action": "query", "prop": "revisions", "rvprop": "ids|content", "rvslots": "main",
                  "pageids": "|".join(map(str, chunk)), "format": "json", "formatversion": 2, "maxlag": 5}
        got = {}
        while True:
            d = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params), tries=5)
            if d is None:
                break
            if (d.get("error") or {}).get("code") == "maxlag":
                time.sleep(5)
                continue
            for p in (d.get("query") or {}).get("pages") or []:
                if p.get("revisions"):
                    rv = p["revisions"][0]
                    got[p["pageid"]] = (rv.get("revid"), ((rv.get("slots") or {}).get("main") or {}).get("content") or "")
                elif p.get("missing") or p.get("invalid"):
                    got.setdefault(p.get("pageid"), None)
            cont = d.get("continue")
            if not cont:
                break
            params.update(cont)                    # very long pages: the rest of the batch comes next
        ts = F.now()
        with _lock:
            for p in chunk:
                fid = by_pid[p]
                stats["pages"] += 1
                if p not in got or got[p] is None:
                    stats["missing"] += 1
                    conn.execute("insert or replace into wiki_reception_done values (?,?,?,?,?,?)",
                                 (p, fid, None, "missing", 0, ts))
                    continue
                revid, text = got[p]
                if a.again and done.get(p) == revid:
                    stats["unchanged"] += 1
                    continue
                secs = reception_sections(text)
                conn.execute("delete from wiki_reception where pageid = ?", (p,))
                conn.executemany("insert into wiki_reception(tmdb_id, pageid, revid, kind, heading, level, wikitext_z, "
                                 "refs_z, fetched_at) values (?,?,?,?,?,?,?,?,?)",
                                 [(fid, p, revid, k, h, lvl, zlib.compress(t.encode()),
                                   zlib.compress(json.dumps(refs).encode()), ts) for k, h, lvl, t, refs in secs])
                conn.execute("insert or replace into wiki_reception_done values (?,?,?,?,?,?)",
                             (p, fid, revid, "ok", len(secs), ts))
                stats["with_sections"] += bool(secs)
                stats["sections"] += len(secs)
            conn.commit()
        if (s // a.batch) % 25 == 0 or s + a.batch >= len(pids):
            el = time.time() - t0
            print(f"  {min(s + a.batch, len(pids)):,}/{len(pids):,} pages  " +
                  ", ".join(f"{k} {v:,}" for k, v in stats.items()) +
                  f"  ({el / 60:.0f} min, ~{(len(pids) - s) / max((s + a.batch) / max(el, 1), 1e-6) / 60:.0f} min left)",
                  flush=True)
        time.sleep(a.sleep)
    F.set_meta(conn, "wiki_reception_fetched_at", F.now())
    conn.commit()
    touch_source(conn, "wikipedia")


def reception_text(conn, tmdb_id):
    """Helper for Monday's extractor and for checks: [(kind, heading, wikitext, refs)] for one film."""
    return [(k, h, zlib.decompress(t).decode(), json.loads(zlib.decompress(r).decode()))
            for k, h, t, r in conn.execute("select kind, heading, wikitext_z, refs_z from wiki_reception where "
                                           "tmdb_id = ? order by id", (tmdb_id,))]


# ------------------------------------------------------------------ themes / controversy sections (4 Nov)
# For questions about the issues a film raised ("how did people view Joker's portrayal of mental illness"): the
# film's Wikipedia sections on themes, analysis, interpretation, controversy, depiction / portrayal, as plain text
# (critics.strip_markup, refs removed), into wiki_themes; evidence.py makes them "themes" units (switch ISSUES).
# The reception fetch kept only reception / box office / legacy, so pages are fetched again (revid kept; --again
# re-reads every page, otherwise only pages not done yet or whose revid changed since).
THEMES_DDL = """create table if not exists wiki_themes(tmdb_id integer, pageid integer, revid integer, kind text,
  heading text, text text, fetched_at text);
create index if not exists wiki_themes_film on wiki_themes(tmdb_id);
create table if not exists wiki_themes_done(pageid integer primary key, tmdb_id integer, revid integer, n integer,
  fetched_at text);"""
R_THEME_HEAD = re.compile(r"\b(themes?|thematic|analysis|interpretations?|symbolism|subtext|allegor\w*|meaning|"
                          r"motifs?|political|social commentary|philosoph\w*)\b", re.I)
R_CONTRO_HEAD = re.compile(r"\b(controvers\w*|backlash|accusations?|protests?|boycotts?|censorship|bans?|"
                           r"depictions? of|portrayals? of|representation|criticism of|accuracy|historical accuracy|"
                           r"inaccurac\w*|cultural impact)\b", re.I)
_REF_ANY = re.compile(r"<ref(?:\s[^>]*?)?/>|<ref(?:\s[^>]*)?>.*?</ref\s*>", re.S | re.I)
_HEAD_CLEAN = re.compile(r"<[^>]+>|\[\[|\]\]|'{2,3}")


def themes_sections(wikitext):
    """[(kind, heading, plain text)] for theme / controversy sections (any level; subsections included)."""
    try:
        import critics as _C
        strip = _C.strip_markup
    except Exception:  # noqa: BLE001
        def strip(t):
            t = re.sub(r"\{\{.*?\}\}", "", t, flags=re.S)
            return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
    heads = [(m.start(), m.end(), len(m.group(1)), _HEAD_CLEAN.sub("", m.group(2)).strip())
             for m in _HEAD.finditer(wikitext)]
    out, taken_until = [], -1
    for i, (st, en, lvl, h) in enumerate(heads):
        if st < taken_until:
            continue
        kind = "themes" if R_THEME_HEAD.search(h) else "controversy" if R_CONTRO_HEAD.search(h) else None
        if not kind:
            continue
        if kind == "themes" and re.search(r"\b(box office|release|cast|soundtrack|music)\b", h, re.I):
            continue
        end = len(wikitext)
        for s2, _e2, lvl2, _h2 in heads[i + 1:]:
            if lvl2 <= lvl:
                end = s2
                break
        body = _REF_ANY.sub("", wikitext[en:end])
        body = _HEAD.sub("\n\n", body)
        text = re.sub(r"[ \t]+", " ", strip(body)).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) >= 200:
            out.append((kind, h, text[:20000]))
            taken_until = end
    return out


def cmd_themes(a, conn):
    conn.executescript(THEMES_DDL)
    pages = film_pages(conn, a.wiki)
    done = {r[0]: r[1] for r in conn.execute("select pageid, revid from wiki_themes_done")}
    by_pid = {}
    for fid, p in pages:
        by_pid.setdefault(p, fid)
    pids = [p for p in by_pid if a.again or p not in done]
    if a.limit:
        pids = pids[:a.limit]
    print(f"themes: {len(by_pid):,} film pages; {len(pids):,} to fetch ({a.batch} per request)", flush=True)
    st = {"pages": 0, "with_sections": 0, "sections": 0, "themes": 0, "controversy": 0, "unchanged": 0}
    t0 = time.time()
    for s in range(0, len(pids), a.batch):
        chunk = pids[s:s + a.batch]
        params = {"action": "query", "prop": "revisions", "rvprop": "ids|content", "rvslots": "main",
                  "pageids": "|".join(map(str, chunk)), "format": "json", "formatversion": 2, "maxlag": 5}
        got = {}
        while True:
            d = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params), tries=5)
            if d is None:
                break
            if (d.get("error") or {}).get("code") == "maxlag":
                time.sleep(5)
                continue
            for p in (d.get("query") or {}).get("pages") or []:
                if p.get("revisions"):
                    rv = p["revisions"][0]
                    got[p["pageid"]] = (rv.get("revid"), ((rv.get("slots") or {}).get("main") or {}).get("content") or "")
            cont = d.get("continue")
            if not cont:
                break
            params.update(cont)
        ts = F.now()
        with _lock:
            for p in chunk:
                if p not in got:
                    continue
                revid, text = got[p]
                st["pages"] += 1
                if not a.again and done.get(p) == revid:
                    st["unchanged"] += 1
                    continue
                secs = themes_sections(text)
                fid = by_pid[p]
                conn.execute("delete from wiki_themes where pageid = ?", (p,))
                conn.executemany("insert into wiki_themes values (?,?,?,?,?,?,?)",
                                 [(fid, p, revid, k, h, t, ts) for k, h, t in secs])
                conn.execute("insert or replace into wiki_themes_done values (?,?,?,?,?)", (p, fid, revid, len(secs), ts))
                st["with_sections"] += bool(secs)
                st["sections"] += len(secs)
                for k, _h, _t in secs:
                    st[k] += 1
            conn.commit()
        if (s // a.batch) % 25 == 0 or s + a.batch >= len(pids):
            print(f"  {min(s + a.batch, len(pids)):,}/{len(pids):,} pages  " + ", ".join(f"{k} {v:,}" for k, v in st.items())
                  + f"  ({(time.time() - t0) / 60:.0f} min)", flush=True)
        time.sleep(a.sleep)
    F.set_meta(conn, "wiki_themes_fetched_at", F.now())
    conn.commit()


# ------------------------------------------------------------------ Wikidata review scores (E1, 27 Oct)
WD_SPARQL = "https://query.wikidata.org/sparql"
WD_SCORE_DDL = """create table if not exists wikidata_scores(tmdb_id integer, qid text, by_qid text, source text,
  score_text text, value real, scale real, n_reviews integer, method text, as_of text, fetched_at text);
create index if not exists wikidata_scores_film on wikidata_scores(tmdb_id, source);
create table if not exists wikidata_scores_done(qid text primary key, n integer, fetched_at text);"""
WD_SCORE_Q = """SELECT ?item ?score ?by ?byLabel ?time ?n ?methodLabel WHERE {
  VALUES ?item { %s }
  ?item p:P444 ?st . ?st ps:P444 ?score .
  OPTIONAL { ?st pq:P447 ?by . }
  OPTIONAL { ?st pq:P585 ?time . }
  OPTIONAL { ?st pq:P7887 ?n . }
  OPTIONAL { ?st pq:P459 ?method . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def _num_or_none(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_score(s):
    """'93%' -> (93, 100); '8.6/10' -> (8.6, 10); '90/100' -> (90, 100); 'A' (CinemaScore) -> (None, None)."""
    s = (s or "").strip()
    m = re.match(r"^([\d.]+)\s*%$", s)
    if m:
        return float(m.group(1)), 100.0
    m = re.match(r"^([\d.]+)\s*/\s*([\d.]+)$", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def cmd_wd_scores(a, conn):
    """E1: review scores (P444) stated on each film's Wikidata item, with who gave them (P447), when (P585), how
    many reviews (P7887) and the method (P459). Open data (CC0), dated, for films OMDb / Wikipedia text lack."""
    conn.executescript(WD_SCORE_DDL)
    films = conn.execute("select tmdb_id, wikidata_id from films where wikidata_id like 'Q%' order by vote_count desc"
                         ).fetchall()
    done = set() if a.again else {r[0] for r in conn.execute("select qid from wikidata_scores_done")}
    todo = [(f, q) for f, q in films if q not in done][: a.limit or None]
    by_q = {}
    for f, q in todo:
        by_q.setdefault(q, f)
    qids = list(by_q)
    print(f"wd-scores: {len(films):,} films with a Wikidata id, {len(qids):,} to query", flush=True)
    n_rows, t0 = 0, time.time()
    for s in range(0, len(qids), a.wd_batch):
        part = qids[s:s + a.wd_batch]
        q = WD_SCORE_Q % " ".join(f"wd:{x}" for x in part)
        d = _get_json(WD_SPARQL + "?" + urllib.parse.urlencode({"query": q, "format": "json"}), tries=4)
        if d is None:
            print(f"  batch at {s} failed; it is retried next run", flush=True)
            time.sleep(10)
            continue
        rows = []
        for b in (d.get("results") or {}).get("bindings") or []:
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            txt = (b.get("score") or {}).get("value")
            val, scale = parse_score(txt)
            by = (b.get("by") or {}).get("value", "").rsplit("/", 1)[-1] or None
            src = (b.get("byLabel") or {}).get("value") or by
            n = _num_or_none((b.get("n") or {}).get("value"))   # 28 Oct: "unknown value" comes back as a blank node URL
            when = (b.get("time") or {}).get("value") or ""
            when = when[:10] if re.match(r"^-?\d{4}", when) else None
            rows.append((by_q.get(qid), qid, by, src, txt, val, scale, int(n) if n is not None else None,
                         (b.get("methodLabel") or {}).get("value"), when, F.now()))
        with _lock:
            conn.executemany("delete from wikidata_scores where qid = ?", [(x,) for x in part])
            # a Metascore is a whole number 0-100; 7.7/100 etc. are 10-point user scores on the wrong scale
            rows = [r for r in rows if not (r[3] == "Metacritic" and r[6] == 100 and r[5] is not None
                                            and float(r[5]) != int(float(r[5])))]
            conn.executemany("insert into wikidata_scores values (?,?,?,?,?,?,?,?,?,?,?)", rows)
            got = {}
            for r in rows:
                got[r[1]] = got.get(r[1], 0) + 1
            conn.executemany("insert or replace into wikidata_scores_done values (?,?,?)",
                             [(x, got.get(x, 0), F.now()) for x in part])
            conn.commit()
        n_rows += len(rows)
        if (s // a.wd_batch) % 10 == 0 or s + a.wd_batch >= len(qids):
            print(f"  {min(s + a.wd_batch, len(qids)):,}/{len(qids):,} items, {n_rows:,} scores ({time.time() - t0:.0f}s)",
                  flush=True)
        time.sleep(a.sleep)
    F.set_meta(conn, "wikidata_scores_at", F.now())
    conn.commit()
    touch_source(conn, "wikidata")
    wd_scores_coverage(conn)


def wd_scores_coverage(conn):
    try:
        rt = one(conn, "select count(distinct tmdb_id) from wikidata_scores where source = 'Rotten Tomatoes' and scale = 100")
        mc = one(conn, "select count(distinct tmdb_id) from wikidata_scores where source = 'Metacritic'")
        print(f"  Wikidata review scores: {one(conn, 'select count(*) from wikidata_scores'):,} rows on "
              f"{one(conn, 'select count(distinct tmdb_id) from wikidata_scores'):,} films; Rotten Tomatoes % {rt:,}, "
              f"Metacritic {mc:,}; sources: " + ", ".join(f"{s} {n:,}" for s, n in conn.execute(
                  "select source, count(distinct tmdb_id) from wikidata_scores group by source order by 2 desc limit 6")))
        new_rt = one(conn, """select count(distinct w.tmdb_id) from wikidata_scores w where w.source = 'Rotten Tomatoes'
            and w.scale = 100 and not exists (select 1 from omdb o where o.tmdb_id = w.tmdb_id and o.rt_pct is not null)
            and not exists (select 1 from critic_scores c where c.tmdb_id = w.tmdb_id and c.rt_pct is not null)""")
        print(f"  films whose Rotten Tomatoes % comes only from Wikidata (not OMDb, not Wikipedia text): {new_rt:,}")
    except sqlite3.Error as e:
        print(f"  (coverage: {e})")


# ------------------------------------------------------------------ coverage
def cmd_coverage(a, conn):
    cat = one(conn, "select count(*) from films where in_catalog = 1")
    allf = one(conn, "select count(*) from films")
    print(f"v7 data coverage (facts.db; {allf:,} films, catalogue {cat:,})")
    print(f"  TV shows: listed {one(conn, 'select count(*) from tv_list'):,}, fetched "
          f"{one(conn, 'select count(*) from tv_shows'):,}; with IMDb id "
          f"{one(conn, 'select count(*) from tv_shows where imdb_id is not null'):,}; with Wikidata id "
          f"{one(conn, 'select count(*) from tv_shows where wikidata_id is not null'):,}")
    print(f"    seasons {one(conn, 'select count(*) from tv_seasons'):,}; credits {one(conn, 'select count(*) from tv_credits'):,};"
          f" keywords {one(conn, 'select count(*) from tv_keywords'):,}; trailers {one(conn, 'select count(*) from tv_videos'):,};"
          f" US age ratings {one(conn, 'select count(*) from tv_ratings where region = ?', 'US'):,};"
          f" US where-to-watch shows {one(conn, 'select count(distinct tmdb_id) from tv_providers where region = ?', 'US'):,};"
          f" reviews {one(conn, 'select count(*) from tv_reviews'):,}")
    print(f"    Wikipedia: {one(conn, 'select count(*) from tv_wiki where status = ?', 'ok'):,} found, "
          f"{one(conn, 'select count(*) from tv_wiki where status = ?', 'none'):,} not found")
    print(f"  IMDb: titles {one(conn, 'select count(*) from imdb_titles'):,}; ratings {one(conn, 'select count(*) from imdb_ratings'):,}; "
          f"alternate titles {one(conn, 'select count(*) from imdb_akas'):,}; crew {one(conn, 'select count(*) from imdb_crew'):,}; "
          f"principals {one(conn, 'select count(*) from imdb_principals'):,}; people {one(conn, 'select count(*) from imdb_names'):,}; "
          f"episodes {one(conn, 'select count(*) from imdb_episodes'):,}")
    nr = one(conn, "select count(*) from films f join imdb_ratings r on r.imdb_id = f.imdb_id")
    print(f"    films with an IMDb rating: {nr:,}; shows with one: "
          f"{one(conn, 'select count(*) from tv_shows s join imdb_ratings r on r.imdb_id = s.imdb_id'):,}")
    try:
        types = conn.execute("select t.title_type, count(*) from films f join imdb_titles t on t.tconst = f.imdb_id "
                             "group by t.title_type order by 2 desc").fetchall()
        print("    our films by IMDb title type: " + ", ".join(f"{k} {v:,}" for k, v in types)
              + "   (non-'movie' types feed the discovery fix, D8)")
    except sqlite3.Error:
        pass
    print(f"  MovieLens distributions: {one(conn, 'select count(*) from ml_dist'):,} films "
          f"({one(conn, 'select count(*) from ml_dist where n >= ?', MIN_N):,} with >= {MIN_N} ratings); "
          f"film-years {one(conn, 'select count(*) from ml_year'):,}")
    try:
        top = conn.execute("""select f.title, d.n, d.mean, d.sd, d.pct_low, d.pct_high, d.bc from ml_dist d
            join films f on f.tmdb_id = d.tmdb_id where d.n >= 1000 order by d.sd desc limit 10""").fetchall()
        if top:
            print("    most divisive (sd, n >= 1,000; check by eye):")
            for t, n, m, sd, lo, hi, bc in top:
                print(f"      {t[:40]:40s} n {n:6,} mean {m:.2f} sd {sd:.2f} low {lo:.0%} high {hi:.0%} bc {bc}")
        moved = conn.execute("""select f.title, d.film_year, d.early_n, d.early_mean, d.late_n, d.late_mean from ml_dist d
            join films f on f.tmdb_id = d.tmdb_id where d.early_n >= 200 and d.late_n >= 200
            order by (d.late_mean - d.early_mean) desc limit 8""").fetchall()
        if moved:
            print("    risen most since release (early = release year to +2, late = +5 on):")
            for t, y, en, em, ln, lm in moved:
                print(f"      {t[:40]:40s} {y}  {em:.2f} (n {en:,}) -> {lm:.2f} (n {ln:,})")
    except sqlite3.Error:
        pass
    print(f"  tag genome: {one(conn, 'select count(distinct tmdb_id) from film_tags'):,} films")
    print(f"  Wikipedia reception wikitext: {one(conn, 'select count(*) from wiki_reception_done'):,} pages checked, "
          f"{one(conn, 'select count(*) from wiki_reception_done where n_sections > 0'):,} with sections; "
          f"{one(conn, 'select count(*) from wiki_reception'):,} sections "
          + ", ".join(f"{k} {v:,}" for k, v in conn.execute("select kind, count(*) from wiki_reception group by kind")))
    wd_scores_coverage(conn)
    srcs = conn.execute("select source, last_run from data_sources where last_run is not null order by source").fetchall()
    if srcs:
        print("  sources last run: " + ", ".join(f"{s} {t[:16]}" for s, t in srcs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sources", "register", "tv", "tv-wiki", "imdb", "movielens", "genome", "reception", "wd-scores",
                                    "coverage", "themes"])
    ap.add_argument("--wd-batch", type=int, default=150, help="wd-scores: Wikidata items per SPARQL query")
    ap.add_argument("--facts", default=None)
    ap.add_argument("--wiki", default=os.environ.get("WIKI_DB") or os.path.join(HERE, "wiki.db"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--offline", action="store_true", help="imdb/movielens/genome: use files already in data/")
    ap.add_argument("--keep", action="store_true", help="imdb: keep the two largest downloads (akas, principals)")
    ap.add_argument("--min-votes", type=int, default=40, help="tv: TMDB votes a show needs")
    ap.add_argument("--max-shows", type=int, default=8000, help="tv: at most this many shows")
    ap.add_argument("--regions", default="US")
    ap.add_argument("--top", type=int, default=25, help="genome: tags kept per film in film_tags")
    ap.add_argument("--batch", type=int, default=20, help="reception: pages per Wikipedia request")
    ap.add_argument("--sleep", type=float, default=1.0, help="reception: seconds between requests")
    a = ap.parse_args()
    if a.cmd == "register":                    # no database needed
        return cmd_register(a)
    conn = connect(a.facts)
    seed_sources(conn)
    t0 = time.time()
    globals()["cmd_" + a.cmd.replace("-", "_")](a, conn)
    if a.cmd not in ("coverage", "sources", "themes"):
        print(f"took {(time.time() - t0) / 60:.1f} min")
        cmd_coverage(a, conn)


if __name__ == "__main__":
    main()
