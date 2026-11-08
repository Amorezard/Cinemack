"""6 Nov: per-chat filter bar (web/user_prefs.py clean_chat / merge_chat / excluded), no real database or model:
    python -m unittest tests.test_chat_filters"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
FACTS = os.path.join(TMP, "facts.db")
os.environ["FACTS_DB"] = FACTS
os.environ["USERS_DB"] = os.path.join(TMP, "users.db")
sys.path.insert(0, os.path.join(HERE, "web"))

FILMS = [  # tmdb_id, title, year, genres, runtime
    (1, "Heat", 1995, ["Crime", "Drama", "Action"], 170),
    (2, "Groundhog Day", 1993, ["Comedy", "Fantasy", "Romance"], 101),
    (3, "Paddington 2", 2017, ["Comedy", "Family", "Adventure"], 104),
    (4, "Alien", 1979, ["Horror", "Science Fiction"], 117),
    (5, "Clueless", 1995, ["Comedy", "Romance"], 97),
    (6, "Mystery Film", None, [], None),           # nothing known: never removed by year / genre / length
]


def make_facts():
    c = sqlite3.connect(FACTS)
    c.executescript("""create table films(tmdb_id integer, title text, original_title text, year integer, genres_json text,
        vote_count integer, in_catalog integer, runtime integer, imdb_id text);
        create table watch_providers(tmdb_id integer, region text, kind text, provider_name text, priority integer, fetched_at text);
        create table certifications(tmdb_id integer, region text, certification text, release_type integer);""")
    for fid, t, y, g, rt in FILMS:
        c.execute("insert into films values (?,?,?,?,?,100,1,?,null)", (fid, t, t, y, json.dumps(g), rt))
    c.executemany("insert into watch_providers values (?,?,?,?,?,?)", [
        (2, "US", "flatrate", "Netflix", 1, "2026-10"), (5, "US", "flatrate", "Paramount Plus", 1, "2026-10"),
        (1, "US", "rent", "Netflix", 1, "2026-10")])      # rent does not count as "on my services"
    c.executemany("insert into certifications values (?,?,?,?)", [
        (1, "US", "R", 3), (2, "US", "PG", 3), (3, "US", "PG", 3), (4, "US", "R", 3), (5, "US", "PG-13", 3)])
    c.commit()
    c.close()


make_facts()
import user_prefs as U  # noqa: E402

HITS = [{"id": f[0], "title": f[1]} for f in FILMS]


def kept(settings):
    U.FACTS = FACTS               # another test module may have imported user_prefs with its own facts.db
    U._C.pop("f", None)
    gone = U.excluded(settings, HITS)
    return [h["title"] for h in HITS if h["id"] not in gone]


class Clean(unittest.TestCase):
    def test_junk_dropped(self):
        self.assertEqual(U.clean_chat({"era": "1890s", "genre": "Sludge", "max_runtime": 95, "max_cert": "NC-17",
                                       "services_only": "yes", "x": 1}), {})
        self.assertEqual(U.clean_chat(None), {})
        self.assertEqual(U.clean_chat({"era": "1990s", "genre": "Comedy", "max_runtime": "120", "max_cert": "PG",
                                       "services_only": True}),
                         {"era": "1990s", "genre": "Comedy", "max_runtime": 120, "max_cert": "PG", "services_only": True})

    def test_no_filters_leaves_settings_alone(self):
        s = dict(U.DEFAULTS)
        self.assertEqual(U.merge_chat(s, {}), s)
        self.assertEqual(U.tag(U.merge_chat(s, None)), "")


class Merge(unittest.TestCase):
    def test_stricter_value_wins(self):
        s = dict(U.DEFAULTS, min_year=1992, max_runtime=110, max_cert="PG")
        m = U.merge_chat(s, {"era": "1990s", "max_runtime": 150, "max_cert": "PG-13"})
        self.assertEqual((m["min_year"], m["max_year"], m["max_runtime"], m["max_cert"]), (1992, 1999, 110, "PG"))
        m = U.merge_chat(dict(U.DEFAULTS), {"era": "pre1970"})
        self.assertIsNone(m["min_year"])
        self.assertEqual(m["max_year"], 1969)

    def test_services_only_needs_services(self):
        self.assertNotIn("services_only", U.merge_chat(dict(U.DEFAULTS), {"services_only": True}))
        self.assertTrue(U.merge_chat(dict(U.DEFAULTS, services=["netflix"]), {"services_only": True})["services_only"])

    def test_cache_keys_change(self):
        s = dict(U.DEFAULTS)
        m = U.merge_chat(s, {"genre": "Comedy"})
        self.assertNotEqual(U.tag(m), U.tag(s))
        self.assertNotEqual(U.filter_tag(m), U.filter_tag(s))
        self.assertNotEqual(U.filter_tag(U.merge_chat(s, {"genre": "Horror"})), U.filter_tag(m))


class Excluded(unittest.TestCase):
    def test_era_genre_length(self):
        self.assertEqual(kept(U.merge_chat(dict(U.DEFAULTS), {"era": "1990s"})), ["Heat", "Groundhog Day", "Clueless", "Mystery Film"])
        self.assertEqual(kept(U.merge_chat(dict(U.DEFAULTS), {"genre": "Comedy"})),
                         ["Groundhog Day", "Paddington 2", "Clueless", "Mystery Film"])
        self.assertEqual(kept(U.merge_chat(dict(U.DEFAULTS), {"era": "1990s", "genre": "Comedy", "max_runtime": 90})),
                         ["Mystery Film"])

    def test_rating(self):
        self.assertEqual(kept(U.merge_chat(dict(U.DEFAULTS), {"max_cert": "PG"})), ["Groundhog Day", "Paddington 2", "Mystery Film"])

    def test_services_only(self):
        m = U.merge_chat(dict(U.DEFAULTS, services=["netflix", "paramount"]), {"services_only": True})
        self.assertEqual(kept(m), ["Groundhog Day", "Clueless"])        # Heat is only for rent; unlisted films go

    def test_saved_never_genres_still_apply(self):
        m = U.merge_chat(dict(U.DEFAULTS, never_genres=["Romance"]), {"genre": "Comedy"})
        self.assertEqual(kept(m), ["Paddington 2", "Mystery Film"])


class Prompt(unittest.TestCase):
    def test_lines_for_recommendations_only(self):
        m = U.merge_chat(dict(U.DEFAULTS, services=["netflix"]), {"era": "1980s", "genre": "Horror", "services_only": True})
        b = U.prompt_block(m, "recommend", "recommend something")
        for want in ("1980 or later", "1989 or earlier", "genre: Horror", "streaming services", "loosening the filters"):
            self.assertIn(want, b)
        self.assertNotIn("1989 or earlier", U.prompt_block(m, "facts", "who directed Alien?"))


if __name__ == "__main__":
    unittest.main()
