"""v8.1 round 55 (20 Nov 2026): news next to search results, a time-limited aspects step, email settings from
keys.env for the site process, the nightly Trakt sync that only adds what is missing, the event-log baseline report,
and the Pick Tonight notes (the Node test is run from here when node is installed). No network, no real email."""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), os.path.join(ROOT, "tools"), ROOT]
os.environ["EMAIL_SANDBOX"] = "1"
os.environ["EMAIL_FILES"] = "0"


def facts_db():
    c = sqlite3.connect(":memory:")
    c.executescript("""
      create table films(tmdb_id integer primary key, title text, year integer, vote_count integer);
      create table critic_reviews(review_key text primary key, source text, tmdb_id integer, title_guess text, headline text,
        critic text, stars real, published text, url text, summary text, body text, match real, fetched_at text);
      create table film_press(tmdb_id integer, article_id text, pub_date text, headline text, trail text, section text,
        primary key (tmdb_id, article_id));""")
    c.executemany("insert into films values (?,?,?,?)", [(1, "Home", 2015, 10), (2, "Three Colors: Blue", 1993, 5)])
    c.executemany("insert into critic_reviews values (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("g1", "guardian", 2, "", "Three Colours: Blue review", "Peter B", 5, "2023-01-02", "https://www.theguardian.com/film/blue",
         "<b>A</b> masterpiece", "", 1.0, ""),
        ("n1", "nyt", 2, "", "Blue, reviewed", "A. O.", None, "1993-12-04", "https://www.nytimes.com/1993/12/04/blue.html", "Fine", "", 1.0, ""),
        ("x1", "guardian", 2, "", "Weak match", "", None, "2024-01-01", "https://www.theguardian.com/film/other", "", "", 0.5, ""),
        ("e1", "guardian", 2, "", "Bad link", "", None, "2024-02-01", "http://example.com/x", "", "", 1.0, "")])
    c.executemany("insert into film_press values (?,?,?,?,?,?)", [
        (1, "film/2015/mar/01/home-dreamworks", "2015-03-01", "Home: DreamWorks aliens land", "The new film Home", "film"),
        (1, "uk-news/2015/mar/02/house-prices", "2015-03-02", "House prices at home rise", "Property", "money"),
        (1, "film/2015/mar/03/homeward", "2015-03-03", "Homeward bound", "A different film", "film")])
    return c


