"""4 Nov website changes (Cinemack), no real database or model:  python -m unittest tests.test_site_nov4"""
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
FACTS = os.path.join(TMP, "facts.db")
os.environ["FACTS_DB"] = FACTS
os.environ["USERS_DB"] = os.path.join(TMP, "users.db")
sys.path.insert(0, os.path.join(HERE, "web"))
sys.path.insert(0, HERE)

FILMS = [  # tmdb_id, title, year, genres, votes, keywords
    (1, "Avengers: Infinity War", 2018, ["Action", "Adventure", "Science Fiction"], 30000, ["superhero", "alien", "space", "based on comic"]),
    (2, "Inception", 2010, ["Action", "Science Fiction", "Adventure"], 36000, ["dream", "subconscious"]),
    (3, "Interstellar", 2014, ["Adventure", "Drama", "Science Fiction"], 35000, ["space", "time travel"]),
    (4, "The Matrix", 1999, ["Action", "Science Fiction"], 25000, ["simulated reality", "artificial intelligence"]),
    (5, "Avengers: Endgame", 2019, ["Action", "Adventure", "Science Fiction"], 26000, ["superhero", "time travel"]),
    (6, "Alien", 1979, ["Horror", "Science Fiction"], 14000, ["alien", "space"]),
    (7, "Arrival", 2016, ["Drama", "Science Fiction", "Mystery"], 18000, ["alien", "nonlinear timeline"]),
    (8, "Guardians of the Galaxy", 2014, ["Action", "Science Fiction", "Adventure"], 28000, ["superhero", "space", "based on comic"]),
    (9, "Heat", 1995, ["Crime", "Drama", "Action"], 7000, ["heist", "police"]),
]


def make_facts():
    if os.path.exists(FACTS):
        return
    c = sqlite3.connect(FACTS)
    c.executescript("""create table films(tmdb_id integer, title text, original_title text, year integer, genres_json text,
        vote_count integer, in_catalog integer, runtime integer, imdb_id text);
        create table film_images(tmdb_id integer, poster_path text);
        create table keywords(tmdb_id integer, name text);
        create table watch_providers(tmdb_id integer, region text, kind text, provider_name text, priority integer, fetched_at text);""")
    for fid, t, y, g, v, kws in FILMS:
        c.execute("insert into films values (?,?,?,?,?,?,1,120,null)", (fid, t, t, y, json.dumps(g), v))
        c.execute("insert into film_images values (?,?)", (fid, f"/p{fid}.jpg"))
        c.executemany("insert into keywords values (?,?)", [(fid, k) for k in kws])
    c.executemany("insert into watch_providers values (?,?,?,?,?,?)", [
        (2, "US", "flatrate", "Netflix Standard with Ads", 1, "2026-10"), (3, "US", "flatrate", "Paramount Plus", 1, "2026-10"),
        (4, "US", "flatrate", "Max Amazon Channel", 2, "2026-10"), (6, "US", "rent", "Netflix", 1, "2026-10"),
        (9, "US", "free", "Tubi TV", 1, "2026-10"), (7, "GB", "flatrate", "Netflix", 1, "2026-10")])
    c.commit()
    c.close()


make_facts()
import user_prefs as U  # noqa: E402
import imports as I  # noqa: E402


class TasteGroups(unittest.TestCase):
    def test_mind_bending_has_no_superhero_films(self):
        g = {x["key"]: x for x in U.groups()}
        titles = [f["title"] for f in g["scifi"]["films"]]
        self.assertNotIn("Avengers: Infinity War", titles)
        self.assertNotIn("Avengers: Endgame", titles)
        self.assertIn("Inception", titles)
        heroes = [f["title"] for f in g["heroes"]["films"]]
        self.assertIn("Avengers: Infinity War", heroes)

    def test_no_film_shown_twice(self):
        seen = [f["id"] for x in U.groups() for f in x["films"]]
        self.assertEqual(len(seen), len(set(seen)))


