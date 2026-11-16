"""v8.1 round 45 (15 Nov 2026): the hidden forecast endpoint (4.6), the forecast store tool on a made-up
database, and the tool router (7.2). No user data; the store test trains tiny models and is skipped where
lightgbm is not installed."""
import copy
import datetime
import importlib
import json
import math
import os
import random
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import forecast_api as FA  # noqa: E402
import tool_router as TR  # noqa: E402

NOT_PROFIT = "Gross / budget is a ratio of two reported figures, not profit: marketing and cinema shares are not included."


def response(media_id="movie:7", horizon="d7"):
    est = lambda p, lo, hi: {"point": p, "intervals": [{"nominal_coverage": 0.5, "lower": lo * 1.5, "upper": hi / 1.5},
                                                        {"nominal_coverage": 0.8, "lower": lo, "upper": hi}]}
    none = {"point": None, "intervals": []}
    return {"schema_version": 1, "prediction_id": "v81-pf4bt-cqr-1:7:%s" % horizon, "media_id": media_id,
            "model_version": "v81-pf4bt-cqr-1", "status": "experimental", "horizon": horizon, "as_of": "2019-05-01T00:00:00Z",
            "currency": "USD", "budget": {"amount": 40000000.0, "known_as_of": None, "source_id": "tmdb"},
            "domestic_gross": none, "international_gross": copy.deepcopy(none), "worldwide_gross": est(1.2e8, 2e7, 6e8),
            "gross_to_budget_multiple": est(3.0, 0.5, 15.0), "source_notes": ["Budget as reported by tmdb."],
            "warnings": ["Experimental forecast.", NOT_PROFIT]}


def store(path, rows):
    c = sqlite3.connect(path)
    c.execute("create table forecasts(media_id text, horizon text, model_version text, as_of text, release_year integer, "
              "released integer, response_json text, created_at text, primary key (media_id, horizon))")
    for mid, h, d in rows:
        c.execute("insert into forecasts values (?,?,?,?,?,?,?,?)", (mid, h, "v", "x", 2019, 1, d if isinstance(d, str) else json.dumps(d), "now"))
    c.commit()
    c.close()


class TestForecastApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "forecast.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_by_default_and_404_for_everything_while_off(self):
        self.assertFalse(FA.ENABLED)
        store(self.db, [("movie:7", "d7", response())])
        self.assertEqual(FA.handle("movie:7", "d7", self.db), (404, {"detail": "Not Found"}))
        self.assertEqual(FA.handle("nonsense", "zz", self.db), (404, {"detail": "Not Found"}))

    def test_serves_the_stored_response_unchanged(self):
        store(self.db, [("movie:7", "d7", response()), ("movie:7", "d90", response(horizon="d90"))])
        status, body = FA.handle("movie:7", "d7", self.db, enabled=True)
        self.assertEqual((status, body), (200, response()))
        self.assertEqual(FA.handle("movie:7", "d90", self.db, enabled=True)[1]["horizon"], "d90")
        self.assertIsNone(body["domestic_gross"]["point"])                        # worldwide only
        self.assertEqual(body["status"], "experimental")

    def test_bad_requests_and_unknown_films(self):
        store(self.db, [("movie:7", "d7", response())])
        for mid in ("7", "tv:7", "movie:0", "movie:7 or 1=1", "movie:7;drop", "", "movie:99999999999"):
            self.assertEqual(FA.handle(mid, "d7", self.db, enabled=True)[0], 400, mid)
        self.assertEqual(FA.handle("movie:7", "opening", self.db, enabled=True)[0], 400)
        self.assertEqual(FA.handle("movie:8", "d7", self.db, enabled=True)[0], 404)
        self.assertEqual(FA.handle("movie:7", "d7", os.path.join(self.tmp.name, "absent.db"), enabled=True)[0], 404)
        self.assertEqual(sqlite3.connect(self.db).execute("select count(*) from forecasts").fetchone()[0], 1)

    def test_a_response_that_breaks_a_rule_is_not_served(self):
        bad = {}
        d = response(); d["warnings"].append("Expected profit of $20M."); bad["profit"] = d
        d = response(); d["source_notes"].append("ROI looks strong"); bad["roi"] = d
        d = response(); d["warnings"].append("This film will break even."); bad["break even"] = d
        d = response(); d["domestic_gross"] = {"point": 5e7, "intervals": []}; bad["territory"] = d
        d = response(); d["status"] = "validated"; bad["status"] = d
        d = response(); d["worldwide_gross"]["intervals"][1]["lower"] = 9e9; bad["range order"] = d
        d = response(); d["worldwide_gross"]["point"] = float("nan"); bad["nan"] = d
        d = response(); d["extra"] = 1; bad["extra field"] = d
        d = response(); del d["warnings"]; bad["missing field"] = d
        d = response(media_id="movie:8"); bad["other film's response"] = d
        for name, d in bad.items():
            path = os.path.join(self.tmp.name, name.replace(" ", "_").replace("'", "") + ".db")
            store(path, [("movie:7", "d7", d)])
            self.assertEqual(FA.handle("movie:7", "d7", path, enabled=True)[0], 404, name)
        store(os.path.join(self.tmp.name, "junk.db"), [("movie:7", "d7", "{not json")])
        self.assertIsNone(FA.get("movie:7", "d7", os.path.join(self.tmp.name, "junk.db")))
        self.assertEqual(FA.check(response()), [])                               # "not profit" itself is allowed
        self.assertEqual(FA.check([]), ["not an object"])

    def test_the_store_is_opened_read_only(self):
        store(self.db, [("movie:7", "d7", response())])
        before = open(self.db, "rb").read()
        for _ in range(3):
            FA.handle("movie:7", "d7", self.db, enabled=True)
        self.assertEqual(open(self.db, "rb").read(), before)

    def test_route_answers_404_while_the_switch_is_off(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as e:  # noqa: BLE001
            self.skipTest("fastapi test client not available: %s" % e)
        store(self.db, [("movie:7", "d7", response())])
        os.environ["FORECAST_DB"] = self.db
        try:
            for on, want in (("0", 404), ("1", 200)):
                os.environ["FORECAST_PANEL"] = on
                importlib.reload(FA)
                app = FastAPI()
                FA.install(app, lambda request: None)
                r = TestClient(app).get("/api/v8/forecast/movie:7?horizon=d7")
                self.assertEqual(r.status_code, want)
                if on == "1":
                    self.assertEqual(r.json(), response())
                    self.assertEqual(r.headers.get("cache-control"), "no-store")
                    from fastapi.responses import JSONResponse
                    app2 = FastAPI()
                    FA.install(app2, lambda request: JSONResponse({"error": "code"}, status_code=401))
                    self.assertEqual(TestClient(app2).get("/api/v8/forecast/movie:7").status_code, 401)   # the site's gate applies
        finally:
            os.environ.pop("FORECAST_PANEL", None)
            os.environ.pop("FORECAST_DB", None)
            importlib.reload(FA)


def synth_facts(path, n=700, seed=5):
    import facts_db as F
    c = F.connect(path)
    c.execute("create table if not exists money(tmdb_id integer primary key, budget real, gross real, budget_src text, gross_src text)")
    c.execute("create table if not exists film_videos(tmdb_id integer, video_key text, site text, type text, name text, official integer, published_at text)")
    c.execute("create table if not exists certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text)")
    rnd = random.Random(seed)
    genres = ["Action", "Comedy", "Drama", "Horror", "Family", "Science Fiction"]
    today = datetime.date.today()
    for i in range(1, n + 1):
        y = rnd.choice(list(range(1995, 2024)) + [2024, 2025, today.year + 1])
        d = datetime.date(y, rnd.randint(1, 12), rnd.randint(1, 28))
        g = rnd.sample(genres, rnd.randint(1, 2))
        b = math.exp(rnd.uniform(math.log(2e5), math.log(2e8)))
        ret = rnd.gauss(0.4 + (0.5 if "Horror" in g else 0) + 0.15 * math.log10(b / 1e6), 1.1)
        released = d <= today
        c.execute("insert into films(tmdb_id, imdb_id, title, original_title, release_date, us_release_date, year, runtime, genres_json, "
                  "original_language, countries_json, companies_json, vote_average, vote_count, overview) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (i, "tt%d" % (1000000 + i), "Film %d" % i, "Film %d" % i, d.isoformat(), d.isoformat(), y, rnd.randint(80, 150),
                   json.dumps(g), "en", '["US"]', "[]", 6.5, 500, "x"))
        c.execute("insert into money values (?,?,?,?,?)", (i, b, b * math.exp(ret) if released else None, "tmdb", "tmdb" if released else None))
        for k in range(rnd.randint(0, 3)):
            pub = d - datetime.timedelta(days=rnd.randint(1, 200))
            c.execute("insert into film_videos values (?,?,?,?,?,?,?)", (i, "k%d_%d" % (i, k), "YouTube", "Trailer", "t", 1, pub.isoformat() + "T00:00:00Z"))
        if rnd.random() < 0.8:
            c.execute("insert into certifications values (?,?,?,?,?)", (i, "US", rnd.choice(["G", "PG", "PG-13", "R"]), 3, None))
        for pid, kind, job, dep, ordn in ((rnd.randint(1, 60), "crew", "Director", "Directing", 0), (rnd.randint(100, 180), "crew", "Producer", "Production", 0),
                                          (rnd.randint(200, 300), "cast", None, None, 0), (rnd.randint(200, 300), "cast", None, None, 1)):
            c.execute("insert into credits(tmdb_id, person_id, kind, job, department, ord) values (?,?,?,?,?,?)", (i, pid, kind, job, dep, ordn))
    for pid in list(range(1, 61)) + list(range(100, 181)) + list(range(200, 301)):
        c.execute("insert or ignore into people(person_id, name) values (?,?)", (pid, "P%d" % pid))
    c.commit()
    c.close()


