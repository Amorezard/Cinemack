"""v8.1 round 49 (17 Nov 2026): following a person (web/follows.py) and the nightly Trakt history refresh
(tools/v81_trakt_autosync.py). No network, no real user data: a made-up facts.db and Guardian store."""
import datetime
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import follows as F  # noqa: E402
import guardian_live as GL  # noqa: E402
import v81_trakt_autosync as A  # noqa: E402

TODAY = datetime.date(2026, 11, 17)


def make_facts(path):
    c = sqlite3.connect(path)
    c.executescript("""
    create table films(tmdb_id integer primary key, title text, release_date text);
    create table people(person_id integer primary key, name text);
    create table credits(tmdb_id integer, person_id integer, job text, kind text, character text, ord integer);
    """)
    c.executemany("insert into films values (?,?,?)", [
        (1, "Old One", "2019-05-01"), (2, "Last Year", "2025-10-10"), (3, "Out Now", "2026-11-01"),
        (4, "Next Spring", "2027-03-12"), (5, "Far Off", "2028-07-01"), (6, "No Date Yet", None), (7, "Year Only", "2027")])
    c.executemany("insert into people values (?,?)", [(10, "Ada Vale"), (11, "Ben Okoro"), (12, "Cher"), (13, "Ada Valentine")])
    c.executemany("insert into credits values (?,?,?,?,?,?)", [
        (1, 10, "Director", "crew", None, None), (2, 10, "Director", "crew", None, None), (2, 10, "Writer", "crew", None, None),
        (3, 10, None, "cast", "The Judge", 4), (4, 10, "Director", "crew", None, None), (5, 10, "Producer", "crew", None, None),
        (6, 10, "Director", "crew", None, None), (7, 10, "Director", "crew", None, None),
        (3, 11, None, "cast", "Sam", 0), (4, 11, None, "cast", "Lee", 1), (3, 12, None, "cast", "Herself", 2),
        (3, 13, None, "cast", "Guard", 9)])
    c.commit()
    return c