class Services(unittest.TestCase):
    def test_provider_names(self):
        self.assertEqual(U.service_of("Netflix Standard with Ads"), "netflix")
        self.assertEqual(U.service_of("Max Amazon Channel"), "max")
        self.assertEqual(U.service_of("Disney Plus"), "disney")
        self.assertEqual(U.service_of("Apple TV+"), "apple")
        self.assertEqual(U.service_of("Tubi TV"), "tubi")
        self.assertIsNone(U.service_of("Maxdome"))
        self.assertIsNone(U.service_of("Fandango At Home"))

    def test_on_services_counts_streaming_only_in_the_us(self):
        s = U.clean({"services": ["netflix", "max", "bogus"]})
        self.assertEqual(s["services"], ["netflix", "max"])
        on = U.on_services(s, [2, 4, 6, 7, 9])
        self.assertEqual(on, {2: ["Netflix"], 4: ["Max"]})       # 6 is rent only, 7 is GB, 9 is Tubi (not theirs)

    def test_prefer_services_is_stable(self):
        s = U.clean({"services": ["netflix"]})
        hits = [{"id": 9, "title": "Heat"}, {"id": 2, "title": "Inception"}, {"id": 3, "title": "Interstellar"}]
        self.assertEqual([h["id"] for h in U.prefer_services(s, hits)], [2, 9, 3])
        self.assertEqual(U.prefer_services(U.clean({}), hits), hits)

    def test_prompt_line(self):
        s = U.clean({"services": ["netflix"]})
        hits = [{"id": 9, "title": "Heat"}, {"id": 2, "title": "Inception"}]
        line = U.services_block(s, hits, "recommend")
        self.assertIn("Inception (Netflix)", line)
        self.assertEqual(U.services_block(s, hits, "named_film", "Who directed Heat?"), "")
        self.assertIn("Inception", U.services_block(s, hits, None, "Where can I stream Inception?"))
        self.assertIn("None of the films above", U.services_block(U.clean({"services": ["hulu"]}), hits, "recommend"))

    def test_search_cache_key_includes_services(self):
        self.assertNotEqual(U.filter_tag(U.clean({"services": ["netflix"]})), U.filter_tag(U.clean({})))


class AvoidGroups(unittest.TestCase):
    def test_avoid_becomes_negative_tastes(self):
        got = []
        sys.modules["personal"] = types.SimpleNamespace(record_stated=lambda key, prefs: got.extend(prefs))
        prof = U.set_groups("u1", ["scifi", "drama"], ["horror", "drama", "nope"])
        self.assertEqual(prof["groups"], ["scifi", "drama", "-horror"])
        pos = {p["text"] for p in got if p["polarity"] > 0}
        neg = {p["text"] for p in got if p["polarity"] < 0}
        self.assertIn("time travel", pos)
        self.assertIn("ghost", neg)
        self.assertFalse(pos & neg)


class Letterboxd(unittest.TestCase):
    RATINGS = "Date,Name,Year,Letterboxd URI,Rating\n2024-01-01,Inception,2010,x,5\n2024-01-02,Avengers: Endgame,2019,x,1.5\n" \
              "2024-01-03,Heat,1995,x,3\n2024-01-04,Some Film Not Here,2001,x,4\n"
    WATCHLIST = "Date,Name,Year,Letterboxd URI\n2024-02-01,Arrival,2016,x\n2024-02-01,Inception,2010,x\n"
    WATCHED = "﻿Date,Name,Year,Letterboxd URI\n2024-03-01,The Matrix,1999,x\n"

    def test_parse_and_apply(self):
        parsed = I.parse_letterboxd({"ratings.csv": self.RATINGS, "watchlist.csv": self.WATCHLIST, "watched.csv": self.WATCHED})
        self.assertEqual(len(parsed["rated"]), 4)
        self.assertEqual(len(parsed["watchlist"]), 2)
        self.assertEqual(parsed["watched"][0][0], "The Matrix")
        calls = []
        counts = I.apply("u2", parsed, FACTS, lambda u, f, t, c: calls.append((f, c)), lambda u, ids: {7: "liked"})
        self.assertIn((2, "liked"), calls)              # 5 stars; also on the watchlist - the rating wins
        self.assertIn((5, "not"), calls)                # 1.5 stars
        self.assertNotIn(3, [f for f, _ in calls])       # Heat 3 stars: seen, no signal
        self.assertNotIn(7, [f for f, _ in calls])       # Arrival already marked on the site: kept
        self.assertEqual(counts["kept_site"], 1)
        self.assertEqual(counts["unmatched"], 1)
        self.assertEqual(counts["unmatched_examples"], ["Some Film Not Here (2001)"])

    def test_year_off_by_one_and_junk(self):
        cat = I.Catalogue(FACTS)
        self.assertEqual(cat.find("the matrix", 2000)[0], 4)
        self.assertIsNone(cat.find("The Matrix", 2010))
        self.assertEqual(I.parse_letterboxd({"x.csv": "not,a,letterboxd\nfile,at,all\n"}),
                         {"rated": [], "watched": [], "watchlist": []})


if __name__ == "__main__":
    unittest.main()
