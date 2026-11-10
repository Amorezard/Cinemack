"""10 Nov (v8 redesign): TV progress / Continue Watching, upcoming-from-watchlist, alert opt-out, search, chat rename,
profile summary, account move/delete. Synthetic databases in a temp folder; no server, no model, no network.
    python -m unittest tests.test_v8_extra
"""
import datetime
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

D = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()   # noqa: E731
c = sqlite3.connect(os.environ["FACTS_DB"])
c.executescript("""
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text,
  us_release_date text, vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real);
create table tv_shows(tmdb_id integer primary key, name text, original_name text, first_year integer, vote_count integer,
  networks_json text, poster_path text);
create table tv_seasons(tmdb_id integer, season_number integer, air_date text, poster_path text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
""")
FILMS = [(1, "Parasite", 2019, '["Thriller","Drama","Comedy"]', D(-2000)), (2, "Heat", 1995, '["Crime","Thriller"]', D(-9000)),
         (3, "Arrival", 2016, '["Science Fiction","Drama"]', D(-3000)), (4, "Future Wide", 2026, '["Action"]', D(30)),
         (5, "Future Digital", 2026, '["Comedy"]', D(-20)), (6, "No Date Yet", 2027, '["Drama"]', None),
         (7, "Heat Wave", 2001, '["Drama"]', D(-8000))]
for fid, t, y, g, d in FILMS:
    c.execute("insert into films values (?,?,?,?,?,?,?,?,?,?,?,?)", (fid, t, t, y, d, d, 1000, 7.5, 110, g, None, 10 - fid))
    c.execute("insert into film_images values (?,?,?)", (fid, f"/p{fid}.jpg", f"/b{fid}.jpg"))
c.execute("insert into certifications values (4,'US','PG-13',3,?)", (D(30) + "T00:00:00.000Z",))
c.execute("insert into certifications values (5,'US','R',3,?)", (D(-20) + "T00:00:00.000Z",))
c.execute("insert into certifications values (5,'US','R',4,?)", (D(12) + "T00:00:00.000Z",))
c.execute("insert into watch_providers values (5,'US','flatrate',8,'Netflix',1)")
c.execute("insert into tv_shows values (100,'Slow Burn','Slow Burn',2020,500,'[\"HBO\"]','/s.jpg')")
c.execute("insert into tv_shows values (101,'Quick Fire','Quick Fire',2021,900,'[\"Netflix\"]','/q.jpg')")
c.execute("insert into tv_shows values (102,'Undated','Undated',2019,100,'[]',null)")
for sid, sn, d in ((100, 1, D(-700)), (100, 2, D(-300)), (100, 3, D(40)), (101, 1, D(-200)), (102, 1, D(-500)), (102, 2, None)):
    c.execute("insert into tv_seasons values (?,?,?,null)", (sid, sn, d))
c.commit()
c.close()

import discovery  # noqa: E402
import film_info  # noqa: E402
import history  # noqa: E402
import social  # noqa: E402
import v8extra  # noqa: E402

U, V = "browser-u", "browser-v"


def want(user, fid, title):
    with film_info._lock:
        k = film_info._users()
        k.execute("insert or replace into watch values (?,?,?,?,?,?)", (user, fid, title, "want", None, "2026-11-01T10:00:00"))
        k.commit()


