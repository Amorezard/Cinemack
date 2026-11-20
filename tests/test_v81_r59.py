"""v8.1 round 59 (20 Nov 2026): alerts and email digests about the people you follow, one switch for them, a note for
guests on the Follow section, and the nightly cleanup of guests' follows. No network; email stays in the sandbox."""
import datetime
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), ROOT]
os.environ["EMAIL_SANDBOX"] = "1"
os.environ["EMAIL_FILES"] = "0"
import follows as F  # noqa: E402


def facts(extra_films=()):
    c = sqlite3.connect(":memory:")
    c.executescript("""create table people(person_id integer primary key, name text);
      create table films(tmdb_id integer primary key, title text, release_date text);
      create table credits(tmdb_id integer, person_id integer, kind text, job text, character text, ord integer);""")
    c.execute("insert into people values (1, 'Greta Gerwig')")
    films = [(10, "Lady Bird", "2017-11-03"), (11, "Barbie", "2023-07-21"), (12, "Narnia", "2026-11-26")] + list(extra_films)
    for fid, t, d in films:
        c.execute("insert into films values (?,?,?)", (fid, t, d))
        c.execute("insert into credits values (?,?,?,?,?,?)", (fid, 1, "crew", "Director", None, None))
    return c


def guardian(path, rows):
    g = sqlite3.connect(path)
    g.execute("create table if not exists articles(tmdb_id integer, article_id text, kind text, headline text, url text, "
              "published text, trail text, byline text, stars real, fetched_at text, primary key (tmdb_id, article_id))")
    for i, (head, url) in enumerate(rows):
        g.execute("insert or replace into articles values (?,?,?,?,?,?,?,?,?,?)", (1, "a%d" % i, "news", head, url, "2026-11-19", "", None, None, ""))
    g.commit()
    g.close()


class TestPeopleAlerts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "follows.db")
        self.g = os.path.join(self.d, "guardian.db")
        guardian(self.g, [("Greta Gerwig on Barbie", "https://www.theguardian.com/film/a")])
        self.today = datetime.date(2026, 11, 20)

    def tearDown(self):
        shutil.rmtree(self.d)

    def look(self, f):
        return F.new_alerts("acct1", f, self.today, db=self.db, guardian_db=self.g)

    def test_first_look_is_silent_then_each_new_item_once(self):
        f = facts()
        F.follow("acct1", 1, f, db=self.db)
        self.assertEqual(self.look(f), [])                                  # Narnia and the article were already there
        f2 = facts([(13, "New Gerwig Film", "2027-06-01")])
        guardian(self.g, [("Greta Gerwig announces a new film", "https://www.theguardian.com/film/b")])
        new = self.look(f2)
        self.assertEqual(sorted(a["kind"] for a in new), ["person_article", "person_film"])
        film = next(a for a in new if a["kind"] == "person_film")
        self.assertEqual(film["tmdb_id"], 13)
        self.assertTrue(film["detail"].startswith("1|Greta Gerwig|Director|2027-06-01"))
        self.assertEqual(self.look(f2), [])                                 # never twice
        self.assertNotIn(10, [a["tmdb_id"] for a in new])                   # old films never alert

    def test_switch_off_and_move_on_sign_in(self):
        f = facts()
        F.follow("guestA", 1, f, db=self.db)
        F.new_alerts("guestA", f, self.today, db=self.db, guardian_db=self.g)
        F.set_notify("guestA", False, db=self.db)
        F.move_user("guestA", "acct1", db=self.db)
        self.assertFalse(F.notify_on("acct1", db=self.db))
        self.assertEqual(F.new_alerts("acct1", facts([(13, "X", "2027-01-01")]), self.today, db=self.db, guardian_db=self.g), [])
        F.set_notify("acct1", True, db=self.db)
        self.assertEqual([a["tmdb_id"] for a in F.new_alerts("acct1", facts([(13, "X", "2027-01-01")]), self.today, db=self.db,
                                                               guardian_db=self.g)], [13])   # seen-list moved: no flood

    def test_cleanup_only_old_guests(self):
        f = facts()
        old = "2026-01-01T00:00:00"
        for u in ("guestOld", "acctOld", "guestNew"):
            F.follow(u, 1, f, db=self.db)
        c = sqlite3.connect(self.db)
        c.execute("update follows set created_at = ? where user_key in ('guestOld', 'acctOld')", (old,))
        c.commit()
        c.close()
        F.touch("guestNew", db=self.db)
        r = F.cleanup(120, db=self.db, now=datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc))
        self.assertEqual(r, {"guests_removed": 1, "follows_removed": 1})
        left = {u for (u,) in sqlite3.connect(self.db).execute("select user_key from follows")}
        self.assertEqual(left, {"acctOld", "guestNew"})
        F.touch("guestOld2", db=self.db, at=old)                           # a recent visit keeps an old follow
        F.follow("guestOld2", 1, f, db=self.db)
        self.assertEqual(F.cleanup(120, db=self.db, now=datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc))["guests_removed"], 0)