class TestNewsSearch(unittest.TestCase):
    def test_reviews_and_press_only_matching_links(self):
        import news_search as N
        arts = N.for_films(facts_db(), [2, 1], live_db="/nonexistent.db")
        urls = [a["url"] for a in arts]
        self.assertIn("https://www.theguardian.com/film/blue", urls)
        self.assertIn("https://www.nytimes.com/1993/12/04/blue.html", urls)
        self.assertNotIn("https://www.theguardian.com/film/other", urls)        # match < 1
        self.assertNotIn("http://example.com/x", urls)                          # not an allowed site
        self.assertIn("https://www.theguardian.com/film/2015/mar/01/home-dreamworks", urls)
        self.assertFalse(any("house-prices" in u or "homeward" in u for u in urls))   # "home" inside another word / not the title
        g = next(a for a in arts if a["url"].endswith("/film/blue"))
        self.assertEqual(g["summary"], "A masterpiece")
        self.assertEqual(g["film"], "Three Colors: Blue (1993)")
        self.assertEqual(g["outlet"], "The Guardian")
        self.assertEqual(arts[0]["film_id"], 2)                                 # the search's first film first

    def test_bad_ids_and_caps(self):
        import news_search as N
        self.assertEqual(N.for_films(facts_db(), ["x", None, -3, 999], live_db="/nonexistent.db"), [])
        self.assertLessEqual(len(N.for_films(facts_db(), [2, 1, 2, 1], live_db="/nonexistent.db")), N.TOTAL)

    def test_route_is_404_while_off(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception:  # noqa: BLE001
            self.skipTest("fastapi test client not installed")
        import news_search as N
        old = N.SEARCH_NEWS
        try:
            for on, code in ((False, 404), (True, 200)):
                N.SEARCH_NEWS = on
                app = FastAPI()
                N.install(app, lambda r: None, facts_path="/nonexistent/facts.db")
                r = TestClient(app).get("/api/v8/news?ids=1")
                self.assertEqual(r.status_code, code)
        finally:
            N.SEARCH_NEWS = old

    def test_wired_into_server_and_page(self):
        s = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
        self.assertIn("news_search.install(app, _gate)", s)
        j = open(os.path.join(ROOT, "web", "v8", "app.js"), encoding="utf-8").read()
        self.assertIn("/api/v8/news?ids=", j)
        self.assertIn("rel: 'noopener noreferrer'", j)


class TestAspectsTimed(unittest.TestCase):
    def setUp(self):
        import reviews_analyze as R
        self.R = R
        c = sqlite3.connect(":memory:")
        c.executescript("""
          create table films(tmdb_id integer primary key, vote_count integer);
          create table reviews(review_id text, tmdb_id integer, content text, created_at text);
          create table sections(sec_id integer, tmdb_id integer, kind text, position integer, text text);
          create table review_done(review_id text primary key, version text, n integer);
          create table review_aspects(review_id text, tmdb_id integer, source_type text, aspect text, sentiment integer,
            quote text, model text, version text);
          create table film_aspects(tmdb_id integer, source_type text, aspect text, pos integer, neg integer, mixed integer,
            n_reviews integer, quotes_json text, computed_at text, primary key (tmdb_id, source_type, aspect));
          create table meta(key text primary key, value text);""")
        for f in range(1, 6):
            c.execute("insert into films values (?,?)", (f, 100 - f))
            for k in range(3):
                c.execute("insert into reviews values (?,?,?,?)", (f"r{f}{k}", f, "The acting was superb throughout. " * 10, f"2020-0{k + 1}"))
        self.c = c

    def test_iter_matches_list(self):
        a = self.R.work_items(self.c, 10, ["audience", "critic"], 25, 3)
        b = list(self.R.iter_work_items(self.c, 10, ["audience", "critic"], 25, 3))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 15)

    def test_stops_at_time_limit_and_resumes(self):
        R = self.R
        t = [0.0]

        def clock():
            t[0] += 7.0            # every look at the clock = 7 s
            return t[0]
        reply = {"aspects": [{"aspect": "acting", "sentiment": "positive", "quote": "The acting was superb"}]}
        old_meta = getattr(R.F, "set_meta", None)
        R.F.set_meta = lambda conn, k, v: None
        old_usable = R.usable
        R.usable = lambda: None
        try:
            a = argparse.Namespace(minutes=1, model="m", ollama="", kinds="audience", per_film=25, critic_passages=3)
            r1 = R.run_timed(self.c, a, 10, None, clock=clock, caller=lambda *x: reply)
            self.assertTrue(r1["timed_out"])
            self.assertGreater(r1["done"], 0)
            self.assertLess(r1["done"], 15)
            r2 = R.run_timed(self.c, argparse.Namespace(minutes=100, model="m", ollama="", kinds="audience", per_film=25,
                                                        critic_passages=3), 10, None, clock=clock, caller=lambda *x: reply)
            self.assertFalse(r2["timed_out"])
            self.assertEqual(r1["done"] + r2["done"], 15)                    # nothing twice, nothing lost
            self.assertEqual(self.c.execute("select count(*) from review_done").fetchone()[0], 15)
        finally:
            R.usable = old_usable
            if old_meta:
                R.F.set_meta = old_meta

    def test_nightly_uses_the_limit(self):
        n = open(os.path.join(ROOT, "nightly.sh"), encoding="utf-8").read()
        self.assertIn("reviews_analyze.py --kinds audience,critic --films 40000 --minutes 50", n)
        self.assertIn("v81_event_report.py", n)
        self.assertIn("TRAKT_AUTOSYNC=1", n)
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(ROOT, "nightly.sh")]).returncode, 0)


class TestEmailSettings(unittest.TestCase):
    def test_reads_missing_names_only(self):
        import emailer
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "keys.env")
            with open(p, "w") as f:
                f.write("export EMAIL_SANDBOX=0\nEMAIL_PROVIDER='brevo'\nEMAIL_FROM=\"Cinemack <you@example.edu>\"\n"
                        "SITE_URL=http://x:1 # comment\nOTHER_SECRET=zzz\nBREVO_API_KEY=k1\n")
            env = {"EMAIL_PROVIDER": "smtp"}
            got = emailer._settings_from_files([p], env=env)
            self.assertEqual(env["EMAIL_SANDBOX"], "0")
            self.assertEqual(env["EMAIL_PROVIDER"], "smtp")                     # the environment wins
            self.assertEqual(env["EMAIL_FROM"], "Cinemack <you@example.edu>")
            self.assertEqual(env["SITE_URL"], "http://x:1")
            self.assertNotIn("OTHER_SECRET", env)
            self.assertNotIn("OTHER_SECRET", got)
        finally:
            shutil.rmtree(d)

    def test_tests_stay_in_the_sandbox(self):
        import emailer
        self.assertTrue(emailer.SANDBOX)


class FakeT:
    def __init__(self, data):
        self.data, self._lock, self.c = data, threading.Lock(), sqlite3.connect(":memory:", check_same_thread=False)
        self.c.execute("create table tokens(user_key text, last_sync text)")
        self.c.execute("insert into tokens values ('acct1', null)")
        self.imported = 0

    def conn(self):
        return self.c

    def _get_all(self, user, path):
        return self.data.get(path, [])

    def _rows_history(self, items):
        return items

    def _import_by_id(self, user, rows):
        self.imported += len(rows)
        return {"added": len(rows)}


