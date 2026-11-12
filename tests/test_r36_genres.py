"""Round 36 (12 Nov): GENRE_MAIN - filters use each title's main genres (web/genres.py).
No real database or model:  python -m unittest tests.test_r36_genres"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

T = tempfile.mkdtemp()
for k, f in (("SOCIAL_DB", "social.db"), ("HISTORY_DB", "history.db"), ("TRAKT_DB", "trakt.db"), ("ALERTS_DB", "alerts.db"),
             ("USERS_DB", "users.db"), ("FACTS_DB", "facts.db"), ("MOVIES_DB", "movies.db"), ("CHATS_DB", "chats.db"),
             ("MEMORY_DB", "memory.db"), ("TMDB_EXTRA_DB", "tmdb_extra.db")):
    os.environ[k] = os.path.join(T, f)
os.environ["GENRE_MAIN"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(ROOT, "web"), ROOT]

FILMS = [  # id, title, TMDB genres, IMDb id, IMDb genres (None = no IMDb row)
    (1, "Inside Out", ["Animation", "Family", "Adventure", "Drama", "Comedy"], "tt1", "Adventure,Animation,Comedy"),
    (2, "The Wolf of Wall Street", ["Crime", "Drama", "Comedy"], "tt2", "Biography,Comedy,Crime"),
    (3, "Forrest Gump", ["Comedy", "Drama", "Romance"], "tt3", "Drama,Romance"),
    (4, "The Green Mile", ["Fantasy", "Drama", "Crime"], "tt4", "Crime,Drama,Fantasy"),
    (5, "Zombieland", ["Comedy", "Horror"], "tt5", "Adventure,Comedy,Fantasy"),
    (6, "Small Film", ["Drama", "Comedy"], None, None),
    (7, "No IMDb Genres", ["Drama", "Comedy"], "tt7", "\\N"),
    (8, "Despicable Me 3", ["Action", "Animation", "Adventure", "Comedy", "Family"], "tt8", "Adventure,Animation,Comedy"),
    (9, "Interstellar", ["Adventure", "Drama", "Science Fiction"], "tt9", "Adventure,Drama,Sci-Fi"),
    (10, "Disagree", ["Drama"], "tt10", "Comedy"),
]
c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text, us_release_date text,
  vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real, overview text, in_catalog integer);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
""")
for fid, t, g, imdb, _ig in FILMS:
    c.execute("insert into films values (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
              (fid, t, t, 2010, "2010-01-01", "2010-01-01", 5000 + fid, 7.0 + fid / 20, 110, json.dumps(g), imdb, 10.0, ""))
c.commit()
c.close()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_demo_tv.py"), os.environ["FACTS_DB"]], check=True, capture_output=True)
c = sqlite3.connect(os.environ["FACTS_DB"])
for fid, t, g, imdb, ig in FILMS:
    if imdb:
        c.execute("insert or replace into imdb_titles(tconst, title_type, primary_title, genres) values (?, 'movie', ?, ?)", (imdb, t, ig))
# shows: every demo show is adult animation (TMDB Animation, Comedy, Sci-Fi & Fantasy; IMDb Adventure, Animation, Comedy)
# except one kids' show (TMDB Animation, Kids; IMDb Animation, Comedy, Family)
SHOWS = [r[0] for r in c.execute("select tmdb_id from tv_shows order by tmdb_id")]
KIDS = SHOWS[0]
for sid in SHOWS:
    kids = sid == KIDS
    c.execute("update tv_shows set genres_json = ?, imdb_id = ? where tmdb_id = ?",
              (json.dumps(["Animation", "Kids"] if kids else ["Animation", "Comedy", "Sci-Fi & Fantasy"]), f"tvx{sid}", sid))
    c.execute("insert or replace into imdb_titles(tconst, title_type, primary_title, genres) values (?, 'tvSeries', ?, ?)",
              (f"tvx{sid}", f"Show {sid}", "Animation,Comedy,Family" if kids else "Adventure,Animation,Comedy"))
    c.execute("insert or replace into imdb_ratings(imdb_id, rating, votes) values (?, 8.0, 20000)", (f"tvx{sid}",))
