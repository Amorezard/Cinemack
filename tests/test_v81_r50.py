"""v8.1 round 50 (17 Nov 2026): anonymous recommendation events from the page (web/events_api.py, web/v8/events.js)
and the follow pieces of the pages (film page list, Home row). No network; a temporary events database."""
import importlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
import rec_events as RE  # noqa: E402
import events_api as EA  # noqa: E402

V8 = os.path.join(ROOT, "web", "v8")
EX = "0123456789abcdef01234567"


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return f.read()


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "ev.db")
        EA._counts.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("select subject_key, media_id, placement, event_type, position, exposure_id, consent_scope, "
                             "ranking_version, context_key from rec_event order by rowid").fetchall()
        finally:
            c.close()

    def test_good_events_are_stored_anonymously(self):
        out = EA.store("tab-12345678", [
            {"media_id": "movie:603", "placement": "home", "type": "impression", "position": 0, "exposure": EX},
            {"media_id": "tv:1399", "placement": "detail", "type": "click"},
            {"media_id": "movie:603", "placement": "home", "type": "watchlist_add"}], db=self.db, enabled=True)
        self.assertEqual(out, {"stored": 3, "skipped": 0})
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r[0] is None for r in rows))                  # never linked to a person
        self.assertTrue(all(r[6] == "anonymous" and r[7] == "v8.0" and r[8] is None for r in rows))
        self.assertEqual(rows[0][1:6], ("movie:603", "home", "impression", 0, EX))

    def test_bad_events_are_skipped_never_stored(self):
        bad = [{"media_id": "movie:0", "placement": "home", "type": "click"},
               {"media_id": "movie:603; drop table", "placement": "home", "type": "click"},
               {"media_id": "movie:603", "placement": "chatroom", "type": "click"},
               {"media_id": "movie:603", "placement": "home", "type": "watch_completed"},     # not one the page may send
               {"media_id": "movie:603", "placement": "home", "type": "click", "position": -1},
               {"media_id": "movie:603", "placement": "home", "type": "click", "position": True},
               {"media_id": "movie:603", "placement": "home", "type": "click", "exposure": "xyz"},
               "not an object", None]
        out = EA.store("tab-12345678", bad, db=self.db, enabled=True)
        self.assertEqual(out["stored"], 0)
        self.assertFalse(os.path.exists(self.db) and self.rows())

    def test_session_must_look_like_the_pages_random_id(self):
        for s in (None, "", "short", "x" * 65, "has space 123", "user@example.com"):
            out = EA.store(s, [{"media_id": "movie:1", "placement": "home", "type": "click"}], db=self.db, enabled=True)
            self.assertEqual(out["stored"], 0, s)
            self.assertIn("error", out)

    def test_batch_and_hourly_caps(self):
        ev = {"media_id": "movie:7", "placement": "home", "type": "impression"}
        out = EA.store("tab-aaaaaaaa", [ev] * 100, db=self.db, enabled=True, now=0)
        self.assertEqual(out["stored"], EA.MAX_BATCH)
        old, EA.MAX_PER_HOUR = EA.MAX_PER_HOUR, 50
        try:
            EA._counts.clear()
            self.assertEqual(EA.store("tab-bbbbbbbb", [ev] * 40, db=self.db, enabled=True, now=0)["stored"], 40)
            self.assertEqual(EA.store("tab-bbbbbbbb", [ev] * 40, db=self.db, enabled=True, now=10)["stored"], 10)
            self.assertEqual(EA.store("tab-bbbbbbbb", [ev] * 40, db=self.db, enabled=True, now=20)["stored"], 0)
            self.assertEqual(EA.store("tab-bbbbbbbb", [ev] * 40, db=self.db, enabled=True, now=3600)["stored"], 40)
            self.assertEqual(EA.store("tab-cccccccc", [ev] * 5, db=self.db, enabled=True, now=20)["stored"], 5)
        finally:
            EA.MAX_PER_HOUR = old

    def test_nothing_is_stored_while_the_switch_is_off(self):
        out = EA.store("tab-12345678", [{"media_id": "movie:1", "placement": "home", "type": "click"}], db=self.db, enabled=False)
        self.assertEqual(out["stored"], 0)
        self.assertFalse(os.path.exists(self.db))

    def test_route_404_while_off_then_stores_while_on(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest("fastapi test client not available: %s" % e)
        body = {"session": "tab-12345678", "events": [{"media_id": "movie:1", "placement": "home", "type": "click"}]}
        os.environ["REC_EVENTS_DB"] = self.db
        try:
            os.environ["REC_EVENTS"] = "0"
            importlib.reload(RE)
            importlib.reload(EA)
            app = FastAPI()
            EA.install(app, lambda r: None)
            self.assertEqual(TestClient(app).post("/api/v8/events", json=body).status_code, 404)
            self.assertFalse(os.path.exists(self.db))
            os.environ["REC_EVENTS"] = "1"
            importlib.reload(RE)
            importlib.reload(EA)
            app = FastAPI()
            EA.install(app, lambda r: None)
            cl = TestClient(app)
            self.assertEqual(cl.post("/api/v8/events", json=body).json(), {"stored": 1, "skipped": 0})
            self.assertEqual(cl.post("/api/v8/events", content=b"nope", headers={"Content-Type": "application/json"}).status_code, 400)
            self.assertEqual(cl.post("/api/v8/events", json=[1, 2]).status_code, 400)
            self.assertEqual(cl.post("/api/v8/events", json={"session": "x", "events": []}).status_code, 400)
            app = FastAPI()
            EA.install(app, lambda r: EA.JSONResponse({"error": "code"}, status_code=401))
            self.assertEqual(TestClient(app).post("/api/v8/events", json=body).status_code, 401)
        finally:
            os.environ.pop("REC_EVENTS", None)
            os.environ.pop("REC_EVENTS_DB", None)
            importlib.reload(RE)
            importlib.reload(EA)

    def test_server_never_passes_a_person(self):
        src = read(ROOT, "web", "events_api.py")
        self.assertIn("RE.record(None, session", src)
        self.assertIn('consent="anonymous"', src)
        self.assertNotIn("user_of", src.split("def install")[1])


class TestPages(unittest.TestCase):
    def test_events_module_sends_only_ids_places_kinds_and_positions(self):
        src = re.sub(r"//[^\n]*", "", read(V8, "events.js"))
        sent = re.search(r"queue\.push\(\{([^}]*)\}\)", src).group(1)
        self.assertEqual(sorted(k.split(":")[0].strip() for k in sent.split(",")), ["exposure", "media_id", "placement", "position", "type"])
        for bad in ("textContent", "innerText", ".value", "mb.user", "innerHTML", "X-MB-User", "document.cookie"):
            self.assertNotIn(bad, src, bad)
        self.assertIn("sessionStorage", src)
        self.assertIn("r.status === 404", src)                           # the first 404 stops sending

    def test_rows_and_choices_are_hooked(self):
        core = read(V8, "core.js")
        self.assertRegex(core, r"(?m)^import \{ watchRow \} from './events\.js';")
        self.assertIn("watchRow(track);", core)
        self.assertIn("emit('cm:choice', { id: f.id, choice, prev, film: f });", core)
        self.assertNotIn("import", read(V8, "events.js").split("\n", 8)[-1][:0])  # events.js imports nothing
        self.assertNotRegex(read(V8, "events.js"), r"(?m)^import ")

    def test_follow_pieces_draw_only_after_the_route_answered(self):
        film = read(V8, "film.js")
        i = film.index("api(`/api/v8/film/${d.id}/people`)")
        self.assertLess(i, film.index("'people-follow'"))
        self.assertIn("post('/api/v8/follows'", film)
        home = read(V8, "home.js")
        self.assertIn("try { r = await api('/api/v8/follows'); } catch { return; }", home)
        self.assertIn("followRow(view);", home)
        self.assertIn("theguardian\\.com", home)                          # only Guardian links are drawn
        css = read(V8, "app.css")
        for cls in (".follow-list", ".fp ", ".follow-feed", ".ff "):
            self.assertIn(cls, css)
        self.assertNotIn(".people-follow {", css)                         # the old .follow chip row is not reused

    def test_server_installs_the_route(self):
        self.assertIn("events_api.install(app, _gate)", read(ROOT, "web", "server.py"))


if __name__ == "__main__":
    unittest.main()
