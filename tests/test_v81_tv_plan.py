"""v8.1 slice 2.3 (15 Nov 2026): TV query plans, mixed film + show plans as media ids, and the shadow adapter.
Everything runs on a small made-up facts.db; no user data, no model."""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import media_identity as MI  # noqa: E402
import plan_adapter as PA  # noqa: E402
import plan_sql as PS  # noqa: E402
import plan_sql_tv as PT  # noqa: E402
import query_plan as QP  # noqa: E402
import v81_tv_conformance as TC  # noqa: E402

# id, first_year, episode_runtime, genres_json, US rating, US listings [(kind, provider)]
SHOWS = [
    (1, 2019, 30, '["Comedy"]', "TV-14", [("flatrate", "Netflix")]),
    (2, 2011, 55, '["Sci-Fi & Fantasy", "Drama"]', "TV-MA", [("flatrate", "Max")]),
    (3, 1994, 22, '["Comedy", "Kids"]', "TV-G", [("ads", "Tubi TV")]),
    (4, None, 45, '["Action & Adventure"]', None, []),                       # no year, no rating
    (5, 2020, None, '["Drama", "Crime"]', "TV-PG", [("rent", "Netflix")]),   # no episode length; rent only
    (6, 2015, 0, None, "NR", [("flatrate", "Hulu")]),                        # no length, no genres, rating not a TV rating
    (7, 2022, 48, '["War & Politics", "Drama"]', "tv-ma ", [("free", "Kanopy")]),
    (8, 2008, 25, 'not json', "TV-Y7", [("flatrate", "Netflix Standard with Ads")]),
    (9, 2017, 60, '["Reality"]', "TV-14", [("flatrate", "Netflixxx")]),      # not Netflix
    (10, 2001, 42, '["Science Fiction", "Mystery"]', "TV-14", [("flatrate", "Disney Plus")]),
    (11, 2023, 35, '["Fantasy", "Animation"]', "TV-Y7-FV", [("flatrate", "Paramount Plus Apple TV Channel")]),
    (12, 1999, 50, '["Western"]', "TV-PG", []),
]
FILMS = [(1, 2018, 95, '["Comedy"]', "PG-13"), (2, 1995, 130, '["Crime", "Drama"]', "R"), (3, 2021, 88, '["Horror"]', None),
         (10, 2012, 100, '["Comedy", "Family"]', "PG")]