c.commit()
c.close()

import discovery  # noqa: E402
import genres  # noqa: E402
import tvdata  # noqa: E402
import user_prefs  # noqa: E402

HITS = [{"id": f[0], "title": f[1]} for f in FILMS]


def kept(settings):
    out = user_prefs.excluded(settings, HITS)
    return [h["id"] for h in HITS if h["id"] not in out]


class MainGenres(unittest.TestCase):
    def test_split_rules(self):
        g = genres.films([f[0] for f in FILMS])
        self.assertEqual(set(g[1][0]), {"Animation", "Adventure", "Comedy", "Family"})       # Family kept: animated
        self.assertEqual(set(g[2][0]), {"Crime", "Comedy"})
        self.assertEqual(set(g[3][0]), {"Drama", "Romance"})
        self.assertIn("Horror", g[5][1])
        self.assertNotIn("Horror", g[5][0])
        self.assertEqual(set(g[6][0]), {"Drama", "Comedy"})                                   # no IMDb row: TMDB's
        self.assertEqual(set(g[7][0]), {"Drama", "Comedy"})                                   # IMDb \N: TMDB's
        self.assertNotIn("Action", g[8][0])                                                   # round 27's case
        self.assertIn("Science Fiction", g[9][0])                                             # Sci-Fi mapped
        self.assertEqual(set(g[10][0]), {"Drama"})                                            # no overlap: TMDB's
        self.assertEqual(list(g[2][2]), ["Crime", "Comedy"])                                  # TMDB order kept

    def test_tv_split(self):
        self.assertEqual(genres.tv_split(["Crime", "Drama", "Mystery"], "Crime,Drama,Thriller")[0], ["Crime", "Drama", "Thriller"])
        self.assertEqual(genres.tv_split(["Action & Adventure", "Kids"], None)[0], ["Action", "Adventure", "Family"])
        self.assertTrue(genres.tv_mood_ok({"Drama"}, "Horror", horror_keyword=True))
        self.assertFalse(genres.tv_mood_ok({"Animation", "Comedy"}, "Family"))


class PickAndChatFilters(unittest.TestCase):
    def test_moving_is_drama_by_main_genre(self):
        self.assertEqual(kept({"only_genres": ["Drama"], "strict": True}), [3, 4, 6, 7, 9, 10])

    def test_funny_drops_forrest_gump(self):
        k = kept({"only_genres": ["Comedy"], "strict": True})
        self.assertNotIn(3, k)
        self.assertTrue({1, 2, 5, 8} <= set(k))

    def test_never_uses_both_lists(self):
        self.assertNotIn(5, kept({"never_genres": ["Horror"]}))
        self.assertIn(3, kept({"never_genres": ["Horror"]}))

    def test_tv_cosy_mood_skips_adult_animation(self):
        r = tvdata.pick("", "Family", None, None, None, [], "familiar", n=3)
        ids = [x["id"] for x in r["picks"] + r["candidates"]]
        self.assertEqual(ids, [KIDS])


class Home(unittest.TestCase):
    def test_chip_filter(self):
        ids = [f[0] for f in FILMS]
        self.assertEqual(discovery.match_genres(ids, ["Drama"]), [3, 4, 6, 7, 9, 10])
        self.assertEqual(discovery.match_genres(ids, ["Comedy", "Crime"], "all"), [2])

    def test_top_rated_uses_main_genres(self):
        top, total = discovery.top_rated(["Drama"])
        self.assertNotIn(2, top)
        self.assertNotIn(1, top)
        self.assertIn(3, top)
        self.assertEqual(total, len(top))


if __name__ == "__main__":
    unittest.main()
