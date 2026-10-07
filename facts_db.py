"""
facts.db: Movie Buff v3's structured movie knowledge (new in v3; movies.db and wiki.db are
not touched, so every v2 number stays reproducible).

What lives where (v3 storage plan):
    movies.db   TMDB film list + vectors (v2, read-only). The search catalogue.
    wiki.db     Wikipedia lead + plot passages + vectors (v2, read-only). Tip-of-the-tongue search.
    facts.db    everything structured, plus the non-plot Wikipedia sections and review analysis:
                  films / people / credits / keywords      TMDB API          (SQL, not vectors)
                  facts                                    budget, revenue, box office from
                                                           TMDB + Wikipedia infobox + Wikidata,
                                                           one row per source (conflicts kept)
                  awards                                   Wikidata wins / nominations
                  reviews                                  TMDB user reviews (audience)
                  sections (+ sec_vec)                     Wikipedia Production / Reception /
                                                           Box office / Themes ... passages
                  review_aspects, film_aspects             review intelligence (aspects x sentiment)
                  summaries                                section / film summaries, cached by
                                                           content hash + prompt version
                  lists                                    now playing / upcoming, with fetch time
                  ml_stats                                 MovieLens audience rating per film
                  meta                                     build + refresh times

Every fact row carries source, retrieved_at / as_of and a reliability score, so the
answer can say where a number came from and how fresh it is, and conflicting
sources are shown instead of silently picking one.

    python facts_db.py status        # row counts, freshness, coverage
"""
import datetime
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("FACTS_DB", os.path.join(HERE, "facts.db"))

SCHEMA = """
create table if not exists films(
  tmdb_id integer primary key, imdb_id text, wikidata_id text, wiki_doc_id text,
  title text, original_title text, release_date text, us_release_date text, year integer,
  runtime integer, budget integer, revenue integer, status text, original_language text,
  overview text, tagline text, genres_json text, countries_json text, companies_json text,
  collection_id integer, collection_name text, popularity real, vote_average real,
  vote_count integer, in_catalog integer default 0, tier text, tmdb_fetched_at text);
create index if not exists films_year on films(year);
create index if not exists films_coll on films(collection_id);
create index if not exists films_tier on films(tier);
create index if not exists films_title on films(title);

create table if not exists people(
  person_id integer primary key, name text, popularity real, known_for text);
create index if not exists people_name on people(name);

create table if not exists credits(
  tmdb_id integer, person_id integer, kind text, job text, department text,
  character text, ord integer);
create index if not exists credits_film on credits(tmdb_id);
create index if not exists credits_person on credits(person_id, kind, job);

create table if not exists keywords(tmdb_id integer, keyword_id integer, name text);
create index if not exists keywords_film on keywords(tmdb_id);
create index if not exists keywords_name on keywords(name);

create table if not exists facts(
  tmdb_id integer, field text, value_num real, value_text text, currency text,
  source text, source_ref text, as_of text, retrieved_at text, reliability real);
create index if not exists facts_film on facts(tmdb_id, field);

create table if not exists awards(
  tmdb_id integer, wikidata_id text, award_qid text, award text, status text,
  year integer, for_person text, retrieved_at text);
create index if not exists awards_film on awards(tmdb_id);

create table if not exists reviews(
  review_id text primary key, tmdb_id integer, source text, author text, rating real,
  created_at text, url text, content text, fetched_at text);
create index if not exists reviews_film on reviews(tmdb_id);

create table if not exists sections(
  sec_id integer primary key, tmdb_id integer, doc_id text, section_key text, kind text,
  position integer, n_parts integer, text text, n_words integer, url text, snapshot text);
create index if not exists sections_film on sections(tmdb_id, kind);

create table if not exists sec_vec(sec_id integer primary key, emb blob);

create table if not exists summaries(
  key text primary key, tmdb_id integer, kind text, level text, model text, version text,
  text text, sources_json text, created_at text);
create index if not exists summaries_film on summaries(tmdb_id, kind);
create table if not exists sum_vec(key text primary key, emb blob);

create table if not exists review_aspects(
  review_id text, tmdb_id integer, source_type text, aspect text, sentiment integer,
  quote text, model text, version text);
create index if not exists ra_film on review_aspects(tmdb_id, source_type, aspect);
create table if not exists review_done(review_id text primary key, version text, n integer);

create table if not exists film_aspects(
  tmdb_id integer, source_type text, aspect text, pos integer, neg integer, mixed integer,
  n_reviews integer, quotes_json text, updated_at text,
  primary key (tmdb_id, source_type, aspect));

create table if not exists lists(
  list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create index if not exists lists_name on lists(list_name, fetched_at);

create table if not exists ml_stats(tmdb_id integer primary key, n_ratings integer, mean real, source text);

create table if not exists meta(key text primary key, value text);
"""

# How quickly each kind of data goes stale (refresh.py). Days; None = never refresh.
FRESHNESS = {
    "static":      None,   # title, director, cast, original release year
    "slow":        30,     # ratings, review counts, total box office of films > 1 year old
    "recent":      7,      # films released in the last year: revenue + ratings still moving
    "fast":        1,      # upcoming release dates, now playing, opening weekends
}

# How much to trust each source for money figures (used when sources disagree).
RELIABILITY = {"tmdb": 0.7, "wikipedia_infobox": 0.8, "wikidata": 0.75}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def connect(path=None, readonly=False):
    p = path or PATH
    if readonly:
        if not os.path.exists(p):
            return None
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
    else:
        # Several builders write facts.db at once (TMDB, Wikidata, sections); each write is a short
        # transaction, so waiting up to 10 min for the lock is always enough.
        conn = sqlite3.connect(p, check_same_thread=False, timeout=600)
        conn.execute("pragma journal_mode=WAL")
        conn.executescript(SCHEMA)
    return conn


def set_meta(conn, key, value):
    conn.execute("insert or replace into meta values (?, ?)", (key, str(value)))


def get_meta(conn, key, default=None):
    try:
        row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    return row[0] if row else default


def status(path=None):
    conn = connect(path, readonly=True)
    if conn is None:
        print(f"{path or PATH}: not built yet")
        return 1
    print(f"{path or PATH}")
    for t in ("films", "people", "credits", "keywords", "facts", "awards", "reviews", "sections",
              "sec_vec", "summaries", "sum_vec", "review_aspects", "film_aspects", "lists", "ml_stats"):
        try:
            n = conn.execute(f"select count(*) from {t}").fetchone()[0]
        except sqlite3.Error:
            n = "-"
        print(f"  {t:15s} {n}")
    try:
        for tier, n in conn.execute("select tier, count(*) from films group by tier"):
            print(f"  films tier={tier}: {n}")
        n_money = conn.execute("select count(*) from films where budget > 0 and revenue > 0").fetchone()[0]
        print(f"  films with TMDB budget AND revenue: {n_money}")
        for src, n in conn.execute("select source, count(distinct tmdb_id) from facts "
                                   "where field in ('budget','box_office','revenue') group by source"):
            print(f"  money facts from {src}: {n} films")
        for k, v in conn.execute("select key, value from meta order by key"):
            print(f"  meta {k} = {v}")
    except sqlite3.Error as e:
        print(f"  ({e})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "status":
        sys.exit(status(sys.argv[2] if len(sys.argv) > 2 else None))
    print(__doc__)