class TestRenderAndEmail(unittest.TestCase):
    def test_article_text_and_link(self):
        import discovery as D
        t, u = D._person_text("person_article", "1|Greta Gerwig|https://www.theguardian.com/film/b|Greta Gerwig announces")
        self.assertEqual(t, "Greta Gerwig in the Guardian: Greta Gerwig announces")
        self.assertEqual(u, "https://www.theguardian.com/film/b")
        self.assertIsNone(D._person_text("person_article", "1|X|http://evil.example/|h")[1])
        src = open(D.__file__, encoding="utf-8").read()
        self.assertIn("_refresh_follow_alerts(user)", src)
        self.assertIn('os.environ.get("ALERTS_FOLLOWS") == "1"', src)

    def test_digest_lists_people_alerts_with_links(self):
        import emailer as E
        d = tempfile.mkdtemp()
        old = (E.DB, E.OUTBOX, dict(E._C))
        try:
            E.DB, E.OUTBOX = __import__("pathlib").Path(d) / "email.db", __import__("pathlib").Path(d) / "out"
            E._C.clear()
            E.conn().execute("insert into emails(user_key, email, verified, digest, unsub) values ('acct1', 'a@example.edu', 1, 1, 'tok')")
            E.conn().commit()
            alerts = {"alerts": [{"film": None, "kind": "person_article", "detail": "1|G|https://www.theguardian.com/x|h",
                                  "text": "G in the Guardian: h", "url": "https://www.theguardian.com/x"}]}
            r = E.digest_all(lambda u: alerts)
            self.assertEqual(r["emails"], 1)
            import email
            import email.policy
            raw = open(next((E.OUTBOX).glob("*.eml")), "rb").read()
            body = email.message_from_bytes(raw, policy=email.policy.default).get_content()
            self.assertIn("people you follow", body)
            self.assertIn("G in the Guardian: h (https://www.theguardian.com/x)", body)
            self.assertIn("sec=notifications", body)
        finally:
            E.DB, E.OUTBOX = old[0], old[1]
            E._C.clear()
            E._C.update(old[2])
            shutil.rmtree(d)


class TestPages(unittest.TestCase):
    def read(self, *p):
        return open(os.path.join(ROOT, *p), encoding="utf-8").read()

    def test_guest_note_alert_links_and_switch(self):
        film = self.read("web", "v8", "film.js")
        self.assertIn("You are not signed in, so your follows are kept in this browser only", film)
        app = self.read("web", "v8", "app.js")
        self.assertIn("x.url || (x.film?.id", app)
        self.assertIn("rel: x.url ? 'noopener noreferrer'", app)
        prof = self.read("web", "v8", "profile.js")
        self.assertIn("/api/v8/follows/notify", prof)
        n = self.read("nightly.sh")
        self.assertIn("follows.cleanup()", n)
        self.assertIn("ALERTS_FOLLOWS=$(grep -c '^FOLLOW_PEOPLE=1$' site.env", n)
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(ROOT, "nightly.sh")]).returncode, 0)

    def test_routes_404_while_off(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception:  # noqa: BLE001
            self.skipTest("fastapi not installed")
        old = F.FOLLOW_PEOPLE
        try:
            F.FOLLOW_PEOPLE = False
            app = FastAPI()
            F.install(app, lambda r: None, lambda r: "u1", facts_path="/nonexistent.db")
            c = TestClient(app)
            self.assertEqual(c.get("/api/v8/follows/notify").status_code, 404)
            self.assertEqual(c.post("/api/v8/follows/notify", json={"on": False}).status_code, 404)
            F.FOLLOW_PEOPLE = True
            d = tempfile.mkdtemp()
            oldDB = F.DB
            F.DB = __import__("pathlib").Path(d) / "f.db"
            try:
                self.assertEqual(c.post("/api/v8/follows/notify", json={"on": False}).json(), {"on": False})
                self.assertEqual(c.get("/api/v8/follows/notify").json()["on"], False)
            finally:
                F.DB = oldDB
                shutil.rmtree(d)
        finally:
            F.FOLLOW_PEOPLE = old


if __name__ == "__main__":
    unittest.main()