class FakeSocial:
    def __init__(self, have):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("create table reviews(user_key text, tmdb_id integer, rating real, text text)")
        for t in have:
            self.c.execute("insert into reviews values ('acct1', ?, 4, 'my own words')", (t,))
        self.saved = []

    def conn(self):
        return self.c

    def save_review(self, user, tid, rating, text):
        self.saved.append((tid, rating))
        self.c.execute("insert or replace into reviews values (?,?,?,?)", (user, tid, rating, text))


class FakeFilmInfo:
    def __init__(self, choices):
        self.ch, self.added = dict(choices), []

    def user_choices(self, user, ids):
        return {i: self.ch[i] for i in ids if i in self.ch}

    def choose(self, user, tid, title, choice):
        self.added.append(tid)
        self.ch[tid] = choice


class TestTraktMissing(unittest.TestCase):
    def test_only_adds_what_is_missing_and_never_twice(self):
        import trakt_missing as TM
        data = {"/sync/history/movies": [{"x": 1}],
                "/sync/ratings/movies": [{"movie": {"ids": {"tmdb": 10}}, "rating": 8}, {"movie": {"ids": {"tmdb": 11}}, "rating": 6}],
                "/sync/watchlist/movies": [{"movie": {"ids": {"tmdb": 20}, "title": "A"}}, {"movie": {"ids": {"tmdb": 21}, "title": "B"}}]}
        T, S, FI = FakeT(data), FakeSocial(have=[10]), FakeFilmInfo({21: "liked"})
        r = TM.sync_missing(T, "acct1", social=S, film_info=FI)
        self.assertEqual(r["ratings_added"], 1)             # 10 already had the person's own review
        self.assertEqual(S.saved, [(11, 3.0)])
        self.assertEqual(S.c.execute("select text from reviews where tmdb_id = 10").fetchone()[0], "my own words")
        self.assertEqual(FI.added, [20])                    # 21 already has a choice here
        # the person deletes rating 11 and takes 20 off the list in Cinemack; the next night must not undo that
        S.c.execute("delete from reviews where tmdb_id = 11")
        del FI.ch[20]
        r2 = TM.sync_missing(T, "acct1", social=S, film_info=FI)
        self.assertEqual((r2["ratings_added"], r2["watchlist_added"]), (0, 0))
        self.assertIsNotNone(T.c.execute("select last_sync from tokens").fetchone()[0])

    def test_autosync_uses_it(self):
        s = open(os.path.join(ROOT, "tools", "v81_trakt_autosync.py"), encoding="utf-8").read()
        self.assertIn("trakt_missing.sync_missing(T, u)", s)


class TestEventReport(unittest.TestCase):
    def test_baseline_counts(self):
        import v81_event_report as E
        c = sqlite3.connect(":memory:")
        c.execute("""create table rec_event(event_id text, occurred_at text, subject_key text, session_key text, media_id text,
                     placement text, event_type text, ranking_version text, exposure_id text, position integer, context_key text,
                     consent_scope text, explicit_rating real)""")
        rows = []
        for v in range(3):
            for p in range(4):
                rows.append((f"i{v}{p}", "2026-11-21T10:00", None, f"s{v}", f"movie:{p}", "home", "impression", "v", f"e{v}", p))
        rows.append(("c1", "2026-11-21T10:01", None, "s0", "movie:0", "home", "click", "v", "e0", 0))
        rows.append(("w1", "2026-11-21T10:02", None, "s1", "movie:2", "detail", "watchlist_add", "v", None, None))
        rows.append(("old", "2026-11-01T10:00", None, "s9", "movie:0", "home", "click", "v", "e0", 0))   # before the window
        c.executemany("insert into rec_event (event_id, occurred_at, subject_key, session_key, media_id, placement, event_type, "
                      "ranking_version, exposure_id, position) values (?,?,?,?,?,?,?,?,?,?)", rows)
        r = E.report(c)
        self.assertEqual((r["exposures"], r["visits"], r["titles_shown"]), (3, 3, 12))
        self.assertAlmostEqual(r["click_rate"], 1 / 3, places=3)
        self.assertAlmostEqual(r["save_rate"], 1 / 12, places=3)
        self.assertEqual(r["position_click_rate"][0], round(1 / 3, 4))
        self.assertFalse(r["enough_data"])
        self.assertIn("NOT ENOUGH DATA", E.text(r))


class TestPickNotes(unittest.TestCase):
    def test_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        r = subprocess.run([node, os.path.join(ROOT, "tests", "js", "test_v81_r55_pick.mjs")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
