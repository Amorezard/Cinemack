"""Round 25: tools/fetch_tvmaze.py against a fake TVmaze on localhost, then web/tvmaze.py reading the result."""
import http.server
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
SHOW = {"id": 169, "name": "Breaking Bad", "status": "Ended", "premiered": "2008-01-20", "ended": "2013-09-29",
        "network": {"name": "AMC", "country": {"code": "US", "timezone": "America/New_York"}}, "webChannel": None,
        "schedule": {"time": "22:00", "days": ["Sunday"]}, "runtime": 60, "rating": {"average": 9.2},
        "image": {"medium": "http://x/bb.jpg"}, "summary": "<p><b>Breaking Bad</b> follows a chemistry teacher &amp; more.</p>",
        "url": "https://www.tvmaze.com/shows/169/breaking-bad", "externals": {"imdb": "tt0903747"}, "updated": 100}
NEW = dict(SHOW, id=500, name="New Show", status="Running", network=None, externals={"imdb": "tt9"},
           webChannel={"name": "Netflix", "country": None}, url="https://www.tvmaze.com/shows/500/new-show")
EPS = [{"id": 1, "season": 5, "number": 16, "name": "Felina", "type": "regular", "airdate": "2013-09-29", "airtime": "21:00",
        "airstamp": "2013-09-30T01:00:00+00:00", "runtime": 75, "rating": {"average": 9.6}, "image": None,
        "summary": "<p>All bad things must come to an end.</p>", "url": "https://www.tvmaze.com/episodes/1"},
       {"id": 2, "season": 0, "number": None, "name": "Special", "type": "insignificant_special", "airdate": "",
        "airtime": "", "airstamp": None, "runtime": 10, "rating": {}, "image": None, "summary": None, "url": "u"}]
FULL = [{"id": 900, "season": 1, "number": 2, "name": "Two", "airdate": "2026-11-20", "airtime": "", "airstamp": "2026-11-20T08:00:00+00:00",
         "runtime": 50, "_embedded": {"show": NEW}},
        {"id": 901, "season": 3, "number": 1, "name": "Not ours", "airdate": "2026-11-21", "_embedded": {"show": dict(SHOW, id=777)}}]
ROUTES = {"/lookup/shows?imdb=tt0903747": SHOW, "/lookup/shows?imdb=tt9": NEW, "/shows/169/episodes?specials=1": EPS,
          "/shows/500/episodes?specials=1": [], "/schedule/full": FULL, "/updates/shows?since=day": {"169": 200, "12345": 5},
          "/shows/169": dict(SHOW, name="Breaking Bad (upd)", updated=200)}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = ROUTES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        b = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class TVmaze(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        cls.facts = os.path.join(cls.tmp, "facts.db")
        f = sqlite3.connect(cls.facts)
        f.execute("create table tv_shows(tmdb_id integer primary key, imdb_id text, tvdb_id integer, status text, vote_count integer)")
        f.executemany("insert into tv_shows values (?,?,?,?,?)", [(1396, "tt0903747", 81189, "Ended", 15000),
                                                                (2, "tt9", None, "Returning Series", 10), (3, "tt404", None, "Ended", 5),
                                                                (4, None, None, "Ended", 1)])
        f.commit()
        f.close()
        cls.db = os.path.join(cls.tmp, "tvmaze.db")
        cls.env = dict(os.environ, FACTS_DB=cls.facts, TVMAZE_DB=cls.db, TVMAZE_GAP="0",
                       TVMAZE_API=f"http://127.0.0.1:{cls.srv.server_address[1]}")
        for cmd in ("map", "episodes", "schedule", "updates", "status"):
            r = subprocess.run([sys.executable, str(RAG / "tools" / "fetch_tvmaze.py"), cmd], env=cls.env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            cls.out = getattr(cls, "out", "") + r.stdout
        os.environ["TVMAZE_DB"] = cls.db
        sys.path.insert(0, str(RAG / "web"))
        import tvmaze
        tvmaze.DB = cls.db
        cls.T = tvmaze

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_map(self):
        c = sqlite3.connect(self.db)
        self.assertEqual(dict(c.execute("select tmdb_id, how from map_done")), {1396: "imdb", 2: "imdb", 3: "none"})
        self.assertEqual(c.execute("select summary from show where tvmaze_id = 169").fetchone()[0],
                         "Breaking Bad follows a chemistry teacher & more.")

    def test_rerun_skips_done(self):
        r = subprocess.run([sys.executable, str(RAG / "tools" / "fetch_tvmaze.py"), "map"], env=self.env, capture_output=True, text=True)
        self.assertIn("map: 0 shows to look up", r.stdout)

    def test_updates_refetch(self):
        self.assertEqual(self.T.show_extra(1396)["tvmaze_id"], 169)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("select name, updated from show where tvmaze_id = 169").fetchone(), ("Breaking Bad (upd)", 200))

    def test_show_extra(self):
        x = self.T.show_extra(1396)
        self.assertEqual((x["network"], x["air_time"], x["air_days"]), ("AMC", "22:00", ["Sunday"]))
        self.assertIn("tvmaze.com", x["credit"]["url"])
        self.assertEqual(self.T.show_extra(2)["next"]["date"], "2026-11-20")
        self.assertEqual(self.T.show_extra(99), {})

    def test_episode_extras(self):
        e = self.T.episode_extras(1396, 5)
        self.assertEqual(e[(5, 16)]["summary"], "All bad things must come to an end.")
        self.assertNotIn((0, None), self.T.episode_extras(1396))

    def test_upcoming(self):
        u = self.T.upcoming("2026-11-11", "2026-11-30")
        self.assertEqual([(x["tmdb_id"], x["episode"], x["streaming"], x["where"]) for x in u], [(2, 2, True, "Netflix")])
        self.assertEqual(self.T.upcoming("2026-11-11", "2026-11-30", [1396]), [])

    def test_missing_db(self):
        old = self.T.DB
        self.T.DB = "/nonexistent/tvmaze.db"
        self.T._TL.__dict__.clear()
        try:
            self.assertEqual((self.T.available(), self.T.show_extra(1396), self.T.upcoming("a", "z")), (False, {}, []))
        finally:
            self.T.DB = old
            self.T._TL.__dict__.clear()


if __name__ == "__main__":
    unittest.main()
