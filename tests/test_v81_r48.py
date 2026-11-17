"""v8.1 round 48 (16 Nov 2026): the third ranking candidate's choice rule, the ranking module reading its result,
the cautious Guardian call count, burst probes, the forecast panel's routes and film page hook, and the
pre-publish check. No network, no user data."""
import datetime
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import forecast_api as FA  # noqa: E402
import guardian_live as GL  # noqa: E402
import recs as R  # noqa: E402
import v81_guardian_quota as G  # noqa: E402

T0 = datetime.datetime(2026, 11, 17, 15, 0, tzinfo=datetime.timezone.utc)


class TestThirdCandidate(unittest.TestCase):
    def setUp(self):
        try:
            import v81_recs_v3 as V3
        except ImportError as e:
            self.skipTest("benchmark libraries not installed: %s" % e)
        self.V3 = V3

    def test_choice_maximises_the_smaller_margin(self):
        m = {(2, 1, 0): {"uniform": -0.015, "popular": 0.150}, (2, 4, 0): {"uniform": 0.026, "popular": 0.060},
             (1, 4, 0): {"uniform": 0.030, "popular": 0.020}, (0, 4, 1): {"uniform": 0.040, "popular": -0.100}}
        self.assertEqual(self.V3.choose(m), (2, 4, 0))            # 0.026 is the best "worse side"
        self.assertEqual(self.V3.choose({(1, 0, 0): {"uniform": 0.01, "popular": 0.01}, (2, 0, 0): {"uniform": 0.01, "popular": 0.01}}), (2, 0, 0))

    def test_margins_are_measured_against_the_current_ranking(self):
        import v81_recs_benchmark as B
        rows = {p: [] for p in ("uniform", "popular")}
        cur = {p: {} for p in rows}
        for u in range(1, 31):
            cand, pos = list(range(1, 21)), {u % 5 + 1}
            s = {"c": {f: 1.0 if f in pos else 0.0 for f in cand}, "nt": {f: 0.0 for f in cand}, "pt": {f: (20 - f) / 20 for f in cand}}
            for p in rows:
                rows[p].append((u, pos, cand, s))
                cur[p][u] = sorted(cand, key=lambda f: -s["pt"][f])[:10]      # a popularity-only "current"
        m, base = self.V3.margins(rows, cur, grid=[(1, 0, 0), (0, 0, 1)])
        self.assertGreater(m[(1, 0, 0)]["uniform"], 0.3)             # content finds the liked film first
        self.assertAlmostEqual(m[(0, 0, 1)]["uniform"], 0.0)         # the same order as current: no margin
        self.assertEqual(set(base), {"uniform", "popular"})
        self.assertEqual(self.V3.MIN_USERS, 500)
        self.assertEqual((self.V3.Y2021, self.V3.Y2022), (1609459200, 1640995200))
        self.assertGreater(self.V3.Y2021, B.CUT)

    def test_new_users_are_those_outside_the_2020_runs(self):
        import pandas as pd
        V3 = self.V3
        rows = []
        for u in (1, 2, 3):
            for m in range(1, 26):
                rows.append((u, m, 4.0, 1500000000 + m))               # 25 ratings before 2020
        rows += [(1, 90, 5.0, 1590000000), (2, 91, 5.0, 1620000000), (3, 92, 2.0, 1620000000)]   # liked in 2020 / liked in 2021 / disliked
        Rr = pd.DataFrame(rows, columns=["userId", "tmdb", "rating", "timestamp"])
        _t, _p, e20 = V3.eligible(Rr, 1577836800, V3.Y2021)
        _t, _p, e21 = V3.eligible(Rr, V3.Y2021, V3.Y2022)
        self.assertEqual((e20, e21), ([1], [2]))


