"""v8.1 round 47 (16 Nov 2026): Guardian articles for one film (web/guardian_live.py) - cache first, limits,
the shared call ledger, the route, the film page hook - and the review relink proposal. No network."""
import datetime
import importlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import guardian_live as GL  # noqa: E402

KEY = "SECRET-KEY-123"
T0 = datetime.datetime(2026, 11, 16, 15, 0, tzinfo=datetime.timezone.utc)


class Reply:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._b = status, headers, body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def item(i, head, trail="", review=False, stars=None):
    return {"id": f"film/2026/nov/{i}", "webUrl": f"https://www.theguardian.com/film/2026/nov/{i}", "webPublicationDate": f"2026-11-{i:02d}T10:00:00Z",
            "fields": {"headline": head, "trailText": trail, "byline": "Peter Bradshaw", "starRating": stars},
            "tags": [{"id": "tone/reviews"}] if review else [{"id": "tone/news"}]}


def opener(results, remaining=300, status=200, seen=None):
    def go(req, timeout=0):
        if seen is not None:
            seen.append(req.full_url)
        h = {"X-RateLimit-Remaining-day": str(remaining)}
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", h, io.BytesIO(b""))
        return Reply(200, h, json.dumps({"response": {"results": results}}).encode())
    return go


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        f = self.facts = sqlite3.connect(os.path.join(self.tmp, "facts.db"))
        f.execute("create table films(tmdb_id integer primary key, title text, year integer, release_date text, us_release_date text)")
        f.executemany("insert into films values (?,?,?,?,?)", [
            (1, "Night Harbor", 2026, "2026-11-06", None),           # new film, nothing stored
            (2, "Heat", 1995, "1995-12-15", None),                   # old film with a stored review
            (3, "Quiet Meridian", 2019, "2019-04-01", None),         # old film, nothing stored
            (4, "Heat", 2013, "2013-02-01", None)])
        f.execute("create table critic_reviews(review_key text, source text, tmdb_id integer, headline text, url text, published text, "
                  "summary text, critic text, stars real)")
        f.execute("insert into critic_reviews values ('guardian:film/1995/heat', 'guardian', 2, 'Heat review', "
                  "'https://www.theguardian.com/film/1995/heat', '1996-02-01', 'Pacino and De Niro', 'Derek Malcolm', 5)")
        f.commit()
        self.c = GL.connect(os.path.join(self.tmp, "live.db"))
        self.probe = os.path.join(self.tmp, "probe.jsonl")

    def tearDown(self):
        self.c.close()
        self.facts.close()
        shutil.rmtree(self.tmp)

    def look(self, fid, results=(), user="u1", at=T0, seen=None, **kw):
        kw.setdefault("enabled", True)
        return GL.lookup(self.facts, fid, user, c=self.c, key=KEY, opener=kw.pop("op", None) or opener(list(results), seen=seen),
                         at=at, probe_log=self.probe, **kw)