def make_guardian(path):
    c = GL.connect(path)
    rows = [(3, "a1", "review", "Out Now review - Ada Vale steals it", "https://www.theguardian.com/film/a1", "2026-11-02", "", "", 4, ""),
            (3, "a2", "news", "Box office round-up", "https://www.theguardian.com/film/a2", "2026-11-03", "Director  ada   vale on her next film", "", None, ""),
            (3, "a3", "news", "Ada Valentine joins cast", "https://www.theguardian.com/film/a3", "2026-11-04", "", "", None, ""),
            (3, "a4", "news", "Ada Vale interview", "http://evil.example.com/a4", "2026-11-05", "", "", None, ""),
            (2, "a1", "review", "Out Now review - Ada Vale steals it", "https://www.theguardian.com/film/a1", "2026-11-02", "", "", 4, "")]
    c.executemany("insert into articles values (?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fp = os.path.join(self.tmp, "facts.db")
        self.facts = make_facts(self.fp)
        self.db = os.path.join(self.tmp, "follows.db")
        self.gdb = os.path.join(self.tmp, "guardian.db")
        make_guardian(self.gdb)

    def tearDown(self):
        self.facts.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestFollow(Base):
    def test_follow_unfollow_and_list(self):
        self.assertEqual(F.following("u1", self.facts, self.db), [])
        self.assertEqual(F.follow("u1", 10, self.facts, self.db)["name"], "Ada Vale")
        F.follow("u1", 10, self.facts, self.db)                                   # twice is once
        F.follow("u1", "11", self.facts, self.db)
        self.assertEqual({p["person_id"] for p in F.following("u1", self.facts, self.db)}, {10, 11})
        self.assertEqual(F.following("u2", self.facts, self.db), [])                # private to each person
        F.unfollow("u1", 11, self.db)
        self.assertEqual([p["person_id"] for p in F.following("u1", self.facts, self.db)], [10])

    def test_refuses_unknown_people_bad_ids_and_no_user(self):
        for bad in (999, 0, -3, "x", None, 10 ** 11):
            with self.assertRaises(F.FollowError):
                F.follow("u1", bad, self.facts, self.db)
        with self.assertRaises(F.FollowError):
            F.follow("", 10, self.facts, self.db)

    def test_limit(self):
        old, F.MAX_FOLLOWS = F.MAX_FOLLOWS, 2
        try:
            F.follow("u1", 10, self.facts, self.db)
            F.follow("u1", 11, self.facts, self.db)
            with self.assertRaises(F.FollowError):
                F.follow("u1", 12, self.facts, self.db)
            F.follow("u1", 10, self.facts, self.db)                               # one already followed is fine
        finally:
            F.MAX_FOLLOWS = old

    def test_feed_has_only_dated_films_split_by_today(self):
        F.follow("u1", 10, self.facts, self.db)
        p = F.feed("u1", self.facts, TODAY, self.db, self.gdb)[0]
        self.assertEqual([x["title"] for x in p["coming"]], ["Next Spring", "Far Off"])      # soonest first
        self.assertEqual([x["title"] for x in p["latest"]], ["Out Now", "Last Year", "Old One"])
        titles = [x["title"] for x in p["coming"] + p["latest"]]
        self.assertNotIn("No Date Yet", titles)                                    # nothing guessed
        self.assertNotIn("Year Only", titles)
        self.assertEqual(len(titles), len(set(titles)))                            # two credits on one film: one row
        by = {x["title"]: x["role"] for x in p["coming"] + p["latest"]}
        self.assertEqual(by["Last Year"], "Director")                              # the senior credit wins
        self.assertEqual(by["Out Now"], "as The Judge")
        self.assertEqual(by["Far Off"], "Producer")
        self.assertTrue(all("_rank" not in x for x in p["coming"] + p["latest"]))

    def test_articles_need_the_full_name_and_a_guardian_link(self):
        got = F.articles_about("Ada Vale", self.gdb)
        self.assertEqual([a["url"][-2:] for a in got], ["a2", "a1"])               # newest first, no duplicate
        self.assertTrue(all(a["url"].startswith("https://www.theguardian.com/") for a in got))
        self.assertEqual(F.articles_about("Cher", self.gdb), [])                   # one-word names: not trusted
        self.assertEqual(F.articles_about("Ben Okoro", self.gdb), [])
        self.assertEqual(F.articles_about("Ada Vale", os.path.join(self.tmp, "none.db")), [])

    def test_people_of_a_film_and_search(self):
        F.follow("u1", 11, self.facts, self.db)
        ppl = F.people_of(self.facts, 4, "u1", self.db)
        self.assertEqual([(p["name"], p["role"], p["following"]) for p in ppl],
                         [("Ada Vale", "Director", False), ("Ben Okoro", "as Lee", True)])
        self.assertEqual([p["name"] for p in F.search(self.facts, "ada")], ["Ada Vale", "Ada Valentine"])
        self.assertEqual(F.search(self.facts, "a"), [])
        self.assertEqual(F.search(self.facts, "%%"), [])
        self.assertEqual(F.search(self.facts, "x' or 1=1 --"), [])

    def test_delete_and_move_do_not_create_the_store(self):
        self.assertEqual(F.delete_user("u1", self.db), 0)
        self.assertEqual(F.move_user("b1", "acct1", self.db), 0)
        self.assertFalse(os.path.exists(self.db))
        F.follow("b1", 10, self.facts, self.db)
        F.follow("acct1", 10, self.facts, self.db)
        F.follow("b1", 11, self.facts, self.db)
        F.move_user("b1", "acct1", self.db)
        self.assertEqual({p["person_id"] for p in F.following("acct1", self.facts, self.db)}, {10, 11})
        self.assertEqual(F.following("b1", self.facts, self.db), [])
        self.assertEqual(F.delete_user("acct1", self.db), 2)
        self.assertEqual(F.following("acct1", self.facts, self.db), [])


class TestRoutes(Base):
    def test_404_while_off_and_work_while_on(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest("fastapi test client not available: %s" % e)
        os.environ["FOLLOWS_DB"] = self.db
        os.environ["GUARDIAN_LIVE_DB"] = self.gdb
        try:
            os.environ["FOLLOW_PEOPLE"] = "0"
            importlib.reload(F)
            app = FastAPI()
            F.install(app, lambda r: None, lambda r: "u1", facts_path=self.fp)
            cl = TestClient(app)
            self.assertEqual(cl.get("/api/v8/follows").status_code, 404)
            self.assertEqual(cl.post("/api/v8/follows", json={"person_id": 10}).status_code, 404)
            self.assertEqual(cl.get("/api/v8/film/3/people").status_code, 404)
            self.assertEqual(cl.get("/api/v8/people/search?q=ada").status_code, 404)
            self.assertFalse(os.path.exists(self.db))                              # off: nothing stored

            os.environ["FOLLOW_PEOPLE"] = "1"
            importlib.reload(GL)
            importlib.reload(F)
            app = FastAPI()
            F.install(app, lambda r: None, lambda r: "u1", facts_path=self.fp)
            cl = TestClient(app)
            self.assertEqual(cl.post("/api/v8/follows", json={"person_id": 10}).json()["following"], True)
            self.assertEqual(cl.post("/api/v8/follows", json={"person_id": 999}).status_code, 400)
            r = cl.get("/api/v8/follows")
            self.assertEqual(r.headers.get("cache-control"), "no-store")
            self.assertEqual(r.json()["people"][0]["name"], "Ada Vale")
            self.assertEqual(len(r.json()["people"][0]["articles"]), 2)
            self.assertEqual(cl.get("/api/v8/film/0/people").status_code, 400)
            self.assertTrue(cl.get("/api/v8/film/4/people").json()["people"][0]["following"])
            self.assertEqual(len(cl.get("/api/v8/people/search?q=ada").json()["people"]), 2)
            self.assertEqual(cl.post("/api/v8/follows", json={"person_id": 10, "on": False}).json()["following"], False)
            self.assertEqual(cl.get("/api/v8/follows").json()["people"], [])

            app = FastAPI()                                                         # the gate is asked first
            F.install(app, lambda r: F.JSONResponse({"error": "code"}, status_code=401), lambda r: "u1", facts_path=self.fp)
            self.assertEqual(TestClient(app).get("/api/v8/follows").status_code, 401)
            app = FastAPI()                                                         # no browser id: no write
            F.install(app, lambda r: None, lambda r: "", facts_path=self.fp)
            self.assertEqual(TestClient(app).post("/api/v8/follows", json={"person_id": 10}).status_code, 400)
        finally:
            for k in ("FOLLOW_PEOPLE", "FOLLOWS_DB", "GUARDIAN_LIVE_DB"):
                os.environ.pop(k, None)
            importlib.reload(GL)
            importlib.reload(F)

    def test_wired_into_the_server_and_account_deletion(self):
        srv = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
        self.assertIn("follows.install(app, _gate, user_of)", srv)
        v8 = open(os.path.join(ROOT, "web", "v8routes.py"), encoding="utf-8").read()
        self.assertIn("follows.delete_user(key)", v8)
        self.assertIn("follows.move_user(old_key, new_key)", v8)
        self.assertFalse(F.FOLLOW_PEOPLE and not os.environ.get("FOLLOW_PEOPLE"))   # off unless set


class TestAutosync(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 17, 4, 0, 0)

    def test_plan_oldest_first_and_recent_skipped(self):
        rows = [("a", "2026-11-16T23:00:00"), ("b", "2026-11-10T04:00:00"), ("c", None), ("d", "2026-11-15T04:00:00"),
                ("e", "garbage"), ("f", "2026-11-18T04:00:00")]
        due, recent = A.plan(rows, self.NOW)
        self.assertEqual(recent, 1)                                                # only "a" (5 h ago)
        self.assertEqual(due[:2], ["c", "e"])                                      # never synced first
        self.assertEqual(due[2:], ["b", "d", "f"])                                 # then oldest; a future stamp is due
        self.assertEqual(A.plan(rows, self.NOW, limit=2)[0], ["c", "e"])

    def test_run_counts_pauses_and_stops_after_errors_in_a_row(self):
        naps, log = [], []

        def fn(u):
            if u.startswith("bad"):
                raise RuntimeError("token=SECRETVALUE refused")
            return 3
        out = A.run(["ok1", "bad1", "ok2"], fn, sleep=naps.append, log=log.append)
        self.assertEqual(out, {"done": 2, "failed": 1, "stopped_early": False})
        self.assertEqual(len(naps), 2)
        out = A.run(["ok1", "bad1", "bad2", "bad3", "ok2", "ok3"], fn, sleep=naps.append, log=log.append)
        self.assertEqual(out, {"done": 1, "failed": 3, "stopped_early": True})
        self.assertTrue(all(set(r) <= {"at", "account", "history", "error"} for r in log))
        self.assertTrue(all(r["account"] not in ("ok1", "bad1") and len(r["account"]) == 10 for r in log))
        out = A.run(["bad1", "bad2", "bad3"], fn, sleep=naps.append)
        self.assertEqual(out["stopped_early"], False)                               # nobody was left out

    def test_dry_run_reads_only_and_run_is_refused_without_the_switch(self):
        tmp = tempfile.mkdtemp()
        try:
            db = os.path.join(tmp, "trakt.db")
            c = sqlite3.connect(db)
            c.execute("create table tokens(user_key text primary key, access text, refresh text, expires_at real, "
                      "created_at text, last_sync text)")
            c.execute("insert into tokens values ('acct1','tok','ref',0,'2026-11-01',null)")
            c.commit()
            c.close()
            before = open(db, "rb").read()
            os.environ["TRAKT_DB"] = db
            os.environ.pop("TRAKT_AUTOSYNC", None)
            self.assertEqual(A.main([]), 0)
            self.assertEqual(A.main(["--run"]), 2)                                 # switch off: refused before any import
            self.assertEqual(open(db, "rb").read(), before)
            self.assertEqual(A._rows(os.path.join(tmp, "missing.db")), [])
        finally:
            os.environ.pop("TRAKT_DB", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_history_only_never_touches_ratings_or_watchlist(self):
        src = open(os.path.join(ROOT, "tools", "v81_trakt_autosync.py"), encoding="utf-8").read()
        body = src[src.index("def history_only"):src.index("def run(")]
        self.assertNotIn("ratings", body)
        self.assertNotIn("watchlist", body)
        self.assertNotIn("save_review", src)
        self.assertNotIn("film_info", src)


if __name__ == "__main__":
    unittest.main()