class TestRankingReadsTheNewResult(unittest.TestCase):
    def test_v3_file_first_then_the_15_nov_file_and_only_a_pass_counts(self):
        tmp = tempfile.mkdtemp()
        old = (R.WEIGHTS_V3, R.WEIGHTS_FILE)
        os.environ.pop("RECS_V2_WEIGHTS", None)
        try:
            R.WEIGHTS_V3, R.WEIGHTS_FILE = os.path.join(tmp, "v3.json"), os.path.join(tmp, "single.json")
            self.assertEqual(R.weights(), (R.FALLBACK_W, "fallback"))
            json.dump({"weights": [2, 1, 0], "rule": "FAIL"}, open(R.WEIGHTS_FILE, "w"))      # the real 15 Nov outcome
            self.assertEqual(R.weights()[1], "fallback")
            json.dump({"weights": [2, 4, 0], "rule": "FAIL"}, open(R.WEIGHTS_V3, "w"))
            self.assertEqual(R.weights()[1], "fallback")
            json.dump({"weights": [2, 4, 0], "rule": "UNRUN"}, open(R.WEIGHTS_V3, "w"))
            self.assertEqual(R.weights()[1], "fallback")
            json.dump({"weights": [2, 4, 0], "rule": "PASS"}, open(R.WEIGHTS_V3, "w"))
            self.assertEqual(R.weights(), ((2.0, 4.0, 0.0), "v81_recs_v3"))
            self.assertFalse(R.enabled())                             # a PASS alone enables nothing: RECS_V2 is off
        finally:
            R.WEIGHTS_V3, R.WEIGHTS_FILE = old
            shutil.rmtree(tmp)


class TestGuardianCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = GL.connect(os.path.join(self.tmp, "live.db"))
        self.probe = os.path.join(self.tmp, "probe.jsonl")

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.tmp)

    def test_the_lowest_recent_reading_wins(self):
        with open(self.probe, "w") as fh:                               # the real 16 Nov pattern: servers count separately
            for h, left in ((5, 376), (4, 499), (2, 498), (1, 499)):
                fh.write(json.dumps({"at": GL.iso(T0 - datetime.timedelta(hours=h)), "remaining_day": left}) + "\n")
        self.assertEqual(GL.calls_left(self.c, T0, self.probe), 376)    # not the newest (499)
        GL.record(self.c, "live", 200, None, GL.iso(T0 - datetime.timedelta(minutes=30)))
        GL.record(self.c, "live", 200, 12, GL.iso(T0 - datetime.timedelta(minutes=20)))
        GL.record(self.c, "live", 200, None, GL.iso(T0 - datetime.timedelta(minutes=10)))
        self.assertEqual(GL.calls_left(self.c, T0, self.probe), 11)     # 12, minus the one call made after it
        self.assertIsNone(GL.calls_left(self.c, T0 + datetime.timedelta(hours=7), self.probe))   # all readings too old

    def test_burst_reports_the_lowest_of_several_replies(self):
        import io
        import urllib.error
        left = [499, 376, 498, 497, 499]

        class Reply:
            status = 200

            def __init__(self, n):
                self.headers = {"X-RateLimit-Remaining-day": str(n), "X-RateLimit-Limit-day": "500"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        naps = []
        r = G.burst("KEY", 5, self.probe, lambda req, timeout=0: Reply(left.pop(0)), sleep=naps.append)
        self.assertEqual(r, {"calls": 5, "lowest": 376, "highest": 499})
        self.assertEqual(len(naps), 4)
        self.assertNotIn("KEY", open(self.probe).read())
        self.assertEqual(len(G.read_log(self.probe)), 5)

    def test_report_checks_midnight_utc(self):
        rows = [{"at": "2026-11-17T23:40:0%dZ" % i, "remaining_day": v, "status": 200} for i, v in enumerate((90, 310, 95, 498, 96))]
        rows += [{"at": "2026-11-18T00:10:0%dZ" % i, "remaining_day": v, "status": 200} for i, v in enumerate((499, 498, 497, 499, 496))]
        text = G.report(rows)
        self.assertIn("midnight UTC 2026-11-17 -> 2026-11-18: lowest 90 before, 496 after -> the count resets at 00:00 UTC", text)
        flat = [dict(r, remaining_day=95) for r in rows]
        self.assertIn("no clear reset at 00:00 UTC", G.report(flat))
        self.assertIn("2026-11-17 23h: 90 / 498 (5 replies)", text)


class TestForecastHook(unittest.TestCase):
    def test_locale_route_follows_the_switch_and_serves_two_files_only(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest("fastapi test client not available: %s" % e)
        try:
            for on, want in (("0", 404), ("1", 200)):
                os.environ["FORECAST_PANEL"] = on
                importlib.reload(FA)
                app = FastAPI()
                FA.install(app, lambda request: None)
                c = TestClient(app)
                self.assertEqual(c.get("/api/v8/i18n/en.json").status_code, want)
                if on == "1":
                    self.assertEqual(c.get("/api/v8/i18n/en.json").json()["forecast.title"], "Box-office forecast")
                    self.assertEqual(c.get("/api/v8/i18n/es.json").status_code, 200)
                    for bad in ("fr.json", "en", "app.css", "..%2Fapp.css", "en.json.bak"):
                        self.assertEqual(c.get("/api/v8/i18n/" + bad).status_code, 404, bad)
        finally:
            os.environ.pop("FORECAST_PANEL", None)
            importlib.reload(FA)

    def test_film_page_loads_the_panel_only_after_a_200_and_through_the_access_code(self):
        src = open(os.path.join(ROOT, "web", "v8", "film.js"), encoding="utf-8").read()
        i = src.index("/api/v8/forecast/movie:${d.id}?horizon=d7")
        block = src[i:src.index("}).catch(() => {});", i)]
        self.assertIn("import('./forecast_panel.js')", block)
        self.assertIn("api('/api/v8/i18n/en.json')", block)             # the words travel with the access code
        self.assertNotIn("i18n.load(", block)                           # a plain fetch has no access code (found in the browser)
        self.assertNotIn("innerHTML", block)
        css = open(os.path.join(ROOT, "web", "v8", "app.css"), encoding="utf-8").read()
        self.assertIn(".fcast {", css)


class TestPublishCheck(unittest.TestCase):
    def test_finds_secrets_personal_data_and_data_files_without_printing_them(self):
        import v81_publish_check as PC
        tmp = tempfile.mkdtemp()
        try:
            run = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)
            run("init", "-q", ".")
            run("config", "user.email", "t@example.com")
            run("config", "user.name", "T")
            os.makedirs(os.path.join(tmp, "sets"))
            files = {"ok.py": "print('hello')\nKEY = os.environ.get('TMDB_API_KEY')\n",
                     # built from pieces so this test file does not itself look like a leak to the check
                     "leak.py": "TMDB_API_KEY = '" + "abcdef0123456789" + "abcdef0123456789'\nmail = 'someone" + "@" + "merrimack.edu'\n",
                     "keys.env": "GUARDIAN_API_KEY=" + "11111111-2222-3333-4444-555555555555\n",
                     "sets/sealed_v8_an.json": "[]", "users.db": "x", "notes.md": "contact: noreply@anthropic.com\n"}
            for name, text in files.items():
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write(text)
            run("add", "-A")
            run("commit", "-qm", "x")
            res = PC.scan(tmp)
            kinds = {(f["file"], f["kind"]) for f in res["findings"]}
            self.assertIn(("leak.py", "key-like value"), kinds)
            self.assertIn(("leak.py", "email address"), kinds)
            self.assertIn(("keys.env", "secrets file"), kinds)
            self.assertIn(("sets/sealed_v8_an.json", "sealed test set"), kinds)
            self.assertIn(("users.db", "database file"), kinds)
            self.assertNotIn("ok.py", {f["file"] for f in res["findings"]})          # reading a key from the environment is fine
            self.assertNotIn("notes.md", {f["file"] for f in res["findings"]})       # the commit trailer address is allowed
            text = PC.report(res)
            for secret in ("abcdef0123456789", "11111111-2222", "someone@"):
                self.assertNotIn(secret, text)                                         # findings are located, never shown
            self.assertIn("NOT ready", text)
            self.assertFalse(res["ready"])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