class TestForecastStore(unittest.TestCase):
    def test_rule_decision_table(self):
        try:
            import v81_forecast_store as S
        except ImportError as e:
            self.skipTest("model libraries not installed: %s" % e)
        ok = {"cov80": 0.79, "cov50": 0.48}
        self.assertTrue(S.decide(ok, ok, (-0.02, -0.03, -0.01), (0.0, -0.02, 0.02))["use_v2"])
        self.assertFalse(S.decide(ok, ok, (-0.02, -0.05, 0.001), (0.0, -0.02, 0.02))["use_v2"])       # CI touches 0
        self.assertFalse(S.decide(ok, {"cov80": 0.74, "cov50": 0.48}, (-0.02, -0.03, -0.01), (0.0, -0.02, 0.02))["use_v2"])
        self.assertFalse(S.decide(ok, ok, (-0.02, -0.03, -0.01), (0.03, 0.01, 0.05))["use_v2"])       # late clearly worse
        self.assertEqual(S.EXTRA["d90"], S.PRODUCER)                                                  # no US rating at 90 days
        self.assertNotIn("profit", S.late_warning({"cov80": 0.757, "cov50": 0.435}).lower())
        m, lo, hi = S.boot_diff([1.0] * 50, [2.0] * 50, n=200)
        self.assertEqual((m, lo, hi), (-1.0, -1.0, -1.0))

    def test_store_build_on_a_made_up_database(self):
        try:
            import lightgbm as lgb
            import v81_forecast as V
            import v81_forecast_store as S
        except ImportError as e:
            self.skipTest("model libraries not installed: %s" % e)
        tmp = tempfile.TemporaryDirectory()
        facts, out_db = os.path.join(tmp.name, "facts.db"), os.path.join(tmp.name, "logs", "forecast.db")
        import facts_db as F
        import performance as P
        old = (F.PATH, V.SEEDS, V._lgb, S.BOOT, os.environ.get("FACTS_DB"))

        def small(objective, alpha=None, seed=7):
            kw = dict(n_estimators=30, learning_rate=0.1, num_leaves=7, min_child_samples=10, random_state=seed, verbose=-1)
            if objective == "quantile":
                return lgb.LGBMRegressor(objective="quantile", alpha=alpha, **kw)
            return lgb.LGBMRegressor(objective="huber", alpha=1.0, **kw)
        try:
            os.environ["FACTS_DB"] = facts
            F.PATH = type(F.PATH)(facts) if not isinstance(F.PATH, str) else facts
            synth_facts(facts)
            V.SEEDS, V._lgb, S.BOOT = (7,), small, 100
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = S.main(["--db", out_db, "--out", os.path.join(tmp.name, "store")])
            self.assertEqual(rc, 0, buf.getvalue()[-800:])
            res = json.load(open(os.path.join(tmp.name, "store.json")))
            self.assertEqual(set(res["horizons"]), {"d90", "d7"})
            self.assertEqual(res["invalid_not_stored"], 0)
            self.assertGreater(res["unreleased"], 0)
            c = sqlite3.connect(out_db)
            n, years = c.execute("select count(*), min(release_year) from forecasts").fetchone()
            self.assertGreater(n, 100)
            self.assertGreaterEqual(years, 2016)                                  # training films get no forecast
            for mid, h, ver, js, released, year in c.execute(
                    "select media_id, horizon, model_version, response_json, released, release_year from forecasts"):
                d = json.loads(js)
                self.assertEqual(FA.check(d), [], mid)
                self.assertEqual((d["media_id"], d["horizon"], d["model_version"]), (mid, h, ver))
                self.assertEqual(ver, res["horizons"][h]["chosen"])
                self.assertEqual(any("Back-test" in w for w in d["warnings"]), bool(released))
                self.assertEqual(any("Released after 2019" in w for w in d["warnings"]), year >= 2020)
            mid = c.execute("select media_id from forecasts where horizon = 'd7' limit 1").fetchone()[0]
            c.close()
            self.assertEqual(FA.handle(mid, "d7", out_db, enabled=True)[0], 200)
        finally:
            F.PATH, V.SEEDS, V._lgb, S.BOOT = old[:4]
            if old[4] is None:
                os.environ.pop("FACTS_DB", None)
            else:
                os.environ["FACTS_DB"] = old[4]
            tmp.cleanup()


