"""Round 36 (12 Nov): onboarding example posters are hand-picked; posters keep 2:3.
No real database:  python -m unittest tests.test_r36_onboarding"""
import json
import os
import re
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
sys.path.insert(0, HERE)

FILMS = [  # tmdb_id, title, year, genres, votes, keywords, has_poster
    (1, "John Wick", 2014, ["Action", "Thriller"], 20000, ["revenge", "hitman"], 1),
    (2, "Prisoners", 2013, ["Drama", "Thriller", "Crime"], 11000, ["kidnapping"], 1),
    (3, "The Silence of the Lambs", 1991, ["Crime", "Drama", "Thriller"], 16000, ["serial killer"], 1),
    (4, "Nightcrawler", 2014, ["Crime", "Drama", "Thriller"], 10000, ["journalism"], 1),
    (5, "Se7en", 1995, ["Crime", "Mystery", "Thriller"], 20000, ["serial killer"], 1),
    (6, "The Wolf of Wall Street", 2013, ["Crime", "Drama", "Comedy"], 23000, ["biography", "based on true story"], 1),
    (7, "Inside Out", 2015, ["Animation", "Family", "Adventure", "Drama", "Comedy"], 21000, ["family relationships"], 1),
    (8, "The Green Mile", 1999, ["Fantasy", "Drama", "Crime"], 17000, ["prison"], 1),
    (9, "Good Will Hunting", 1997, ["Drama"], 12000, ["genius"], 1),
    (10, "The Pursuit of Happyness", 2006, ["Drama"], 9000, ["based on true story"], 1),
    (11, "The Lion King", 2019, ["Adventure", "Drama", "Family", "Animation"], 10000, ["remake"], 1),
    (12, "The Lion King", 1994, ["Family", "Animation", "Drama"], 18000, ["musical"], 1),
    (13, "Pride & Prejudice", 2005, ["Drama", "Romance"], 7000, ["based on novel or book"], 1),
    (14, "Rango", 2011, ["Animation", "Comedy", "Family", "Western"], 4000, ["western"], 1),
    (15, "Forrest Gump", 1994, ["Comedy", "Drama", "Romance"], 27000, ["friendship"], 1),
    (16, "Superbad", 2007, ["Comedy"], 8000, ["high school"], 0),          # pick without a poster -> skipped
    (17, "The Hangover", 2009, ["Comedy"], 15000, ["bachelor party"], 1),
]


def make_facts():
    c = sqlite3.connect(FACTS)
    c.executescript("""create table films(tmdb_id integer, title text, original_title text, year integer, genres_json text,
        vote_count integer, in_catalog integer, runtime integer, imdb_id text);
        create table film_images(tmdb_id integer, poster_path text);
        create table keywords(tmdb_id integer, name text);
        create table watch_providers(tmdb_id integer, region text, kind text, provider_name text, priority integer, fetched_at text);""")
    for fid, t, y, g, v, kws, poster in FILMS:
        c.execute("insert into films values (?,?,?,?,?,?,1,120,null)", (fid, t, t, y, json.dumps(g), v))
        c.execute("insert into film_images values (?,?)", (fid, f"/p{fid}.jpg" if poster else None))
        c.executemany("insert into keywords values (?,?)", [(fid, k) for k in kws])
    c.commit()
    c.close()


make_facts()
import user_prefs as U  # noqa: E402


def titles(key):
    return [(f["title"], f["year"]) for g in U.groups() if g["key"] == key for f in g["films"]]


class Picks(unittest.TestCase):
    def test_thrillers_are_the_picks_not_john_wick(self):
        self.assertEqual(titles("thrill"), [("Prisoners", 2013), ("The Silence of the Lambs", 1991), ("Nightcrawler", 2014)])

    def test_dramas_have_no_wolf_of_wall_street_or_inside_out(self):
        t = [x[0] for x in titles("drama")]
        self.assertEqual(t, ["The Green Mile", "Good Will Hunting", "The Pursuit of Happyness"])

    def test_year_picks_the_1994_lion_king(self):
        self.assertIn(("The Lion King", 1994), titles("anim"))
        self.assertNotIn(("The Lion King", 2019), titles("anim"))

    def test_title_matching_ignores_ampersand(self):
        self.assertIn(("Pride & Prejudice", 2005), titles("romance"))

    def test_pick_without_poster_skipped(self):
        self.assertEqual([x[0] for x in titles("comedy")][:1], ["The Hangover"])

    def test_group_without_picks_falls_back_to_query(self):
        self.assertIn("Rango", [x[0] for x in titles("western")])

    def test_no_film_shown_twice(self):
        ids = [f["id"] for g in U.groups() for f in g["films"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_group_has_picks_and_missing_report(self):
        self.assertEqual({g[0] for g in U.GROUPS}, set(U.PICKS))
        self.assertTrue(all(len(v) >= 3 for v in U.PICKS.values()))
        miss = U.missing_picks()
        self.assertIn(("western", "Unforgiven", 1992), miss)
        self.assertIn(("comedy", "Superbad", 2007), miss)
        self.assertNotIn(("thrill", "Prisoners", 2013), miss)


class PosterShape(unittest.TestCase):
    def test_onboarding_posters_ignore_the_height_attribute(self):
        with open(os.path.join(HERE, "web", "v8", "app.css"), encoding="utf-8") as fh:
            css = fh.read()
        for sel in (r"\.tf img", r"\.tg-pp img"):
            rule = re.search(sel + r"\s*\{([^}]*)\}", css).group(1)
            self.assertIn("height: auto", rule)
            self.assertIn("aspect-ratio: 2 / 3", rule)


if __name__ == "__main__":
    unittest.main()
