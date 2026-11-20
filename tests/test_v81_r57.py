"""v8.1 round 57 (20 Nov 2026): rent / buy prices (Watchmode + Apple, switch DEALS), the forecast "update after the
first week" horizon in the API and panel, the "running total" wording on the film page, and the market inputs of the
forecast candidate. No network: every call goes to a fake opener."""
import datetime
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), os.path.join(ROOT, "tools"), ROOT]
import watch_prices as WP  # noqa: E402

PROBE = [  # the shape round 56's probe saw (Interstellar, 20 Nov)
    {"source_id": 1, "name": "Paramount Plus", "type": "sub", "region": "US", "format": "4K", "price": None, "web_url": "https://x.example/p"},
    {"source_id": 2, "name": "Amazon", "type": "rent", "region": "US", "format": "HD", "price": 4.99, "web_url": "https://www.amazon.com/x"},
    {"source_id": 2, "name": "Amazon", "type": "rent", "region": "US", "format": "HD", "price": 4.99, "web_url": "https://www.amazon.com/x"},
    {"source_id": 3, "name": "Fandango at Home", "type": "rent", "region": "US", "format": "SD", "price": 3.99, "web_url": "deeplinks available on paid plans"},
    {"source_id": 2, "name": "Amazon", "type": "buy", "region": "US", "format": "HD", "price": 14.99, "web_url": "https://www.amazon.com/x"},
    {"source_id": 9, "name": "Elsewhere", "type": "rent", "region": "GB", "format": "HD", "price": 1.0, "web_url": "https://y.example"},
    {"source_id": 4, "name": "Odd", "type": "buy", "region": "US", "format": "HD", "price": 0, "web_url": None},
]


