"""v8.1 slice 2.1 (13 Nov 2026): query plan validation and the SQL it compiles to, on a small made-up facts.db."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import plan_sql as PS  # noqa: E402
import query_plan as QP  # noqa: E402

KIT = os.path.join(ROOT, "planning", "cinemack-v8.1")


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def jtext(path):
    with open(path) as fh:
        return fh.read()
EXAMPLE = os.path.join(KIT, "examples", "example_movie_query.json")

# id, year, runtime, genres_json, US certs [(cert, release_type)], US listings [(kind, provider)]
FILMS = [
    (1, 2018, 95, '["Comedy", "Romance"]', [("PG-13", 3)], [("flatrate", "Netflix")]),
    (2, 1995, 130, '["Crime", "Drama"]', [("R", 3)], [("flatrate", "Max Amazon Channel")]),
    (3, 2021, 88, '["Horror", "Comedy"]', [("R", 3)], [("rent", "Netflix")]),
    (4, None, 100, '["Comedy"]', [], []),                                   # no year, no rating
    (5, 2010, None, '["Animation", "Family"]', [("G", 3), ("PG", 4)], [("ads", "Tubi TV")]),
    (6, 2012, 0, None, [("PG", 2)], []),                                    # no runtime, no genres
    (7, 2019, 112, '["Science Fiction"]', [("NR", 3), ("PG-13", 4)], [("free", "Kanopy")]),
    (8, 2005, 101, 'not json', [("PG", 3)], [("flatrate", "Hulu"), ("flatrate", "Netflix Standard with Ads")]),
    (9, 2017, 99, '["Romance"]', [("R", 3)], [("flatrate", "Netflixxx")]),  # not Netflix
    (10, 2016, 140, '["Drama", "Romance", "Comedy"]', [("PG-13", 3)], [("flatrate", "Paramount Plus Apple TV Channel")]),
]


def make_db(path):
    c = sqlite3.connect(path)
    c.execute("create table films(tmdb_id integer primary key, year integer, runtime integer, genres_json text, vote_count integer)")
    c.execute("create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text)")
    c.execute("create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, "
              "priority integer, fetched_at text, primary key (tmdb_id, region, kind, provider_id))")
    for i, (fid, y, rt, g, certs, listings) in enumerate(FILMS):
        c.execute("insert into films values (?,?,?,?,?)", (fid, y, rt, g, 100 - i))
        for cert, t in certs:
            c.execute("insert into certifications values (?,?,?,?,?)", (fid, "US", cert, t, None))
        c.execute("insert into certifications values (?,?,?,?,?)", (fid, "GB", "18", 3, None))
        for j, (kind, name) in enumerate(listings):
            c.execute("insert into watch_providers values (?,?,?,?,?,?,?)", (fid, "US", kind, j + 1, name, j, None))
        if fid % 2:
            c.execute("insert into watch_providers values (?,?,?,?,?,?,?)", (fid, "GB", "flatrate", 99, "Netflix", 0, None))
    c.commit()
    return c


def plan(**filters):
    base = jload((EXAMPLE))
    f = {k: (None if not isinstance(v, (list, bool)) else ([] if isinstance(v, list) else False))
         for k, v in base["filters"].items()}
    f.update(filters)
    base["filters"] = f
    if "certification_max" in filters or "unknown" in filters:
        base["schema_version"] = "1.1"
    return QP.parse(base)


class TestValidation(unittest.TestCase):
    def test_kit_example_is_valid(self):
        p = QP.parse(jtext(EXAMPLE))
        self.assertEqual(p.filters.genres_any, ["Science Fiction"])      # "science fiction" -> canonical name

    def test_unknown_fields_rejected(self):
        d = jload((EXAMPLE))
        d["sql"] = "select * from users"
        with self.assertRaises(ValueError):
            QP.parse(d)
        d = jload((EXAMPLE))
        d["filters"]["where"] = "1=1"
        with self.assertRaises(ValueError):
            QP.parse(d)

    def test_bad_values_rejected(self):
        for path, value in ((("intent",), "drop_table"), (("filters", "year_min"), 1700),
                            (("filters", "genres_any"), ["Horror'); drop table films; --"]),
                            (("filters", "watch_provider_ids_any"), ["netflix", "evil"]),
                            (("filters", "watch_region"), "usa"), (("retrieval", "max_candidates"), 5000),
                            (("filters", "exclude_media_ids"), ["1 or 1=1"]), (("schema_version",), 2),
                            (("filters", "year_min"), "2016"), (("media_types",), [])):
            d = jload((EXAMPLE))
            t = d
            for k in path[:-1]:
                t = t[k]
            t[path[-1]] = value
            with self.assertRaises(ValueError, msg=f"{path}={value!r}"):
                QP.parse(d)

    def test_contradictions_rejected(self):
        with self.assertRaises(ValueError):
            plan(year_min=2020, year_max=2010)
        with self.assertRaises(ValueError):
            plan(genres_any=["Horror"], genres_exclude=["horror"])
        with self.assertRaises(ValueError):
            plan(watch_provider_ids_any=["netflix"], watch_region=None)
        d = jload((EXAMPLE))
        d["intent"], d["media_types"] = "forecast", ["tv"]
        with self.assertRaises(ValueError):
            QP.parse(d)

    def test_extension_needs_version_1_1(self):
        d = jload((EXAMPLE))
        d["filters"]["certification_max"] = "PG"
        with self.assertRaises(ValueError):
            QP.parse(d)
        d["schema_version"] = "1.1"
        self.assertEqual(QP.parse(d).filters.certification_max, "PG")

    def test_matches_kit_json_schema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        v1 = jload((os.path.join(KIT, "specs", "movie_query.schema.json")))
        v11 = jload((os.path.join(KIT, "specs", "movie_query.v1_1.schema.json")))
        ex = jload((EXAMPLE))
        jsonschema.validate(ex, v1)
        jsonschema.validate(ex, v11)
        p = QP.from_settings({"max_cert": "PG", "min_year": 2000, "only_genres": ["Comedy"], "strict": True})
        jsonschema.validate(json.loads(p.model_dump_json()), v11)
        bad = dict(ex, filters=dict(ex["filters"], certification_max="PG"))
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, v11)


class TestSQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.c = make_db(os.path.join(cls.tmp, "facts.db"))

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def ids(self, p, cand=None):
        return set(PS.matching_ids(self.c, p, candidate_ids=cand))

    def test_years_strict_and_lenient(self):
        self.assertEqual(self.ids(plan(year_min=2016, unknown="fail")), {1, 3, 7, 9, 10})
        self.assertEqual(self.ids(plan(year_min=2016, unknown="pass")), {1, 3, 4, 7, 9, 10})
        self.assertEqual(self.ids(plan(year_min=2016)), {1, 3, 7, 9, 10})             # v1 default = fail

    def test_runtime_zero_is_unknown(self):
        self.assertEqual(self.ids(plan(runtime_max_minutes=100, unknown="fail")), {1, 3, 4, 9})
        self.assertEqual(self.ids(plan(runtime_max_minutes=100, unknown="pass")), {1, 3, 4, 5, 6, 9})

    def test_genres_any_all_exclude_exact(self):
        self.assertEqual(self.ids(plan(genres_any=["Comedy"])), {1, 3, 4, 10})
        self.assertEqual(self.ids(plan(genres_all=["Comedy", "Romance"])), {1, 10})
        self.assertEqual(self.ids(plan(genres_exclude=["Horror"], unknown="fail")), {1, 2, 4, 5, 7, 9, 10})
        self.assertEqual(self.ids(plan(genres_exclude=["Horror"], unknown="pass")), {1, 2, 4, 5, 6, 7, 8, 9, 10})
        self.assertEqual(self.ids(plan(genres_any=["Science Fiction"])), {7})       # "Fiction" alone matches nothing
        self.assertEqual(self.ids(plan(genres_any=["Drama"], genres_exclude=["Comedy"])), {2})

    def test_certificate_choice(self):
        # film 5: theatrical G wins over the later PG; film 7: "NR" is ignored, PG-13 counts; film 6: PG (type 2)
        self.assertEqual(self.ids(plan(certification_max="PG", unknown="fail")), {5, 6, 8})
        self.assertEqual(self.ids(plan(certification_max="PG", unknown="pass")), {4, 5, 6, 8})
        self.assertEqual(self.ids(plan(certification_max="G", unknown="fail")), {5})

    def test_services(self):
        p = plan(watch_region="US", watch_provider_ids_any=["netflix"], watch_monetization=["flatrate", "free", "ads"])
        self.assertEqual(self.ids(p), {1, 8})                                     # not rent (3), not "Netflixxx" (9)
        p = plan(watch_region="US", watch_provider_ids_any=["max", "paramount"], watch_monetization=[])
        self.assertEqual(self.ids(p), {2, 10})                                    # default kinds
        p = plan(watch_region="GB", watch_provider_ids_any=["netflix"], watch_monetization=["flatrate"])
        self.assertEqual(self.ids(p), {1, 3, 5, 7, 9})                            # region is respected
        p = plan(watch_region="US", watch_provider_ids_any=["mubi"], watch_monetization=[])
        self.assertEqual(self.ids(p), set())

    def test_ids_and_candidates(self):
        p = plan(include_media_ids=["movie:1", "movie:2", "movie:3"], exclude_media_ids=["movie:4"])
        self.assertEqual(self.ids(p), {1, 2, 3})
        self.assertEqual(self.ids(plan(genres_any=["Comedy"], exclude_media_ids=["movie:1", "movie:4"])), {3, 10})
        with self.assertRaises(ValueError):                                       # both lists: contradiction
            plan(include_media_ids=["movie:1"], exclude_media_ids=["movie:1"])
        self.assertEqual(self.ids(plan(genres_any=["Comedy"]), cand=[1, 2, 3, 99]), {1, 3})
        self.assertEqual(PS.matching_ids(self.c, plan(genres_any=["Comedy"])), [1, 3, 4, 10])   # most-voted first

    def test_sql_has_no_values_inline(self):
        p = plan(genres_any=["Comedy"], year_min=2001, certification_max="PG-13",
                 watch_region="US", watch_provider_ids_any=["netflix"], watch_monetization=["flatrate"],
                 exclude_media_ids=["movie:424242"])
        sql, params = PS.compile_plan(p, self.c)
        for v in ("Comedy", "2001", "424242", "Netflix", "flatrate"):
            self.assertNotIn(v, sql)
        self.assertIn("Comedy", params)
        self.assertEqual(sql.count("?"), len(params))

    def test_tv_plans_unsupported(self):
        d = jload((EXAMPLE))
        d["media_types"] = ["tv"]
        with self.assertRaises(PS.Unsupported):
            PS.compile_plan(QP.parse(d), self.c)

    def test_from_settings_matches_reference(self):
        import filter_conformance as FC
        facts = FC.load_facts(self.c)
        for s in jload((os.path.join(ROOT, "sets", "v81_filters.json")))["cases"]:
            ref = {i for i, f in facts.items() if FC.reference(s["settings"], f)}
            got = self.ids(QP.from_settings(s["settings"]))
            self.assertEqual(got, ref, s["id"])


if __name__ == "__main__":
    unittest.main()