class TestLookup(Base):
    def test_a_film_with_a_stored_review_costs_no_call(self):
        seen = []
        r = self.look(2, seen=seen)
        self.assertEqual((r["source"], seen), ("stored", []))
        self.assertEqual(r["articles"][0]["headline"], "Heat review")
        self.assertEqual(r["credit"], "From the Guardian")
        bad = GL.merge([{"url": "https://evil.example/x", "id": "1"}, {"url": "http://www.theguardian.com/x", "id": "2"},
                        {"url": "https://www.theguardian.com.evil.example/x", "id": "3"}, {"url": "https://www.theguardian.com/film/ok", "id": "4"}])
        self.assertEqual([x["id"] for x in bad], ["4"])                  # only https Guardian links are ever served

    def test_first_visit_calls_once_keeps_only_matching_results_and_stores_them(self):
        seen = []
        res = [item(7, "Night Harbor review – a tense thriller", "Moody and taut", review=True, stars=4),
               item(8, "Box office: Night Harbor opens strongly"), item(9, "The week in film", "Nothing about it"),
               item(10, "Night Harbour is not the same words")]
        r = self.look(1, res, seen=seen)
        self.assertEqual(r["source"], "live")
        self.assertEqual(len(seen), 1)
        self.assertIn("q=%22Night+Harbor%22", seen[0])
        self.assertIn("section=film", seen[0])
        self.assertIn("from-date=2025-01-01", seen[0])
        self.assertEqual([a["headline"] for a in r["articles"]], ["Box office: Night Harbor opens strongly", "Night Harbor review – a tense thriller"])
        self.assertEqual([a["kind"] for a in r["articles"]], ["news", "review"])
        self.assertEqual(r["articles"][1]["stars"], 4.0)
        seen2 = []
        r2 = self.look(1, seen=seen2, at=T0 + datetime.timedelta(hours=2))
        self.assertEqual((r2["source"], seen2, len(r2["articles"])), ("stored", [], 2))   # the second visit is free

    def test_new_films_are_refreshed_at_most_once_a_day_old_films_never(self):
        self.look(1, [item(7, "Night Harbor review", review=True)])
        seen = []
        self.look(1, seen=seen, at=T0 + datetime.timedelta(hours=20))
        self.assertEqual(seen, [])
        self.look(1, [item(9, "Night Harbor wins a prize")], seen=seen, at=T0 + datetime.timedelta(hours=25))
        self.assertEqual(len(seen), 1)
        seen = []
        self.look(3, [], seen=seen)
        self.look(3, [], seen=seen, at=T0 + datetime.timedelta(days=40))
        self.assertEqual(len(seen), 1)                                   # nothing found is stored too: no second call
        self.assertEqual(GL.due(self.c, 1, datetime.date(2026, 11, 6), T0 + datetime.timedelta(days=60)), False)

    def test_off_no_key_unknown_film(self):
        seen = []
        self.assertEqual(self.look(1, [item(7, "Night Harbor")], seen=seen, enabled=False)["reason"], "live lookups are switched off")
        self.assertEqual(seen, [])
        r = GL.lookup(self.facts, 1, "u", c=self.c, key="", enabled=True, opener=opener([], seen=seen), at=T0, probe_log=self.probe)
        os.environ.pop("GUARDIAN_API_KEY", None)
        self.assertEqual(r["reason"], "no Guardian key")
        self.assertEqual(self.look(999)["reason"], "unknown film")
        self.assertEqual(seen, [])

    def test_failures_answer_from_the_store_and_a_429_pauses_lookups(self):
        r = self.look(1, op=opener([], status=429, remaining=0))
        self.assertEqual(r["source"], "stored")
        self.assertIn("429", r["reason"])
        seen = []
        r = self.look(3, [item(1, "Quiet Meridian")], seen=seen, at=T0 + datetime.timedelta(minutes=30))
        self.assertIn("too many requests", r["reason"])
        self.assertEqual(seen, [])

        def dead(req, timeout=0):
            raise TimeoutError()
        r = self.look(3, op=dead, at=T0 + datetime.timedelta(hours=2))
        self.assertEqual((r["source"], r["articles"]), ("stored", []))


class TestBudget(Base):
    def test_daily_cap_per_person_cap_and_floor(self):
        for i in range(GL.LIVE_DAILY_CAP):
            GL.record(self.c, "live", 200, None, GL.iso(T0 - datetime.timedelta(minutes=i)))
        self.assertEqual(GL.why_not(self.c, "x", T0, self.probe), "daily live budget used")
        self.c.execute("delete from calls")
        for i in range(GL.USER_HOURLY_CAP):
            self.c.execute("insert into user_calls values (?,?)", ("h1", GL.iso(T0 - datetime.timedelta(minutes=i))))
        self.assertIn("this person", GL.why_not(self.c, "h1", T0, self.probe))
        self.assertIsNone(GL.why_not(self.c, "h2", T0, self.probe))
        GL.record(self.c, "probe", 200, 7, GL.iso(T0 - datetime.timedelta(minutes=30)))
        GL.record(self.c, "live", 200, None, GL.iso(T0 - datetime.timedelta(minutes=10)))
        GL.record(self.c, "live", 200, None, GL.iso(T0 - datetime.timedelta(minutes=5)))
        self.assertEqual(GL.calls_left(self.c, T0, self.probe), 5)
        self.assertEqual(GL.why_not(self.c, "h2", T0, self.probe), "only 5 Guardian calls left today")

    def test_the_probe_log_counts_and_old_numbers_are_not_trusted(self):
        with open(self.probe, "w") as fh:
            fh.write(json.dumps({"at": GL.iso(T0 - datetime.timedelta(hours=1)), "remaining_day": 4}) + "\n")
        self.assertEqual(GL.calls_left(self.c, T0, self.probe), 4)
        self.assertIn("only 4", GL.why_not(self.c, "h", T0, self.probe))
        self.assertIsNone(GL.calls_left(self.c, T0 + datetime.timedelta(hours=8), self.probe))   # stale: unknown, allowed
        self.assertIsNone(GL.why_not(self.c, "h", T0 + datetime.timedelta(hours=8), self.probe))

    def test_the_key_is_never_stored_and_people_are_hashed(self):
        self.look(1, [item(7, "Night Harbor review", review=True)], user="acct42")
        dump = "\n".join(self.c.iterdump())
        self.assertNotIn(KEY, dump)
        self.assertNotIn("acct42", dump)
        self.assertEqual(self.c.execute("select who, status, remaining from calls").fetchall(), [("live", 200, 300)])


