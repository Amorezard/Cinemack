"""10 Nov (round 18): short films off the movie rows, Home's TV rows (New Seasons, Top Rated Shows), the TMDB sidecar
(logs/tmdb_extra.db) and its fallbacks. Controlled catalogue in temp databases; no server, no network.
    python -m unittest tests.test_v8_r18
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
             ("MEMORY_DB", "memory.db"), ("TMDB_EXTRA_DB", "tmdb_extra.db")):
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
#   id   title          runtime votes rating pop released
F = [(101, "Feature",     110,   900, 7.0, 90, D(-10)), (102, "ee",          7,   900, 7.9, 99, D(-5)),
     (103, "No runtime",  None,  800, 6.8, 80, D(-12)), (104, "Forty",      40,   700, 7.2, 70, D(-15)),
     (105, "Forty-one",   41,    600, 6.9, 60, D(-20)),
     (201, "Top feature", 120, 20000, 8.4, 5, D(-4000)), (202, "Top short",  8, 20000, 8.9, 5, D(-3000)),
     (301, "Soon short",  12,      5, None, 9, D(9)), (302, "Soon art",   None,   3, None, 8, D(10))]
for fid, t, rt, v, r, p, d in F:
    c.execute("insert into films values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (fid, t, t, int(d[:4]), d, d, v, r, rt, json.dumps(["Drama"]), None, p, ""))
    if fid != 302:
        c.execute("insert into film_images values (?,?,?)", (fid, f"/p{fid}.jpg", f"/b{fid}.jpg"))
    c.execute("insert into certifications values (?, 'US', 'PG-13', 3, ?)", (fid, d + "T00:00:00.000Z"))
now = TODAY.isoformat() + "T06:00:00"
for rank, fid in enumerate([101, 102, 103, 104, 105], 1):
    c.execute("insert into lists values ('now_playing','US',?,?,?)", (fid, rank, now))
for rank, fid in enumerate([301, 302], 1):
    c.execute("insert into lists values ('upcoming','US',?,?,?)", (fid, rank, now))
# facts.db shows: 900 has a season in the window (no genre data), 901 an old season only
c.execute("insert into tv_shows values (900, 'Facts Show', 'Facts Show', 2019, 500, '[\"HBO\"]', '/t900.jpg')")
c.execute("insert into tv_shows values (901, 'Old Show', 'Old Show', 2010, 500, '[]', '/t901.jpg')")
c.executemany("insert into tv_seasons values (?,?,?,null)", [(900, 1, D(-700)), (900, 2, D(-4)), (901, 1, D(-400)), (901, 2, D(20))])
c.commit()
c.close()

x = sqlite3.connect(os.environ["TMDB_EXTRA_DB"])
x.executescript("""
create table meta(key text primary key, value text);
create table tv_show(tmdb_id integer primary key, name text, first_year integer, vote_average real, vote_count integer,
  popularity real, genres_json text, tv_genres_json text, poster_path text, backdrop_path text, networks_json text,
  original_language text, top_rank integer);