class Resp(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def facts():
    c = sqlite3.connect(":memory:")
    c.execute("create table films(tmdb_id integer primary key, title text, year integer)")
    c.execute("insert into films values (157336, 'Interstellar', 2014)")
    c.execute("insert into films values (5, 'Small Film', 2020)")
    return c


class TestPrices(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.c = WP.connect(os.path.join(self.d, "deals.db"))
        self.calls = []

    def opener(self, wm=None, it=None):
        def op(req, timeout=None):
            url = req.full_url
            self.calls.append((url, dict(req.header_items())))
            if "watchmode" in url:
                return wm() if callable(wm) else Resp(200, wm if wm is not None else PROBE, {"X-Account-Quota-Used": "12"})
            return Resp(200, it or {"results": []})
        return op

    def test_shape(self):
        s = WP.shape_watchmode(PROBE)
        self.assertEqual([x["service"] for x in s["rent"]], ["Fandango at Home", "Amazon"])   # cheapest first, duplicates gone
        self.assertIsNone(s["rent"][0]["url"])                                               # not an https link
        self.assertEqual(s["buy"], [{"service": "Amazon", "format": "HD", "price": 14.99, "url": "https://www.amazon.com/x"}])
        self.assertEqual(s["subscription"], ["Paramount Plus"])

    def test_cache_key_header_and_no_key_stored(self):
        t = datetime.datetime(2026, 11, 20, 15, tzinfo=datetime.timezone.utc)
        r = WP.prices(facts(), 157336, c=self.c, key="sekrit-123456", enabled=True, opener=self.opener(), at=t)
        self.assertEqual(r["source"], "watchmode")
        self.assertEqual(r["checked"], "2026-11-20")
        self.assertIn("Watchmode", r["credit"])
        url, hdr = self.calls[0]
        self.assertIn("/title/movie-157336/sources/", url)
        self.assertNotIn("sekrit", url)                                    # the key goes in a header, never the URL
        self.assertEqual(hdr.get("X-api-key"), "sekrit-123456")
        r2 = WP.prices(facts(), 157336, c=self.c, key="sekrit-123456", enabled=True, opener=self.opener(),
                       at=t + datetime.timedelta(days=3))
        self.assertEqual(len(self.calls), 1)                               # cached for RECHECK_DAYS
        self.assertEqual(r2["rent"], r["rent"])
        dump = "\n".join(self.c.iterdump())
        self.assertNotIn("sekrit", dump)
        self.assertEqual(self.c.execute("select value from meta where key='quota_used'").fetchone()[0], "12")

    def test_off_means_no_call(self):
        r = WP.prices(facts(), 157336, c=self.c, key="k", enabled=False, opener=self.opener())
        self.assertEqual(self.calls, [])
        self.assertEqual(r["rent"], [])

    def test_daily_cap_and_month_stop(self):
        t = datetime.datetime(2026, 11, 20, 15, tzinfo=datetime.timezone.utc)
        for i in range(WP.DAILY_CAP):
            self.c.execute("insert into calls values (?,?,?,?)", (WP.iso(t), "watchmode", 200, None))
        self.assertEqual(WP.why_not(self.c, t), "daily limit reached")
        self.c.execute("delete from calls")
        self.c.execute("insert or replace into meta values ('quota_used', ?)", (str(WP.MONTH_STOP),))
        self.assertEqual(WP.why_not(self.c, t), "monthly allowance nearly used")
        r = WP.prices(facts(), 157336, c=self.c, key="k", enabled=True, opener=self.opener(), at=t)
        self.assertFalse(any("watchmode" in u for u, _ in self.calls))     # stopped before Watchmode
        self.assertTrue(any("itunes" in u for u, _ in self.calls))         # Apple asked instead

    def test_itunes_only_same_title_and_year(self):
        res = {"results": [
            {"trackName": "Small Film 2", "releaseDate": "2020-05-01", "trackRentalPrice": 1.99},
            {"trackName": "Small Film", "releaseDate": "2012-05-01", "trackRentalPrice": 2.99},
            {"trackName": "Small Film", "releaseDate": "2020-05-01T07:00:00Z", "trackRentalPrice": 3.99, "trackHdPrice": 12.99,
             "trackViewUrl": "https://tv.apple.com/x"}]}
        r = WP.prices(facts(), 5, c=self.c, key="k", enabled=True, opener=self.opener(wm=[], it=res))
        self.assertEqual(r["source"], "itunes")
        self.assertEqual(r["rent"][0]["price"], 3.99)
        self.assertEqual(r["buy"][0]["price"], 12.99)

    def test_429_pauses(self):
        t = datetime.datetime(2026, 11, 20, 15, tzinfo=datetime.timezone.utc)
        self.c.execute("insert into calls values (?,?,?,?)", (WP.iso(t - datetime.timedelta(minutes=10)), "watchmode", 429, None))
        self.assertIn("paused", WP.why_not(self.c, t))
        self.assertIsNone(WP.why_not(self.c, t + datetime.timedelta(hours=2)))

    def test_wired(self):
        s = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
        self.assertIn("watch_prices.install(app, _gate)", s)
        j = open(os.path.join(ROOT, "web", "v8", "film.js"), encoding="utf-8").read()
        self.assertIn("/api/v8/film/${d.id}/prices", j)
        self.assertNotRegex(j, r"(?i)save \$|you save|cheapest deal|% off")


class TestUpdateHorizon(unittest.TestCase):
    def test_update_round_trip(self):
        import forecast_api as FA
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        d = {"schema_version": 1, "prediction_id": "v81-upd:7:update", "media_id": "movie:7", "model_version": "v81-upd",
             "status": "experimental", "horizon": "opening_weekend_update", "as_of": "2026-11-12T12:00:00Z", "currency": "USD",
             "budget": {"amount": 1e8, "known_as_of": None, "source_id": "x"},
             "domestic_gross": {"point": None, "intervals": []}, "international_gross": {"point": None, "intervals": []},
             "worldwide_gross": {"point": 2e8, "intervals": [{"nominal_coverage": 0.8, "lower": 1e8, "upper": 4e8}]},
             "gross_to_budget_multiple": {"point": 2.0, "intervals": [{"nominal_coverage": 0.8, "lower": 1.0, "upper": 4.0}]},
             "source_notes": [], "warnings": []}
        p = os.path.join(tempfile.mkdtemp(), "f.db")
        c = sqlite3.connect(p)
        c.execute("create table forecasts(media_id text, horizon text, model_version text, as_of text, release_year integer, "
                  "released integer, response_json text, created_at text, primary key (media_id, horizon))")
        c.execute("insert into forecasts values ('movie:7','update','v','a',2026,1,?, 'n')", (json.dumps(d),))
        c.commit()
        c.close()
        self.assertEqual(FA.handle("movie:7", "update", db_path=p, enabled=True)[0], 200)
        self.assertEqual(FA.handle("movie:7", "d7", db_path=p, enabled=True)[0], 404)
        self.assertEqual(FA.handle("movie:7", "d1", db_path=p, enabled=True)[0], 400)
        self.assertEqual(FA.handle("movie:7", "update", db_path=p, enabled=False)[0], 404)

    def test_panel_words(self):
        en = json.load(open(os.path.join(ROOT, "web", "v8", "locales", "en.json"), encoding="utf-8"))
        es = json.load(open(os.path.join(ROOT, "web", "v8", "locales", "es.json"), encoding="utf-8"))
        for k in ("forecast.horizon.update", "forecast.before", "forecast.running"):
            self.assertIn(k, en)
            self.assertIn(k, es)
        self.assertIn("{value}", en["forecast.running"])
        j = open(os.path.join(ROOT, "web", "v8", "film.js"), encoding="utf-8").read()
        self.assertIn("horizon=update", j)
        self.assertIn("Worldwide gross so far", j)
        self.assertRegex(j, r"const RUNNING_DAYS = \d+")


class TestMarketInputs(unittest.TestCase):
    def test_add_market(self):
        try:
            import v81_forecast_v3 as F3
        except Exception as e:  # noqa: BLE001  - needs the server's model libraries
            self.skipTest("forecast libraries not installed here: %s" % e)
        c = sqlite3.connect(":memory:")
        c.execute("create table films(tmdb_id integer, original_language text, countries_json text)")
        c.executemany("insert into films values (?,?,?)", [(1, "hi", '["IN"]'), (2, "en", '["US","IN"]'), (3, "ja", '["JP"]'), (4, "en", "[]")])
        rows = [{"tmdb_id": i} for i in (1, 2, 3, 4)]
        F3.add_market(c, rows)
        self.assertEqual([r["country_in"] for r in rows], [1.0, 1.0, 0.0, 0.0])
        self.assertEqual([r["lang_indic"] for r in rows], [1.0, 0.0, 0.0, 0.0])
        self.assertEqual([r["country_other_only"] for r in rows], [1.0, 0.0, 1.0, 0.0])
        src = open(F3.__file__, encoding="utf-8").read()
        self.assertIn("RULE A", src)
        self.assertIn("RULE B", src)
        self.assertNotIn("log_votes", src.split('UPDATE = ')[1].split("\n")[0])   # vote counts never an input


if __name__ == "__main__":
    unittest.main()