def make_db(path):
    c = sqlite3.connect(path)
    c.execute("create table films(tmdb_id integer primary key, imdb_id text, wikidata_id text, title text, year integer, "
              "runtime integer, genres_json text, vote_count integer, release_date text)")
    c.execute("create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text)")
    c.execute("create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, "
              "priority integer, fetched_at text, primary key (tmdb_id, region, kind, provider_id))")
    c.execute("create table tv_shows(tmdb_id integer primary key, imdb_id text, tvdb_id integer, wikidata_id text, name text, "
              "first_year integer, episode_runtime integer, genres_json text, vote_count integer)")
    c.execute("create table tv_ratings(tmdb_id integer, region text, rating text, primary key (tmdb_id, region))")
    c.execute("create table tv_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, "
              "priority integer, primary key (tmdb_id, region, kind, provider_id))")
    for i, (sid, y, rt, g, rating, listings) in enumerate(SHOWS):
        c.execute("insert into tv_shows values (?,?,?,?,?,?,?,?,?)", (sid, None, None, None, "Show %d" % sid, y, rt, g, 500 - i))
        if rating is not None:
            c.execute("insert into tv_ratings values (?,?,?)", (sid, "US", rating))
        c.execute("insert into tv_ratings values (?,?,?)", (sid, "GB", "18"))
        for j, (kind, name) in enumerate(listings):
            c.execute("insert into tv_providers values (?,?,?,?,?,?)", (sid, "US", kind, j + 1, name, j))
        c.execute("insert into tv_providers values (?,?,?,?,?,?)", (sid, "GB", "flatrate", 99, "Netflix", 0))
    for i, (fid, y, rt, g, cert) in enumerate(FILMS):
        c.execute("insert into films values (?,?,?,?,?,?,?,?,?)", (fid, None, None, "Film %d" % fid, y, rt, g, 100 - i, None))
        if cert:
            c.execute("insert into certifications values (?,?,?,?,?)", (fid, "US", cert, 3, None))
    c.commit()
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "facts.db")
        self.c = make_db(self.path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def ids(self, settings, **kw):
        return sorted(PT.matching_show_ids(self.c, TC.tv_plan(settings), **kw))


class TestTvSql(Base):
    def test_every_case_equals_the_plain_python_reference(self):
        shows = TC.load_shows(self.c)
        for case in TC.CASES:
            s = case["settings"]
            ref = sorted(i for i in shows if TC.reference(s, shows[i]))
            self.assertEqual(self.ids(s), ref, case["id"])

    def test_combined_tv_genres_count_as_both_film_genres(self):
        self.assertEqual(self.ids({"only_genres": ["Science Fiction"]}), [2, 6, 8, 10])       # 6, 8: genres unknown pass
        self.assertEqual(self.ids({"only_genres": ["Science Fiction"], "strict": True}), [2, 10])
        self.assertEqual(self.ids({"only_genres": ["Science Fiction", "Fantasy"], "genres_all": True, "strict": True}), [2])
        self.assertEqual(self.ids({"only_genres": ["Family"], "strict": True}), [3])           # Kids = Family
        self.assertEqual(self.ids({"never_genres": ["War"], "strict": True}), [1, 2, 3, 4, 5, 9, 10, 11, 12])
        self.assertEqual(PT.site_genres(["Action & Adventure", "Drama", "Action"]), ["Action", "Adventure", "Drama"])

    def test_unknown_facts_fail_only_in_strict_mode(self):
        self.assertIn(4, self.ids({"min_year": 2000}))
        self.assertNotIn(4, self.ids({"min_year": 2000, "strict": True}))
        self.assertIn(5, self.ids({"max_runtime": 40}))
        self.assertEqual(self.ids({"max_runtime": 40, "strict": True}), [1, 3, 8, 11])

    def test_tv_ratings_under_a_film_rating(self):
        self.assertEqual(self.ids({"max_cert": "G", "strict": True}), [3, 8, 11])
        self.assertEqual(self.ids({"max_cert": "PG", "strict": True}), [3, 5, 8, 11, 12])
        self.assertEqual(self.ids({"max_cert": "PG-13", "strict": True}), [1, 3, 5, 8, 9, 10, 11, 12])
        r = self.ids({"max_cert": "R", "strict": True})
        self.assertIn(7, r)                               # "tv-ma " is read as TV-MA
        self.assertNotIn(6, r)                            # NR is not a TV rating: unknown
        self.assertNotIn(4, r)
        self.assertIn(6, self.ids({"max_cert": "G"}))     # unknown passes in normal mode

    def test_services_use_exact_provider_names_and_the_us_region(self):
        self.assertEqual(self.ids({"services_only": True, "services": ["netflix"]}), [1, 8])   # not rent-only 5, not "Netflixxx" 9
        self.assertEqual(self.ids({"services_only": True, "services": ["paramount", "kanopy"]}), [7, 11])
        self.assertEqual(self.ids({"services_only": True, "services": []}), [1, 2, 3, 6, 7, 8, 9, 10, 11])

    def test_ids_are_per_media_type(self):
        d = TC.tv_plan({}).model_dump()
        d["filters"]["include_media_ids"] = ["tv:2", "movie:1", "tv:999"]
        self.assertEqual(PT.matching_show_ids(self.c, QP.parse(d)), [2])
        d["filters"]["include_media_ids"] = ["movie:1"]
        self.assertEqual(PT.matching_show_ids(self.c, QP.parse(d)), [])            # a film id never selects a show
        d["filters"]["include_media_ids"] = []
        d["filters"]["exclude_media_ids"] = ["movie:1", "tv:3"]
        got = PT.matching_show_ids(self.c, QP.parse(d))
        self.assertIn(1, got)
        self.assertNotIn(3, got)

    def test_order_limit_candidates_and_skip_kinds(self):
        self.assertEqual(PT.matching_show_ids(self.c, TC.tv_plan({}), limit=3), [1, 2, 3])
        self.assertEqual(PT.matching_show_ids(self.c, TC.tv_plan({}), candidate_ids=[12, 2, 77]), [2, 12])
        self.assertNotIn(9, PT.matching_show_ids(self.c, TC.tv_plan({}), skip_kinds=True))
        self.assertIn(9, PT.matching_show_ids(self.c, TC.tv_plan({})))

    def test_values_are_parameters_never_sql_text(self):
        sql, params = PT.compile_tv(TC.tv_plan({"min_year": 2000, "only_genres": ["Comedy"], "max_cert": "PG",
                                                "services_only": True, "services": ["netflix"]}), self.c)
        for v in (2000, "Comedy", "TV-PG", "US", "Netflix"):
            self.assertIn(v, params)
            self.assertNotIn(str(v), sql.replace("'US'", "").replace("'TV-PG'", ""))
        d = TC.tv_plan({}).model_dump()
        d["filters"]["genres_any"] = ["Comedy'; drop table tv_shows; --"]
        with self.assertRaises(ValueError):
            QP.parse(d)
        d["filters"]["genres_any"] = []
        d["filters"]["include_media_ids"] = ["tv:1 or 1=1"]
        with self.assertRaises(ValueError):
            QP.parse(d)
        with self.assertRaises(PS.Unsupported):
            PT.compile_tv(TC.tv_plan({}, ("movie",)))
        self.assertEqual(self.c.execute("select count(*) from tv_shows").fetchone()[0], len(SHOWS))

    def test_mixed_plan_is_films_then_shows_as_media_ids(self):
        s = {"only_genres": ["Comedy"], "strict": True}
        both = PT.matching_media_ids(self.c, TC.tv_plan(s, ("movie", "tv")))
        self.assertEqual(both, ["movie:1", "movie:10", "tv:1", "tv:3"])          # film 1 and show 1 are different things
        self.assertEqual(len(both), len(set(both)))
        self.assertEqual(PT.matching_media_ids(self.c, TC.tv_plan(s, ("tv",))), ["tv:1", "tv:3"])
        self.assertEqual(PT.explain(TC.tv_plan({"max_cert": "PG"}))["tv_ratings_allowed"][-1], "TV-PG")

    def test_conformance_tool_passes_on_the_made_up_db(self):
        out = os.path.join(self.tmp.name, "conf")
        argv = sys.argv
        sys.argv = ["x", "--db", self.path, "--out", out]
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                rc = TC.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        res = json.load(open(out + ".json"))
        self.assertEqual(res["rule"], "PASS")
        self.assertEqual(res["shows"], len(SHOWS))
        self.assertEqual(len(res["cases"]), len(TC.CASES))


class TestAdapter(Base):
    def reload(self, on, log):
        os.environ["QUERY_PLAN_SHADOW"] = "1" if on else "0"
        os.environ["PLAN_SHADOW_LOG"] = log
        importlib.reload(PA)

    def tearDown(self):
        os.environ.pop("QUERY_PLAN_SHADOW", None)
        os.environ.pop("PLAN_SHADOW_LOG", None)
        importlib.reload(PA)
        super().tearDown()

    def test_decide_keeps_order_and_separates_films_from_shows(self):
        cands = ["tv:3", "movie:1", "tv:1", "movie:2", "nonsense", "7", "tv:999"]
        d = PA.decide(self.c, {"only_genres": ["Comedy"], "strict": True}, cands)
        self.assertEqual(d["kept"], ["tv:3", "movie:1", "tv:1"])
        self.assertEqual(d["removed"], ["movie:2", "tv:999"])
        self.assertEqual(d["not_ids"], ["nonsense", "7"])                         # a bare number is not an identity
        self.assertEqual(d["unknown_identity"], [])                               # identity tables not built: not checked

    def test_ids_without_an_identity_are_never_kept_once_the_tables_exist(self):
        MI.build(self.c)
        d = PA.decide(self.c, {}, ["movie:1", "tv:1", "movie:4242", "tv:4242"])
        self.assertEqual(d["kept"], ["movie:1", "tv:1"])
        self.assertEqual(d["unknown_identity"], ["movie:4242", "tv:4242"])

    def test_shadow_is_silent_while_the_switch_is_off(self):
        log = os.path.join(self.tmp.name, "shadow.jsonl")
        self.reload(False, log)
        self.assertIsNone(PA.shadow(self.c, {"max_cert": "PG"}, ["movie:1"], ["movie:1"]))
        self.assertFalse(os.path.exists(log))

    def test_shadow_records_differences_without_text_or_user(self):
        log = os.path.join(self.tmp.name, "logs", "shadow.jsonl")
        self.reload(True, log)
        s = {"max_cert": "PG", "strict": True, "name": "Ada Lovelace", "favourite": "The Secret Film"}
        cands = ["movie:1", "movie:10", "tv:3", "tv:2"]
        r = PA.shadow(self.c, s, cands, ["movie:1", "movie:10", "tv:3"], where="pick_tonight")
        self.assertFalse(r["agree"])
        self.assertEqual(r["only_site"], ["movie:1"])                             # PG-13 film the site path kept
        self.assertEqual(r["only_plan"], [])
        self.assertEqual((r["n"], r["kept_site"], r["kept_plan"]), (4, 3, 2))
        r2 = PA.shadow(self.c, s, cands, ["movie:10", "tv:3"], where="pick_tonight")
        self.assertTrue(r2["agree"])
        text = open(log).read()
        self.assertEqual(len(text.strip().split("\n")), 2)
        for secret in ("Ada", "Secret Film", "favourite", "Film 1", "Show 3"):
            self.assertNotIn(secret, text)
        self.assertEqual(set(json.loads(text.split("\n")[0])),
                         {"at", "where", "limits", "n", "kept_site", "kept_plan", "agree", "only_plan", "only_site",
                          "n_only_plan", "n_only_site", "not_ids", "unknown_identity"})
        self.assertEqual(PA.summary(log), {"comparisons": 2, "agree": 1, "by_page": {"pick_tonight": {"n": 2, "differ": 1}}})

    def test_shadow_never_raises_and_the_limits_hash_ignores_personal_fields(self):
        log = os.path.join(self.tmp.name, "shadow.jsonl")
        self.reload(True, log)
        self.assertIsNone(PA.shadow(self.c, {"max_cert": "XXX"}, ["movie:1"], ["movie:1"]))   # bad limit: reported, no raise
        self.assertEqual(PA.limits_hash({"max_cert": "PG", "name": "A"}), PA.limits_hash({"max_cert": "PG", "name": "B"}))
        self.assertNotEqual(PA.limits_hash({"max_cert": "PG"}), PA.limits_hash({"max_cert": "R"}))
        self.assertEqual(PA.limits_of({"strict": False, "services": [], "min_year": 1990}), {"min_year": 1990})


if __name__ == "__main__":
    unittest.main()
