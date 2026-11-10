"""10 Nov (Home redesign): genre filtering and shelf ranking. Tests A-I of the homepage brief on a controlled
catalogue (temp databases, no server, no network). Each test names the old behaviour it guards against.
    python -m unittest tests.test_v8_home
"""
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
for k, f in (("SOCIAL_DB", "social.db"), ("HISTORY_DB", "history.db"), ("TRAKT_DB", "trakt.db"), ("ALERTS_DB", "alerts.db"),
             ("USERS_DB", "users.db"), ("FACTS_DB", "facts.db"), ("MOVIES_DB", "movies.db"), ("CHATS_DB", "chats.db"),
             ("MEMORY_DB", "memory.db")):
    os.environ[k] = os.path.join(T, f)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "web"), os.path.join(HERE, "..")]
TODAY = dt.date.today()
D = lambda n: (TODAY + dt.timedelta(days=n)).isoformat()   # noqa: E731

c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text,
  us_release_date text, vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text,
  popularity real, overview text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
create table tv_shows(tmdb_id integer primary key, name text, original_name text, first_year integer, vote_count integer, networks_json text, poster_path text);
create table tv_seasons(tmdb_id integer, season_number integer, air_date text, poster_path text);
""")
G = {"A": ["Action"], "T": ["Thriller"], "AT": ["Action", "Thriller"], "C": ["Comedy"], "W": ["Western"], "D": ["Drama"],
     "N": [], "ATD": ["Action", "Thriller", "Drama"], "H": ["History"]}
#   id  title        genres votes  rating pop  released
F = [(101, "Now A",   "A",   900,  6.9, 90, D(-10)), (102, "Now T",   "T",  800, 7.1, 80, D(-12)),
     (103, "Now AT",  "AT",  700,  6.5, 70, D(-15)), (104, "Now C",   "C",  600, 7.4, 60, D(-20)),
     (105, "Now W",   "W",   500,  6.0, 50, D(-25)), (106, "Now None","N",  400, 6.2, 40, D(-30)),
     (107, "Now D",   "D",   300,  7.0, 30, D(-35)),
     (201, "Top AT",  "AT", 30000, 8.6, 20, D(-4000)), (202, "Top A thin", "A", 600, 9.4, 5, D(-3000)),
     (203, "Top T",   "T",  20000, 8.4, 10, D(-5000)), (204, "Top C", "C", 25000, 8.5, 12, D(-6000)),
     (205, "Top AT2", "AT", 18000, 8.4, 11, D(-5500)), (206, "Top W", "W", 9000, 8.1, 3, D(-9000)),
     (207, "Top ATD", "ATD", 15000, 8.0, 9, D(-7000)), (208, "Top H", "H", 8000, 8.2, 4, D(-8000)),
     (301, "Soon A late", "A", 5, None, 30, D(40)), (302, "Soon T early", "T", 3, None, 20, D(5)),
     (303, "Soon C mid", "C", 2, None, 40, D(20)), (304, "Soon AT", "AT", 1, None, 10, D(20)),
     (305, "Soon old", "A", 50, 6.0, 5, D(-30))]
for fid, t, g, v, r, p, d in F:
    c.execute("insert into films values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (fid, t, t, int(d[:4]), d, d, v, r, 100, json.dumps(G[g]), None, p, f"About {t}."))
    c.execute("insert into film_images values (?,?,?)", (fid, f"/p{fid}.jpg", f"/b{fid}.jpg"))
    c.execute("insert into certifications values (?, 'US', 'PG-13', 3, ?)", (fid, d + "T00:00:00.000Z"))
now = TODAY.isoformat() + "T06:00:00"
# TMDB's now-playing order deliberately differs from popularity order
for rank, fid in enumerate([103, 101, 107, 102, 105, 104, 106], 1):
    c.execute("insert into lists values ('now_playing','US',?,?,?)", (fid, rank, now))
for rank, fid in enumerate([301, 303, 302, 304, 305], 1):            # TMDB's upcoming order is not chronological
    c.execute("insert into lists values ('upcoming','US',?,?,?)", (fid, rank, now))
c.commit()
c.close()

import discovery  # noqa: E402
import film_info  # noqa: E402

ALL = list(discovery.GENRE_OPTIONS)


def rail(h, key):
    return [x["id"] for r in h["rails"] if r["key"] == key for x in r["items"]]


def gen(fid):
    return set(next(G[g] for i, _t, g, *_ in F if i == fid))


class Semantics(unittest.TestCase):
    def test_a_no_genre_original_order(self):
        h = discovery.home("")
        self.assertEqual(rail(h, "now_playing"), [103, 101, 107, 102, 105, 104, 106])      # TMDB's order, untouched
        self.assertEqual(rail(h, "coming_soon"), [302, 303, 304, 301])                     # by date, then id; old film out
        self.assertEqual(h["genres"], [])

    def test_b_one_genre(self):
        h = discovery.home("", ["Thriller"])
        for key in ("now_playing", "top_rated", "trending", "coming_soon"):
            self.assertTrue(all("Thriller" in gen(i) for i in rail(h, key)), key)
        self.assertEqual(rail(h, "now_playing"), [103, 102])

    def test_c_match_any_keeps_section_order(self):
        """Old: _keep_genres moved films matching both genres to the front (Now AT before everything)."""
        h = discovery.home("", ["Action", "Thriller"])
        self.assertEqual(rail(h, "now_playing"), [103, 101, 102])                         # TMDB order, not match count
        self.assertEqual(h["mode"], "any")

    def test_d_match_all(self):
        h = discovery.home("", ["Action", "Thriller"], "all")
        self.assertEqual(rail(h, "now_playing"), [103])
        self.assertTrue(all({"Action", "Thriller"} <= gen(i) for k in ("top_rated", "coming_soon") for i in rail(h, k)))
        self.assertEqual(rail(h, "coming_soon"), [304])

    def test_e_every_genre_is_no_filter(self):
        """Old: the route kept only the first 6 genres and the page offered 13 of 18, so 'all' dropped Thriller, Western
        and genre-less films and reordered the rest."""
        a, b = discovery.home(""), discovery.home("", ALL)
        for r1, r2 in zip(a["rails"], b["rails"]):
            self.assertEqual((r1["key"], [x["id"] for x in r1["items"]]), (r2["key"], [x["id"] for x in r2["items"]]))
        self.assertTrue(b["all_selected"])
        self.assertEqual(b["genres"], [])
        self.assertEqual(rail(discovery.home("", ALL, "all"), "now_playing"), rail(a, "now_playing"))

    def test_more_than_six_genres_are_all_used(self):
        seven = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Thriller"]
        self.assertIn(102, rail(discovery.home("", seven), "now_playing"))               # Thriller is the 7th

    def test_f_stable(self):
        runs = [[(r["key"], [x["id"] for x in r["items"]]) for r in discovery.home("", ["Action", "Thriller"])["rails"]] for _ in range(5)]
        self.assertTrue(all(r == runs[0] for r in runs))

    def test_g_no_match_is_empty_not_filled(self):
        h = discovery.home("", ["Western", "Comedy"], "all")
        for r in h["rails"]:
            self.assertEqual(r["items"], [], r["key"])
            self.assertEqual(r["total"], 0)

    def test_unknown_and_duplicate_genres_ignored(self):
        self.assertEqual(discovery.genre_filter(["Thriller", "Thriller", "Nonsense"])[0], ["Thriller"])
        self.assertEqual(discovery.genre_filter(["Thriller"], "all")[1], "any")          # 'all' needs two or more


class SectionIntegrity(unittest.TestCase):
    def test_i_top_rated_is_quality_ranked_and_weighted(self):
        top = rail(discovery.home(""), "top_rated")
        self.assertLess(top.index(201), top.index(202))         # 9.4 from 600 votes does not beat 8.6 from 30,000
        filt = rail(discovery.home("", ["Action", "Thriller"]), "top_rated")
        self.assertEqual(filt, [i for i in top if gen(i) & {"Action", "Thriller"}])   # same order, only fewer films
        self.assertEqual(len(top), len(set(top)))

    def test_i_top_rated_tie_break_by_id(self):
        top = rail(discovery.home("", ["Action", "Thriller"]), "top_rated")
        self.assertLess(top.index(203), top.index(205))          # equal-ish scores never swap between requests

    def test_i_trending_is_popularity_and_not_a_copy_of_theaters(self):
        h = discovery.home("")
        tr, th = rail(h, "trending"), set(rail(h, "now_playing"))
        self.assertFalse(set(tr) & th)
        pops = {i: p for i, _t, _g, _v, _r, p, _d in F}
        self.assertEqual(tr, sorted(tr, key=lambda i: (-pops[i], i)))

    def test_i_opening_soon_chronological_with_labels(self):
        h = discovery.home("", ["Action", "Thriller"])
        r = next(r for r in h["rails"] if r["key"] == "coming_soon")
        self.assertEqual([x["id"] for x in r["items"]], [302, 304, 301])
        self.assertTrue(all(x["release"]["kind"] == "theatrical" and x["release"]["date"] >= TODAY.isoformat() for x in r["items"]))

    def test_filter_runs_before_the_cut(self):
        """Old: Popular took its top 80, then filtered, so a genre could come back empty while films existed."""
        h = discovery.home("", ["Western"], per=1)
        self.assertEqual(rail(h, "now_playing"), [105])
        self.assertEqual(next(r for r in h["rails"] if r["key"] == "top_rated")["total"], 2)          # Now W (500 votes) and Top W

    def test_your_list_is_the_saved_films(self):
        with film_info._lock:
            k = film_info._users()
            for i, fid in enumerate((203, 104)):
                k.execute("insert or replace into watch values (?,?,?,?,?,?)", ("me", fid, "t", "want", None, f"2026-11-0{i + 1}T10:00:00"))
            k.commit()
        h = discovery.home("me", ["Thriller"])
        r = next(r for r in h["rails"] if r["key"] == "watchlist")
        self.assertEqual(([x["id"] for x in r["items"]], r["total"], r["saved"]), ([203], 1, 2))   # filtered, and says so
        self.assertEqual(rail(discovery.home("me"), "watchlist"), [104, 203])                      # newest saved first

    def test_hero_unfiltered_with_artwork(self):
        h = discovery.home("", ["Western"])
        self.assertEqual([x["id"] for x in h["hero"]][:3], [103, 101, 107])
        self.assertTrue(h["hero"][0]["backdrop"] and h["hero"][0]["overview"])

    def test_one_genre_list(self):
        import user_prefs
        self.assertEqual(discovery.GENRE_OPTIONS, user_prefs.GENRES)      # page chips, server filter and settings agree


if __name__ == "__main__":
    unittest.main()