create table tv_season(tmdb_id integer, season_number integer, air_date text, episode_count integer, poster_path text);
create table film_art(tmdb_id integer primary key, poster_path text, backdrop_path text, runtime integer);
""")
S = [  # id, name, rating, votes, popularity, genres (mapped), tv genres, top rank
    (1, "Crime Big",   8.6, 20000, 50, ["Crime", "Drama"], ["Crime", "Drama"], 1),
    (2, "Thin Nine",   9.6,   350, 10, ["Drama"], ["Drama"], 2),
    (3, "Comedy Mid",  8.4,  9000, 80, ["Comedy"], ["Comedy"], 3),
    (4, "Talk Night",  8.9, 15000, 90, ["Comedy"], ["Talk", "Comedy"], 4),
    (5, "Few Votes",   9.0,   120,  5, ["Drama"], ["Drama"], 5),
    (6, "Crime Comedy", 8.0, 5000, 20, ["Crime", "Comedy"], ["Crime", "Comedy"], None),
    (7, "Reality Isle", 7.0, 900, 95, [], ["Reality"], None),
]
for i, n, va, vc, pop, g, tg, rank in S:
    x.execute("insert into tv_show values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, n, 2020, va, vc, pop, json.dumps(g), json.dumps(tg), f"/s{i}.jpg", None, '["Netflix"]', "en", rank))
x.executemany("insert into tv_season values (?,?,?,8,null)", [
    (3, 1, D(-800)), (3, 2, D(-2)),            # new season 2 days ago
    (6, 1, D(-10)),                            # a new series
    (1, 5, D(-45)),                            # outside the 30 days
    (4, 12, D(-1)),                            # talk show: never on the TV rows
    (7, 3, D(-3)),                             # reality: never
    (6, 2, D(5))])                             # future season of a sidecar-only show (calendar)
x.execute("insert into film_art values (302, '/art302.jpg', '/artb302.jpg', 95)")
x.execute("insert into meta values ('fetched_at', ?)", (now,))
x.commit()
x.close()

import discovery  # noqa: E402
import tmdb_extra  # noqa: E402
import v8extra  # noqa: E402

ALL = list(discovery.GENRE_OPTIONS)


def rail(h, key):
    return [r for r in h["rails"] if r["key"] == key][0]


def ids(h, key):
    return [x["id"] for x in rail(h, key)["items"]]


class Shorts(unittest.TestCase):
    def test_runtime_rule(self):
        # 40 minutes or less is a short; unknown runtime is kept; order is kept
        self.assertEqual(discovery.no_shorts([105, 104, 103, 102, 101]), [105, 103, 101])

    def test_home_rows_and_hero_have_no_shorts(self):
        h = discovery.home("")
        every = {x["id"] for r in h["rails"] for x in r["items"] if r.get("media") != "tv"} | {x["id"] for x in h["hero"]}
        for short in (102, 104, 202, 301):
            self.assertNotIn(short, every)
        self.assertEqual(ids(h, "now_playing"), [101, 103, 105])          # TMDB order, short taken out
        self.assertIn(201, ids(h, "top_rated"))                            # old bug: a short could top the list

    def test_calendar_has_no_shorts(self):
        cal = discovery.calendar("", days=30, start=D(-30))
        films = {e["film"]["id"] for e in cal["events"] if e.get("film")}
        self.assertNotIn(102, films)
        self.assertIn(101, films)

    def test_artwork_and_runtime_fallback(self):
        cs = {c["id"]: c for c in discovery.cards([302, 101])}
        self.assertTrue(cs[302]["poster"].endswith("/art302.jpg"))
        self.assertEqual(cs[302]["runtime"], 95)
        self.assertTrue(cs[101]["poster"].endswith("/p101.jpg"))           # facts.db artwork still wins


class TvRows(unittest.TestCase):
    def test_new_seasons_window_order_and_exclusions(self):
        h = discovery.home("")
        r = rail(h, "new_seasons")
        self.assertEqual(ids(h, "new_seasons"), [3, 6, 900])                # popularity 80, 20, then unknown
        self.assertNotIn(1, ids(h, "new_seasons"))                          # 45 days ago: outside 30 days
        self.assertNotIn(4, ids(h, "new_seasons"))                          # talk
        self.assertNotIn(7, ids(h, "new_seasons"))                          # reality
        cards = {c["id"]: c for c in r["items"]}
        self.assertEqual(cards[3]["season"]["number"], 2)
        self.assertEqual(cards[900]["title"], "Facts Show")                 # facts.db show, no sidecar data
        self.assertEqual(r["total"], 3)
        self.assertTrue(r["as_of"])

    def test_top_rated_shows_weighted(self):
        top = ids(discovery.home(""), "top_shows")
        self.assertEqual(top[0], 1)                                         # 8.6 / 20k beats 9.6 / 350
        self.assertLess(top.index(3), top.index(2))
        self.assertNotIn(5, top)                                            # under 300 votes
        self.assertNotIn(4, top)                                            # talk
        self.assertNotIn(6, top)                                            # not on TMDB's top-rated list

    def test_genre_filter_on_tv_rows(self):
        h = discovery.home("", ["Crime"])
        self.assertEqual(ids(h, "top_shows"), [1])
        self.assertEqual(ids(h, "new_seasons"), [6])                        # facts show has no genres: out under a filter
        h = discovery.home("", ["Crime", "Comedy"], "all")
        self.assertEqual(ids(h, "new_seasons"), [6])
        self.assertEqual(ids(h, "top_shows"), [])

    def test_every_genre_equals_none(self):
        a, b = discovery.home(""), discovery.home("", ALL, "all")
        for k in ("new_seasons", "top_shows"):
            self.assertEqual(ids(a, k), ids(b, k))

    def test_follow_a_sidecar_only_show(self):
        discovery.tv_follow("u1", 6, True)
        fol = {s["id"]: s for s in discovery.tv_following("u1")}
        self.assertIn(6, fol)                                               # old code dropped shows not in facts.db
        self.assertEqual(fol[6]["next_season"]["number"], 2)
        self.assertTrue([c for c in rail(discovery.home("u1"), "new_seasons")["items"] if c["id"] == 6][0]["following"])
        self.assertEqual(v8extra._shows([6])[6]["title"], "Crime Comedy")
        discovery.tv_follow("u1", 6, False)

    def test_calendar_adds_sidecar_seasons_once(self):
        cal = discovery.calendar("", days=40, start=D(-10))
        tv = [(e["show"]["id"], e["show"]["season"]) for e in cal["events"] if e["kind"] == "tv_season"]
        self.assertIn((6, 2), tv)
        self.assertIn((3, 2), tv)
        self.assertNotIn(4, [s for s, _n in tv])                            # talk
        self.assertEqual(len(tv), len(set(tv)))

    def test_no_sidecar_no_rows(self):
        old = tmdb_extra.DB
        tmdb_extra.DB = os.path.join(T, "missing.db")
        try:
            h = discovery.home("")
            self.assertEqual(ids(h, "top_shows"), [])
            self.assertIsNone(rail(h, "top_shows")["as_of"])                # the page hides the row
            self.assertEqual(ids(h, "new_seasons"), [900])                  # facts.db seasons still count
        finally:
            tmdb_extra.DB = old


if __name__ == "__main__":
    unittest.main()
