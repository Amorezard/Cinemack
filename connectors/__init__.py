"""
R1 (v7, 27 Oct): the connector framework - every outside source as one entry with the same pattern:
fetch (get new raw rows), match (tie them to a TMDB film / show), refresh (keep them inside their terms), a
coverage query, the licence it is used under, and a unit test on a saved sample (tests/test_connectors.py).

The fetchers themselves stay where they are (fetch_tmdb.py, fetch_free_data.py, v7_data.py, youtube_analysis.py,
critics.py ...): this package is the one list of them, so nightly.sh, the About page's licensing register (E6) and
the coverage report all read the same thing.

    python -m connectors list          # every connector: source, fetch / refresh commands, tables, licence
    python -m connectors coverage      # rows and films per connector (facts.db)
    python -m connectors test          # the saved-sample unit tests (tests/test_connectors.py)
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class Connector:
    def __init__(self, name, source, fetch, refresh, match, tables, coverage, test, status="live"):
        self.name, self.source, self.fetch, self.refresh, self.match = name, source, fetch, refresh, match
        self.tables, self.coverage, self.test, self.status = tables, coverage, test, status


C = Connector
CONNECTORS = [
    C("tmdb", "tmdb", "fetch_tmdb.py catalog | current | extra", "fetch_tmdb.py refresh (FRESHNESS rules)",
      "TMDB id is the key", "films people credits keywords reviews lists",
      [("films", "select count(*) from films"), ("films with reviews", "select count(distinct tmdb_id) from reviews")],
      "test_tmdb_us_theatrical"),
    C("tmdb_extra", "tmdb", "fetch_free_data.py tmdb", "fetch_free_data.py tmdb --again (monthly)",
      "TMDB id", "film_videos watch_providers certifications",
      [("films with trailers", "select count(distinct tmdb_id) from film_videos"),
       ("films with US providers", "select count(distinct tmdb_id) from watch_providers where region = 'US'")], None),
    C("tmdb_tv", "tmdb", "v7_data.py tv", "v7_data.py tv --again", "TMDB TV id (own id space, never a film id)",
      "tv_shows tv_seasons tv_credits tv_keywords tv_videos tv_ratings tv_providers tv_reviews",
      [("shows", "select count(*) from tv_shows"), ("shows with reviews", "select count(distinct tmdb_id) from tv_reviews")],
      "test_tv_rows"),
    C("wikipedia_reception", "wikipedia", "v7_data.py reception", "v7_data.py reception --again (changed revisions)",
      "Wikipedia page id from wiki.db pages (TREC doc ids are page ids)", "wiki_reception critic_statements",
      [("pages with sections", "select count(*) from wiki_reception_done where n_sections > 0"),
       ("critic statements", "select count(*) from critic_statements where kind = 'critic'")],
      "test_reception_sections_and_critics"),
    C("wikipedia_tv", "wikipedia", "v7_data.py tv-wiki", "v7_data.py tv-wiki --again", "Wikidata link, else title search",
      "tv_wiki tv_passages", [("shows with an article", "select count(*) from tv_wiki where status = 'ok'")], None),
    C("wikidata_scores", "wikidata", "v7_data.py wd-scores", "v7_data.py wd-scores --again (monthly)",
      "films.wikidata_id", "wikidata_scores",
      [("films with a review score", "select count(distinct tmdb_id) from wikidata_scores")], "test_parse_score"),
    C("imdb", "imdb", "v7_data.py imdb", "v7_data.py imdb (weekly; files kept 7 days)", "films.imdb_id / tv_shows.imdb_id",
      "imdb_titles imdb_ratings imdb_akas imdb_crew imdb_principals imdb_names imdb_episodes",
      [("titles", "select count(*) from imdb_titles"), ("ratings", "select count(*) from imdb_ratings")],
      "test_imdb_tsv"),
    C("movielens", "movielens", "v7_data.py movielens; v7_data.py genome", "- (static dataset)", "links.csv tmdbId",
      "ml_dist ml_year film_tags", [("films with a distribution", "select count(*) from ml_dist"),
                                    ("films with genome tags", "select count(distinct tmdb_id) from film_tags")],
      "test_movielens_dist"),
    C("omdb", "omdb", "fetch_free_data.py omdb (950/day)", "nightly.sh (missing first, then oldest)", "imdb id",
      "omdb", [("films with RT or MC", "select count(*) from omdb where rt_pct is not null or mc_score is not null")],
      "test_omdb_num"),
    C("guardian", "guardian", "fetch_free_data.py guardian", "nightly.sh (from the last date)",
      "headline title + year window", "critic_reviews(source=guardian)",
      [("matched reviews", "select count(*) from critic_reviews where source = 'guardian' and match >= 1")],
      "test_guardian_title"),
    C("nyt", "nyt", "fetch_free_data.py nyt", "nightly.sh (this month, and months not done)",
      "creative-work keyword, else headline title", "critic_reviews(source=nyt) nyt_meta",
      [("matched reviews", "select count(*) from critic_reviews where source = 'nyt' and match >= 1")], "test_nyt_titles"),
    C("youtube_trailers", "youtube", "fetch_free_data.py youtube", "nightly.sh weekly --again; purge after 30 days",
      "TMDB video keys", "yt_videos yt_comments", [("trailers with stats", "select count(*) from yt_videos")], None),
    C("youtube_analysis", "youtube", "youtube_analysis.py channels | fetch | match",
      "youtube_analysis.py refresh (25 days); purge after 30 days", "title phrase / quoted title / year",
      "yt_channels yt_analysis yt_analysis_film yt_analysis_units",
      [("videos", "select count(*) from yt_analysis"),
       ("films with a confident video", "select count(distinct tmdb_id) from yt_analysis_units where match_ok = 1")],
      "test_youtube_match_and_chapters"),
    C("netflix", "netflix", "fetch_free_data.py netflix", "- (twice a year)", "title + release date",
      "netflix_views", [("rows matched", "select count(*) from netflix_views where tmdb_id is not null")], None),
    C("letterboxd", "letterboxd", "connectors/letterboxd.py (only if API access is approved)", "-", "-", "-", [],
      None, status="waiting for approval"),
]


def facts():
    import facts_db as F
    return F.connect(readonly=True)


def cmd_list():
    for c in CONNECTORS:
        print(f"{c.name:20s} [{c.status}] source={c.source}\n    fetch: {c.fetch}\n    refresh: {c.refresh}\n"
              f"    match: {c.match}\n    tables: {c.tables}\n    test: {c.test or '-'}")


def cmd_coverage():
    conn = facts()
    if conn is None:
        sys.exit("facts.db not found")
    lic = {}
    try:
        lic = {s: (l, cr) for s, l, cr in conn.execute("select source, licence, credit from data_sources")}
    except sqlite3.Error:
        pass
    print(f"{'connector':20s} {'status':22s} coverage")
    for c in CONNECTORS:
        bits = []
        for label, sql in c.coverage:
            try:
                n = conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                n = "-"
            bits.append(f"{label} {n:,}" if isinstance(n, int) else f"{label} -")
        print(f"{c.name:20s} {c.status:22s} " + "; ".join(bits))
    missing = sorted({c.source for c in CONNECTORS} - set(lic))
    if missing:
        print(f"(sources missing from the licensing register: {missing} - run `python v7_data.py sources`)")