class Progress(unittest.TestCase):
    def test_progress_next_episode_and_bar(self):
        v8extra.set_progress(U, 100, 2, 4)
        it = {x["id"]: x for x in v8extra.continue_watching(U)}[100]
        self.assertEqual(it["last"]["label"], "S2 E4")
        self.assertEqual(it["next"]["label"], "S2 E5")
        self.assertEqual(it["seasons_bar"], {"done": 1, "total": 2})     # season 3 has not aired: total is 2
        self.assertNotIn("percent", json.dumps(it))

    def test_finished_season_points_at_dated_next_season(self):
        v8extra.set_progress(U, 100, 2, None, finished=True)
        it = {x["id"]: x for x in v8extra.continue_watching(U)}[100]
        self.assertEqual(it["next"]["season"], 3)
        self.assertEqual(it["next"]["date"], D(40))
        self.assertEqual(it["seasons_bar"], {"done": 2, "total": 2})

    def test_bad_values_rejected(self):
        with self.assertRaises(v8extra.ProgressError):
            v8extra.set_progress(U, 100, 9, 1)                 # only 3 seasons on record
        with self.assertRaises(v8extra.ProgressError):
            v8extra.set_progress(U, 100, 1, None)              # no episode and not finished
        with self.assertRaises(v8extra.ProgressError):
            v8extra.set_progress(U, 999, 1, 1)                 # unknown show

    def test_import_only_gives_no_next_episode_or_percent(self):
        history.import_rows("imp-user", "netflix", [{"row": 1, "raw": "Quick Fire: Season 1: Pilot", "kind": "episode",
                                                    "title": "Quick Fire", "season": 1, "episode": "Pilot", "date": D(-3),
                                                    "key": "k1"}])
        it = {x["id"]: x for x in v8extra.continue_watching("imp-user")}[101]
        self.assertIsNone(it["next"])
        self.assertIsNone(it["last"]["episode"])
        self.assertIn("Pilot", it["last"]["label"])
        self.assertEqual(it["last"]["source"], "netflix")
        self.assertNotIn("seasons_bar", it)      # watching season 1 episodes says nothing about finished seasons

    def test_clear(self):
        v8extra.set_progress("clr", 101, 1, 2)
        v8extra.set_progress("clr", 101, clear=True)
        self.assertEqual(v8extra.continue_watching("clr"), [])

    def test_new_user_has_nothing(self):
        self.assertEqual(v8extra.continue_watching("nobody"), [])


class Upcoming(unittest.TestCase):
    def test_kinds_dates_and_unknowns(self):
        for fid, t in ((4, "Future Wide"), (5, "Future Digital"), (6, "No Date Yet"), (1, "Parasite")):
            want("up", fid, t)
        discovery.tv_follow("up", 100)
        discovery.tv_follow("up", 102)
        items = v8extra.upcoming("up")["items"]
        by = {(i["media"], i["id"]): i for i in items}
        self.assertEqual(by[("movie", 4)]["kind"], "theatrical")
        self.assertEqual(by[("movie", 4)]["date"], D(30))
        self.assertTrue(by[("movie", 4)]["confirmed"])
        self.assertNotIn(("movie", 5), by)          # already streaming on Netflix: not "upcoming"
        self.assertIsNone(by[("movie", 6)]["date"])  # no date anywhere: "date not announced"
        self.assertFalse(by[("movie", 6)]["confirmed"])
        self.assertNotIn(("movie", 1), by)          # old film
        self.assertEqual(by[("tv", 100)]["season"], 3)
        self.assertEqual(by[("tv", 100)]["date"], D(40))
        self.assertIsNone(by[("tv", 102)]["date"])   # season 2 listed without a date
        self.assertEqual(len(items), len({(i["media"], i["id"]) for i in items}))   # no duplicates

    def test_follow_twice_is_one_subscription(self):
        discovery.tv_follow("dup", 100)
        discovery.tv_follow("dup", 100)
        self.assertEqual(len(discovery.tv_following("dup")), 1)

    def test_mute(self):
        want("mu", 4, "Future Wide")
        v8extra.set_mute("mu", 4, True)
        self.assertFalse({i["id"]: i for i in v8extra.upcoming("mu")["items"]}[4]["notify"])
        v8extra.set_mute("mu", 4, True)                  # twice: still one row
        self.assertEqual(len(v8extra.muted("mu")), 1)
        v8extra.set_mute("mu", 4, False)
        self.assertTrue({i["id"]: i for i in v8extra.upcoming("mu")["items"]}[4]["notify"])


class SearchRename(unittest.TestCase):
    def test_search(self):
        r = v8extra.search("heat")
        self.assertEqual(r["films"][0]["title"], "Heat")
        self.assertEqual(v8extra.search("h")["films"], [])
        self.assertEqual(v8extra.search("quick")["shows"][0]["title"], "Quick Fire")

    def test_rename_only_own_chat(self):
        import chats
        chats.save_turn("ren-a", "conv-1", "q", "a")
        self.assertEqual(v8extra.rename_chat("ren-a", "conv-1", "  My   heist  night "), "My heist night")
        with self.assertRaises(LookupError):
            v8extra.rename_chat("ren-b", "conv-1", "stolen")
        self.assertEqual(chats.list_chats("ren-a")[0]["title"], "My heist night")