class TestRouteAndPage(Base):
    def test_route_is_404_while_off_and_answers_while_on(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest("fastapi test client not available: %s" % e)
        self.facts.close()
        os.environ["GUARDIAN_LIVE_DB"] = os.path.join(self.tmp, "route.db")
        try:
            for on, want in (("0", 404), ("1", 200)):
                os.environ["GUARDIAN_LIVE"] = on
                importlib.reload(GL)
                app = FastAPI()
                GL.install(app, lambda r: None, lambda r: "u1", facts_path=os.path.join(self.tmp, "facts.db"))
                r = TestClient(app).get("/api/v8/film/2/press")
                self.assertEqual(r.status_code, want)
                if on == "1":
                    self.assertEqual(r.json()["source"], "stored")
                    self.assertEqual(r.headers.get("cache-control"), "no-store")
                    self.assertEqual(TestClient(app).get("/api/v8/film/0/press").status_code, 400)
        finally:
            for k in ("GUARDIAN_LIVE", "GUARDIAN_LIVE_DB"):
                os.environ.pop(k, None)
            importlib.reload(GL)
            self.facts = sqlite3.connect(os.path.join(self.tmp, "facts.db"))

    def test_film_page_hook_uses_text_and_guardian_links_only(self):
        src = open(os.path.join(ROOT, "web", "v8", "film.js"), encoding="utf-8").read()
        i = src.index("/api/v8/film/${d.id}/press")
        block = src[i - 200:src.index("}).catch(() => {});", i)]
        self.assertNotIn("html:", block)
        self.assertNotIn("innerHTML", block)
        self.assertIn("theguardian\\.com", block)
        self.assertIn(".catch(() => {})", src[i:i + 2000])                # 404 while off: nothing shown, no error
        srv = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
        self.assertIn("guardian_live.install(app, _gate, user_of)", srv)


class TestRelink(unittest.TestCase):
    def test_candidates_and_proposals(self):
        try:
            import v81_review_relink as RL
        except ImportError as e:
            self.skipTest(str(e))
        idx = {}
        for fid, t, y, v in [(1, "Heat", 1995, 9000), (2, "Heat", 2013, 50), (3, "Brief Encounter", 1945, 900), (4, "Amelie", 2001, 8000),
                             (5, "The Lobster", 2015, 7000), (6, "Solaris", 1972, 900), (7, "Solaris", 2002, 2000)]:
            idx.setdefault(RL.norm(t), []).append((fid, y, v))
        self.assertEqual(RL.guesses("DVD: Brief Encounter", None)[0], "brief encounter")
        self.assertIn("amelie", RL.guesses("Amélie – review", None))
        self.assertIn("the lobster", RL.guesses("Lobster, The", None))
        self.assertEqual(RL.propose(idx, "Brief Encounter", "Brief Encounter review – still devastating", 2012), (3, "only film with the title"))
        self.assertEqual(RL.propose(idx, "Heat", "Heat review", 1996), (1, "title + year window"))
        self.assertEqual(RL.propose(idx, "Solaris", "Solaris re-release review", 2011)[0], None)        # two films, no year match: left alone
        self.assertEqual(RL.propose(idx, "Solaris", "Solaris review", 2003), (7, "title + year window"))
        self.assertEqual(RL.propose(idx, "The week in film", "The week in film", 2010), (None, None))


if __name__ == "__main__":
    unittest.main()