def plan(**f):
    base = {"year_min": None, "year_max": None, "runtime_min_minutes": None, "runtime_max_minutes": None, "genres_any": [],
            "genres_all": [], "genres_exclude": [], "watch_region": None, "watch_provider_ids_any": [], "watch_monetization": [],
            "exclude_watched": False, "include_media_ids": [], "exclude_media_ids": []}
    media = f.pop("media", ["movie"])
    text = f.pop("text", "")
    base.update(f)
    return {"schema_version": "1.1", "intent": "recommend", "media_types": media, "query_text": text,
            "entity": {"internal_media_id": None, "tmdb_id": None, "imdb_id": None, "candidate_title": None},
            "filters": base, "retrieval": {"routes": ["sql"], "article_mode": "none", "max_candidates": 50},
            "response": {"language": "en", "include_explanations": True, "include_sources": False}}


class TestToolRouter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        c = self.c = sqlite3.connect(os.path.join(self.tmp.name, "facts.db"))
        c.execute("create table films(tmdb_id integer primary key, title text, year integer, runtime integer, genres_json text, vote_count integer)")
        c.execute("create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text)")
        c.execute("create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer, fetched_at text)")
        c.execute("create table tv_shows(tmdb_id integer primary key, name text, first_year integer, episode_runtime integer, genres_json text, vote_count integer)")
        c.execute("create table tv_ratings(tmdb_id integer, region text, rating text)")
        c.execute("create table tv_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer)")
        c.execute("create table keywords(tmdb_id integer, keyword_id integer, name text)")
        c.execute("create table people(person_id integer primary key, name text)")
        c.execute("create table credits(tmdb_id integer, person_id integer, kind text, job text, ord integer)")
        for i in range(1, 9):
            c.execute("insert into films values (?,?,?,?,?,?)", (i, "Film %d" % i, 2000 + i, 100, '["Comedy"]' if i % 2 else '["Drama"]', 1000 - i))
        c.execute("insert into tv_shows values (1, 'Show 1', 2015, 30, '[\"Comedy\"]', 50)")
        c.commit()
        self.asked = []

        def likes(user):
            self.asked.append(user)
            return ([1], [2], [])
        self.ctx = TR.Ctx(c, user_id="user-a", likes=likes, watched=lambda u: [3], movies_db=os.path.join(self.tmp.name, "absent.db"),
                          forecast_db=os.path.join(self.tmp.name, "forecast.db"))
        import personal
        personal._FEAT.clear()
        personal._DF.clear()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def call(self, tool, args, **kw):
        return TR.call({"tool": tool, "args": args}, self.ctx, enabled=True, **kw)

    def kind(self, req, **kw):
        with self.assertRaises(TR.ToolError) as cm:
            TR.call(req, self.ctx, **kw)
        return cm.exception.kind

    def test_off_by_default(self):
        self.assertFalse(TR.TOOL_ROUTER)
        self.assertEqual(self.kind({"tool": "lookup", "args": {"media_id": "movie:1"}}), "disabled")
        self.assertEqual(TR.safe_call({"tool": "lookup", "args": {"media_id": "movie:1"}}, self.ctx)["error"]["kind"], "disabled")

    def test_only_the_four_tools_exist(self):
        self.assertEqual(sorted(TR.TOOLS), ["forecast", "lookup", "recommend", "search"])
        self.assertEqual([t["name"] for t in TR.describe()], list(TR.TOOLS))
        for name in ("sql", "http", "http_get", "shell", "exec", "watchlist", "memory", "read_file", "Lookup", "lookup ", ""):
            self.assertEqual(self.kind({"tool": name, "args": {}}, enabled=True), "unknown_tool", name)

    def test_sql_urls_commands_and_other_peoples_ids_have_no_field_to_travel_in(self):
        for extra in ({"sql": "select * from users"}, {"url": "http://169.254.169.254/"}, {"cmd": "rm -rf /"},
                      {"user": "user-b"}, {"user_id": "user-b"}, {"path": "/etc/passwd"}, {"likes": [5]}):
            for tool, args in (("lookup", {"media_id": "movie:1"}), ("search", {"plan": plan()}), ("recommend", {"plan": plan()}),
                               ("forecast", {"media_id": "movie:1"})):
                self.assertEqual(self.kind({"tool": tool, "args": dict(args, **extra)}, enabled=True), "bad_request", (tool, extra))
        for mid in ("1", "movie:1; drop table films", "movie:1 or 1=1", "http://x/movie:1", "../movie:1", 1, None, ["movie:1"]):
            self.assertEqual(self.kind({"tool": "lookup", "args": {"media_id": mid}}, enabled=True), "bad_request", mid)
        p = plan()
        p["filters"]["sql"] = "1=1"
        self.assertEqual(self.kind({"tool": "search", "args": {"plan": p}}, enabled=True), "bad_request")
        p = plan(genres_any=["Comedy' or '1'='1"])
        self.assertEqual(self.kind({"tool": "search", "args": {"plan": p}}, enabled=True), "bad_request")
        for bad in ("not json", "[1,2]", {"tool": "lookup"}, {"tool": "lookup", "args": {}, "user": "b"}, {"tool": 5, "args": {}},
                    {"tool": "lookup", "args": "movie:1"}, None, 7, "x" * 9000,
                    {"tool": "search", "args": {"plan": plan(text="y" * 9000)}}):
            self.assertEqual(self.kind(bad, enabled=True), "bad_request", str(bad)[:40])
        self.assertEqual(self.c.execute("select count(*) from films").fetchone()[0], 8)

    def test_lookup_and_search(self):
        self.assertEqual(self.call("lookup", {"media_id": "movie:1"})["result"],
                         {"found": True, "media_id": "movie:1", "media_type": "movie", "title": "Film 1", "year": 2001})
        self.assertEqual(self.call("lookup", {"media_id": "tv:1"})["result"]["title"], "Show 1")     # tv:1 is not movie:1
        self.assertEqual(self.call("lookup", {"media_id": "movie:999"})["result"], {"found": False, "media_id": "movie:999"})
        got = self.call("search", {"plan": plan(genres_any=["Comedy"], media=["movie", "tv"]), "limit": 3})["result"]
        self.assertEqual(got["media_ids"], ["movie:1", "movie:3", "movie:5", "tv:1"])            # 3 per media type, films first
        for lim in (0, 51, "5", 2.5):
            self.assertEqual(self.kind({"tool": "search", "args": {"plan": plan(), "limit": lim}}, enabled=True), "bad_request")
        as_text = TR.call(json.dumps({"tool": "lookup", "args": {"media_id": "movie:2"}}), self.ctx, enabled=True)
        self.assertEqual(as_text["result"]["title"], "Film 2")

    def test_free_text_is_data_and_never_executed(self):
        text = "'; drop table films; -- $(rm -rf /) http://evil.example/x"
        got = self.call("search", {"plan": plan(text=text), "limit": 50})["result"]
        self.assertEqual(len(got["media_ids"]), 8)
        self.assertEqual(self.c.execute("select count(*) from films").fetchone()[0], 8)

    def test_watched_films_come_from_the_server_for_the_asking_person(self):
        got = self.call("search", {"plan": plan(exclude_watched=True), "limit": 50})["result"]["media_ids"]
        self.assertNotIn("movie:3", got)
        self.assertEqual(len(got), 7)
        self.ctx.user_id = None                                                   # nobody signed in: nothing to exclude
        self.assertEqual(len(self.call("search", {"plan": plan(exclude_watched=True), "limit": 50})["result"]["media_ids"]), 8)

    def test_recommend_uses_only_the_asking_persons_likes_and_respects_its_switch(self):
        import recs
        self.assertEqual(self.kind({"tool": "recommend", "args": {"plan": plan()}}, enabled=True), "not_available")
        old = recs.enabled
        recs.enabled = lambda: True
        try:
            got = self.call("recommend", {"plan": plan(exclude_watched=True), "k": 5})["result"]["picks"]
            self.assertEqual(self.asked, ["user-a"])
            ids = [p["media_id"] for p in got]
            self.assertEqual(len(ids), 5)
            for gone in ("movie:1", "movie:2", "movie:3"):                        # liked, disliked, watched
                self.assertNotIn(gone, ids)
            self.assertTrue(all(p["reason"]["code"] in recs.REASONS for p in got))
            self.assertEqual(self.kind({"tool": "recommend", "args": {"plan": plan(media=["tv"])}}, enabled=True), "bad_request")
            self.assertEqual(self.kind({"tool": "recommend", "args": {"plan": plan(), "k": 21}}, enabled=True), "bad_request")
        finally:
            recs.enabled = old

    def test_forecast_tool_follows_the_forecast_switch(self):
        store(self.ctx.forecast_db, [("movie:7", "d7", response())])
        self.assertEqual(self.kind({"tool": "forecast", "args": {"media_id": "movie:7"}}, enabled=True), "not_available")
        self.assertEqual(self.kind({"tool": "forecast", "args": {"media_id": "tv:7"}}, enabled=True), "bad_request")
        self.assertEqual(self.kind({"tool": "forecast", "args": {"media_id": "movie:7", "horizon": "d1"}}, enabled=True), "bad_request")
        old = FA.ENABLED
        FA.ENABLED = True
        try:
            self.assertEqual(self.call("forecast", {"media_id": "movie:7"})["result"], {"found": True, "forecast": response()})
            self.assertEqual(self.call("forecast", {"media_id": "movie:8"})["result"], {"found": False, "media_id": "movie:8"})
        finally:
            FA.ENABLED = old

    def test_failures_are_reported_without_internal_text(self):
        self.c.execute("drop table films")
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            got = TR.safe_call({"tool": "lookup", "args": {"media_id": "movie:1"}}, self.ctx, enabled=True)
        self.assertEqual(got, {"tool": "lookup", "ok": False, "error": {"kind": "failed", "message": "the tool could not answer"}})
        self.assertIsNone(TR.safe_call({"tool": "sql", "args": {}}, self.ctx, enabled=True)["tool"])


if __name__ == "__main__":
    unittest.main()
