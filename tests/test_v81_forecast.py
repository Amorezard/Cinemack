"""v8.1 slices 4.3 / 4.5 (13 Nov 2026): as-of rules (leak probe), trailer cutoffs, forecast responses. No database."""
import datetime
import json
import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import boxoffice_asof as A  # noqa: E402
import performance as P  # noqa: E402
import v81_forecast as V  # noqa: E402

D = datetime.date


class TestAsOf(unittest.TestCase):
    def test_pf4_inputs_allowed_before_release(self):
        for h in ("d90", "d7", "d0"):
            self.assertEqual(A.check(P.BEFORE + P.TRAILER, h), P.BEFORE + P.TRAILER)

    def test_leak_probe_post_release_inputs_rejected(self):
        for col in ("rating", "imdb_rating", "rt_pct", "awards_won", "lb_rating", "news_resonance", "log_votes"):
            for h in ("d90", "d7", "d0"):
                with self.assertRaises(A.AsOfError, msg=f"{col} at {h}"):
                    A.check(P.BEFORE + [col], h)

    def test_release_week_inputs_only_at_d0(self):
        with self.assertRaises(A.AsOfError):
            A.check(["log_pv_pre28"], "d7")
        self.assertEqual(A.check(["log_pv_pre28"], "d0"), ["log_pv_pre28"])

    def test_unregistered_input_rejected(self):
        with self.assertRaises(A.AsOfError):
            A.check(P.BEFORE + ["actual_gross_seeded_leak"], "d90")
        with self.assertRaises(A.AsOfError):
            A.check(P.BEFORE, "d30")

    def test_trailers_counted_before_cutoff_only(self):
        rows = [{"tmdb_id": 1, "_date": D(2018, 6, 1)}, {"tmdb_id": 2, "_date": D(2004, 6, 1)}, {"tmdb_id": 3, "_date": D(2019, 1, 1)}]
        vids = {1: [D(2018, 1, 1), D(2018, 3, 5), D(2018, 5, 28), D(2018, 6, 2)], 2: [D(2004, 1, 1)]}
        d90 = {r["tmdb_id"]: r for r in A.trailer_features(rows, vids, "d90")}
        d7 = {r["tmdb_id"]: r for r in A.trailer_features(rows, vids, "d7")}
        d0 = {r["tmdb_id"]: r for r in A.trailer_features(rows, vids, "d0")}
        self.assertEqual(d90[1]["trailer_n_pre"], 1.0)          # cutoff 3 Mar: only 1 Jan
        self.assertEqual(d7[1]["trailer_n_pre"], 2.0)           # cutoff 25 May
        self.assertEqual(d0[1]["trailer_n_pre"], 3.0)           # before 1 Jun (never the 2 Jun one)
        self.assertEqual(d90[1]["trailer_lead_days"], (D(2018, 6, 1) - D(2018, 1, 1)).days)
        self.assertTrue(math.isnan(d90[2]["trailer_n_pre"]))    # before 2007: unknown, as in pf4
        self.assertEqual(d7[3]["trailer_n_pre"], 0.0)
        self.assertTrue(math.isnan(d7[3]["trailer_lead_days"]))
        self.assertNotIn("_cutoff", rows[0])                     # input rows untouched


class TestResponse(unittest.TestCase):
    def make(self):
        r = {"tmdb_id": 42, "_date": D(2018, 6, 1), "_log_budget_nominal": math.log(50e6)}
        return V.response(r, "d7", math.log(2.5), {0.5: (math.log(1.5), math.log(4)), 0.8: (math.log(0.7), math.log(9))}, "tmdb")

    def test_shape_and_numbers(self):
        d = self.make()
        self.assertEqual(V.validate_response(d), [])
        self.assertAlmostEqual(d["worldwide_gross"]["point"], 125e6, delta=1)
        self.assertAlmostEqual(d["gross_to_budget_multiple"]["point"], 2.5)
        self.assertEqual(d["domestic_gross"], {"point": None, "intervals": []})
        self.assertEqual(d["international_gross"], {"point": None, "intervals": []})
        self.assertEqual(d["as_of"], "2018-05-25T12:00:00Z")
        lo80 = [i for i in d["worldwide_gross"]["intervals"] if i["nominal_coverage"] == 0.8][0]
        self.assertAlmostEqual(lo80["lower"], 35e6, delta=1)
        self.assertTrue(any("not profit" in w for w in d["warnings"]))

    def test_checks_catch_bad_output(self):
        d = self.make()
        d["warnings"] = d["warnings"] + ["Expected profit: $75M"]
        self.assertIn("profit / ROI wording", V.validate_response(d))
        d = self.make()
        d["worldwide_gross"]["intervals"][0]["lower"] = 9e12
        self.assertTrue(V.validate_response(d))
        d = self.make()
        d["extra"] = 1
        self.assertTrue(V.validate_response(d))

    def test_kit_schema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(os.path.join(ROOT, "planning", "cinemack-v8.1", "specs", "prediction_response.schema.json")) as fh:
            schema = json.load(fh)
        jsonschema.validate(self.make(), schema)

    def test_metrics(self):
        y = np.array([0.0, 1.0, 2.0, 3.0])
        p = np.array([0.5, 1.0, 1.0, 3.5])
        m = V.metrics(y, p, {0.5: (y - 0.1, y + 0.1), 0.8: (np.array([1, 0, 0, 0.0]), np.array([2, 2, 2, 2.0]))})
        self.assertAlmostEqual(m["mae_log"], 0.5)
        self.assertEqual(m["cov50"], 1.0)
        self.assertEqual(m["cov80"], 0.5)
        self.assertEqual(m["below80"], 0.25)
        self.assertEqual(m["above80"], 0.25)


if __name__ == "__main__":
    unittest.main()