class Summary(unittest.TestCase):
    def test_new_user_is_all_zero(self):
        s = v8extra.summary("fresh")
        self.assertEqual({k: v for k, v in s["stats"].items() if k != "avg_rating"},
                         {"films": 0, "shows": 0, "episodes": 0, "ratings": 0, "reviews": 0, "watchlist": 0,
                          "following": 0, "liked": 0})
        self.assertIsNone(s["stats"]["avg_rating"])
        self.assertEqual(s["taste"]["inferred"], [])
        self.assertFalse(s["taste"]["enough"])
        self.assertEqual(s["favorites"]["items"], [])
        self.assertEqual(s["activity"], [])
        self.assertIsNone(s["banner"])

    def test_counts_taste_favourites_activity(self):
        k = "rich"
        for fid, t in ((1, "Parasite"), (2, "Heat"), (3, "Arrival")):
            history.add_event(k, "movie", fid, t, None, D(-fid))
        social.save_review(k, 1, 5, "Brilliant.")
        social.save_review(k, 2, 4, "")
        social.save_review(k, 7, 1.5, "")
        want(k, 4, "Future Wide")
        s = v8extra.summary(k)
        st = s["stats"]
        self.assertEqual((st["films"], st["ratings"], st["reviews"], st["watchlist"]), (3, 3, 1, 1))
        self.assertEqual(st["avg_rating"], round((5 + 4 + 1.5) / 3, 1))
        self.assertEqual(s["favorites"]["label"], "Your highest rated")
        self.assertEqual([f["id"] for f in s["favorites"]["items"]], [1, 2])     # the 1.5-star film is not a favourite
        g = {x["genre"] for x in s["taste"]["inferred"]}
        self.assertIn("Thriller", g)
        self.assertNotIn("Action", g)            # only on the watchlist, never watched or rated
        self.assertFalse(s["taste"]["enough"])   # 3 films is not enough to call it a taste profile
        kinds = {a["kind"] for a in s["activity"]}
        self.assertTrue({"watched", "reviewed", "rated", "saved"} <= kinds)
        self.assertEqual(s["banner"]["from"], "Parasite")
        self.assertEqual({r["title"] for r in s["reviews"]}, {"Parasite", "Heat", "Heat Wave"})


class Accounts(unittest.TestCase):
    def test_move_and_delete(self):
        v8extra.set_progress("guest-x", 100, 1, 3)
        v8extra.set_mute("guest-x", 4, True)
        v8extra.move_user("guest-x", "acct-x")
        self.assertEqual(v8extra.continue_watching("guest-x"), [])
        self.assertEqual(v8extra.continue_watching("acct-x")[0]["last"]["label"], "S1 E3")
        self.assertEqual(v8extra.muted("acct-x"), {4})
        v8extra.delete_user("acct-x")
        self.assertEqual(v8extra.continue_watching("acct-x"), [])
        self.assertEqual(v8extra.muted("acct-x"), set())


class Calendar(unittest.TestCase):
    def test_digital_provider_and_confirmed(self):
        cal = discovery.calendar("", 60, D(-1))
        th = [e for e in cal["events"] if e.get("film", {}).get("id") == 4]
        self.assertTrue(th and th[0]["confirmed"])
        tv = [e for e in cal["events"] if e["kind"] == "tv_season"]
        self.assertTrue(all(e["confirmed"] for e in tv))


if __name__ == "__main__":
    unittest.main()


class Concurrency(unittest.TestCase):
    def test_parallel_reads_do_not_share_a_connection(self):
        """10 Nov: Pick Tonight fetches three films at once; a shared sqlite connection raised SystemError."""
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(12) as ex:
            out = list(ex.map(lambda i: film_info._q(film_info._ro(film_info.FACTS), "select count(*) from films")[0][0], range(240)))
        self.assertEqual(set(out), {len(FILMS)})
        self.assertIsNot(film_info._ro(film_info.FACTS), None)

    def test_shared_write_connection_survives_parallel_reads(self):
        """10 Nov: Profile asks for /api/v8/profile and /api/v8/me/summary at once; history.stats() on the shared
        connection came back None ('NoneType' object is not subscriptable). dbsafe.wrap serialises each call."""
        import concurrent.futures as cf
        import dbsafe
        self.assertIsInstance(history.conn(), dbsafe.SafeConn)
        history.add_event("par", "movie", 1, "Parasite", 2019, "2026-11-01")
        with cf.ThreadPoolExecutor(16) as ex:
            out = list(ex.map(lambda i: history.stats("par")["films"] if i % 2 else len(history.history("par")), range(400)))
        self.assertEqual(set(out), {1})
