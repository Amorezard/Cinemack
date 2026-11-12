"""Round 37 (12 Nov): TV in onboarding (web/tv_prefs.py), Pick Tonight for shows uses it; Trakt export upload;
form fields align.   No real database, model or network:  python -m unittest tests.test_r36_tv"""
import json
import os
import re
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
c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text, us_release_date text,
  vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real, overview text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
""")
c.execute("insert into films values (550, 'Fight Club', 'Fight Club', 1999, '1999-10-15', '1999-10-15', 30000, 8.4, 139, '[\"Drama\"]', 'tt0137523', 50, '')")
c.commit()
c.close()
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_demo_tv.py"), os.environ["FACTS_DB"]], check=True, capture_output=True)
c = sqlite3.connect(os.environ["FACTS_DB"])
SHOWS = [r[0] for r in c.execute("select tmdb_id from tv_shows order by tmdb_id")]
BB, OFFICE = SHOWS[0], SHOWS[1]
for k, sid in enumerate(SHOWS):
    if sid == BB:
        name, year, tg, ig = "Breaking Bad", 2008, ["Crime", "Drama"], "Crime,Drama,Thriller"
    elif sid == OFFICE:
        name, year, tg, ig = "The Office", 2005, ["Comedy"], "Comedy"
    else:
        name, year = f"Demo Show {k}", 2015
        tg, ig = (["Comedy"], "Comedy") if k % 2 else (["Drama"], "Drama")
    c.execute("update tv_shows set name = ?, first_year = ?, genres_json = ?, imdb_id = ?, poster_path = ?, vote_count = ? where tmdb_id = ?",
              (name, year, json.dumps(tg), f"tvx{sid}", f"/p{sid}.jpg", 500 + k, sid))
    c.execute("insert or replace into imdb_titles(tconst, title_type, primary_title, genres) values (?, 'tvSeries', ?, ?)", (f"tvx{sid}", name, ig))
    c.execute("insert or replace into imdb_ratings(imdb_id, rating, votes) values (?, ?, ?)", (f"tvx{sid}", 8.0 if sid != OFFICE else 7.6, 20000 + k))
c.commit()
c.close()

import discovery  # noqa: E402
import trakt_sync  # noqa: E402
import tv_prefs  # noqa: E402
import tvdata  # noqa: E402


def order(user):
    r = tvdata.pick(user, None, None, None, None, [], "familiar", n=200)
    return [x["id"] for x in r["picks"] + r["candidates"]]


class TvOnboarding(unittest.TestCase):
    def test_groups_hand_picked_then_filled(self):
        g = {x["key"]: x for x in tv_prefs.groups()}
        self.assertEqual(g["crime"]["shows"][0]["title"], "Breaking Bad")
        self.assertEqual(g["sitcom"]["shows"][0]["title"], "The Office")
        ids = [s["id"] for x in g.values() for s in x["shows"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(x["shows"]) <= 3 for x in g.values()))

    def test_famous_skips_group_examples(self):
        ex = {s["id"] for x in tv_prefs.groups() for s in x["shows"]}
        self.assertFalse({s["id"] for s in tv_prefs.famous("u1")} & ex)

    def test_watching_follows_and_unfollows(self):
        tv_prefs.choose("u-w", BB, "Breaking Bad", "watching")
        self.assertIn(BB, {x["id"] for x in discovery.tv_following("u-w")})
        tv_prefs.choose("u-w", BB, "Breaking Bad", "liked")
        self.assertNotIn(BB, {x["id"] for x in discovery.tv_following("u-w")})
        self.assertEqual(tv_prefs.choices("u-w"), {BB: "liked"})
        tv_prefs.choose("u-w", BB, "Breaking Bad", "")
        self.assertEqual(tv_prefs.choices("u-w"), {})
        with self.assertRaises(ValueError):
            tv_prefs.choose("u-w", BB, "x", "maybe")

    def test_pick_skips_marked_shows(self):
        before = order("u-s")
        self.assertIn(BB, before)
        tv_prefs.choose("u-s", BB, "Breaking Bad", "not")
        self.assertNotIn(BB, order("u-s"))

    def test_pick_lifts_loved_kinds(self):
        before = order("u-k")
        tv_prefs.save_groups("u-k", ["sitcom"], ["drama"])
        after = order("u-k")
        self.assertLess(after.index(OFFICE), before.index(OFFICE))
        b = tv_prefs.boost("u-k", [BB, OFFICE])
        self.assertGreater(b.get(OFFICE, 0), 0)
        self.assertLess(b.get(BB, 0), 0)
        self.assertEqual(tv_prefs.boost("nobody", [BB, OFFICE]), {})
        self.assertEqual(tv_prefs.load_groups("u-k"), {"groups": ["sitcom"], "avoid": ["drama"]})


HIST = [{"id": 9001, "watched_at": "2024-03-01T20:00:00.000Z", "action": "watch", "type": "movie",
         "movie": {"title": "Fight Club", "year": 1999, "ids": {"trakt": 1, "tmdb": 550, "imdb": "tt0137523"}}},
        {"id": 9002, "watched_at": "2024-03-02T20:00:00.000Z", "action": "watch", "type": "episode",
         "episode": {"season": 1, "number": 1, "title": "Pilot"}, "show": {"title": "Breaking Bad", "year": 2008, "ids": {"tmdb": BB}}},
        {"id": 9003, "watched_at": "2024-03-03T20:00:00.000Z", "action": "watch", "type": "movie",
         "movie": {"title": "No Id", "year": 2001, "ids": {"trakt": 7}}}]


class TraktExport(unittest.TestCase):
    def test_history_twice_adds_once(self):
        a = trakt_sync.import_export("u-t", json.dumps(HIST), "watched-history.json")
        self.assertEqual((a["rows"], a["added"], len(a["issues"])), (3, 2, 1))
        b = trakt_sync.import_export("u-t", json.dumps(HIST), "watched-history.json")
        self.assertEqual((b["added"], b["duplicates"]), (0, 2))

    def test_ratings_watchlist_and_other_files(self):
        r = trakt_sync.import_export("u-t2", json.dumps([{"rated_at": "2024-01-01T00:00:00Z", "rating": 9, "type": "movie",
                                                          "movie": {"title": "Fight Club", "ids": {"tmdb": 550}}}]), "ratings-movies.json")
        self.assertEqual((r["ratings"], r["rows"]), (1, 0))
        w = trakt_sync.import_export("u-t2", json.dumps([{"listed_at": "2024-01-01T00:00:00Z", "type": "show",
                                                          "show": {"title": "The Office", "ids": {"tmdb": OFFICE}}}]), "watchlist.json")
        self.assertEqual(w["watchlist"], 1)
        self.assertIn(OFFICE, {x["id"] for x in discovery.tv_following("u-t2")})
        o = trakt_sync.import_export("u-t2", json.dumps([{"listed_at": "2024-01-01T00:00:00Z", "type": "movie",
                                                          "movie": {"ids": {"tmdb": 550}}}]), "lists-favourites.json")
        self.assertEqual((o["watchlist"], o["skipped"]), (0, 1))
        with self.assertRaises(ValueError):
            trakt_sync.import_export("u-t2", "id,title\n1,x", "watched.csv")

    def test_watched_summary_only_when_no_history(self):
        s = trakt_sync.import_export("u-t3", json.dumps([{"plays": 2, "last_watched_at": "2023-05-05T00:00:00Z",
                                                          "movie": {"title": "Fight Club", "year": 1999, "ids": {"tmdb": 550}}}]), "watched-movies.json")
        self.assertEqual(s["added"], 1)


class Forms(unittest.TestCase):
    def test_field_rows_do_not_stretch(self):
        with open(os.path.join(ROOT, "web", "v8", "app.css"), encoding="utf-8") as fh:
            rule = re.search(r"\n\.field \{([^}]*)\}", fh.read()).group(1)
        self.assertIn("align-content: start", rule)


if __name__ == "__main__":
    unittest.main()
