"""R1: one unit test per connector on a saved sample (tests/samples). No network, no databases.

    python -m connectors test        (or: python -m unittest tests.test_connectors -v)
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
S = os.path.join(HERE, "tests", "samples")


def sample(name, as_json=True):
    with open(os.path.join(S, name), encoding="utf-8") as f:
        return json.load(f) if as_json else f.read()


class Connectors(unittest.TestCase):
    def test_tmdb_us_theatrical(self):
        import fetch_tmdb
        self.assertEqual(fetch_tmdb.us_theatrical(sample("tmdb_release_dates.json")), "2023-07-21")

    def test_tv_rows(self):
        import v7_data
        show, seasons, credits, kws, vids, rats, provs, revs = v7_data.tv_rows(1396, sample("tmdb_tv.json"), ["US"])
        self.assertEqual(show[1], "tt0903747")
        self.assertEqual((show[13], show[14]), (5, 62))      # seasons, episodes
        self.assertEqual(show[15], 46)                       # mean episode runtime
        self.assertTrue(any(c[4] == "Creator" and c[2] == "Vince Gilligan" for c in credits))
        self.assertEqual(rats, [(1396, "US", "TV-MA")])
        self.assertEqual(provs[0][4], "Netflix")
        self.assertEqual(revs[0][0], "tv:r1")

    def test_reception_sections_and_critics(self):
        import critics
        import v7_data
        secs = v7_data.reception_sections(sample("reception.wikitext", False))
        kinds = [s[0] for s in secs]
        self.assertIn("reception", kinds)
        self.assertIn("box_office", kinds)
        rec = next(s for s in secs if s[0] == "reception")
        st = critics.statements(rec[3], rec[4], "reception")
        crit = {s["outlet"]: s for s in st if s["kind"] == "critic"}
        self.assertIn("Chicago Sun-Times", crit)
        self.assertEqual(crit["Chicago Sun-Times"]["critic"], "Roger Ebert")
        self.assertIn("Variety", crit)
        self.assertEqual(crit["Variety"]["critic"], "Justin Chang")
        self.assertTrue(any(s["kind"] == "aggregator" for s in st))
        legacy = next(s for s in secs if s[0] == "legacy")
        self.assertIn("BOM", legacy[4])                      # a named ref defined elsewhere on the page

    def test_parse_score(self):
        import v7_data
        self.assertEqual(v7_data.parse_score("93%"), (93.0, 100.0))
        self.assertEqual(v7_data.parse_score("8.6/10"), (8.6, 10.0))
        self.assertEqual(v7_data.parse_score("A-"), (None, None))

    def test_imdb_tsv(self):
        import v7_data
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "r.tsv.gz")
            with open(os.path.join(S, "imdb_ratings.tsv"), "rb") as src, gzip.open(p, "wb") as dst:
                shutil.copyfileobj(src, dst)
            rows = list(v7_data.tsv_rows(p, {"tt1375666"}))
            self.assertEqual(rows, [["tt1375666", "8.8", "2600000"]])
        finally:
            shutil.rmtree(d)

    def test_movielens_dist(self):
        import v7_data
        hist = [0, 0, 0, 0, 0, 0, 0, 0, 0, 10]                # ten 5-star ratings
        mean, sd, bayes, low, high, bc = v7_data.dist_stats(hist, 10, 3.5)
        self.assertAlmostEqual(mean, 5.0)
        self.assertAlmostEqual(sd, 0.0)
        self.assertAlmostEqual(high, 1.0)
        self.assertTrue(3.5 < bayes < 5.0)

    def test_omdb_num(self):
        import fetch_free_data as FF
        self.assertEqual(FF._num("93%", pct=True), 93.0)
        self.assertEqual(FF._num("$1,234,567"), 1234567.0)
        self.assertIsNone(FF._num("N/A"))

    def test_guardian_title(self):
        import fetch_free_data as FF
        self.assertEqual(FF.guardian_title("Inception review – a dazzling dream"), "Inception")
        self.assertEqual(FF.guardian_title("Film review: Heat"), "Heat")

    def test_nyt_titles(self):
        import fetch_free_data as FF
        self.assertIn("Inception", FF.nyt_titles(sample("nyt_doc.json")))

    def test_youtube_match_and_chapters(self):
        import youtube_analysis as Y
        idx = {"inception": [(27205, 2010, 35000)], "the dark knight": [(155, 2008, 30000)],
               "the dark knight rises": [(49026, 2012, 20000)]}
        self.assertEqual(Y.match_films("Inception - Dream Logic Explained", "", idx)[0][0], 27205)
        self.assertEqual(Y.match_films("Why The Dark Knight Rises Fails", "", idx)[0][0], 49026)
        ch = Y.chapters(sample("youtube_description.txt", False))
        self.assertEqual(ch[1], (135, "The spinning top"))
        self.assertNotIn("patreon", Y.clean_desc(sample("youtube_description.txt", False)).lower())

    def test_letterboxd_not_fetched(self):
        from connectors import letterboxd
        self.assertFalse(letterboxd.APPROVED)
        with self.assertRaises(SystemExit):
            letterboxd.fetch()


if __name__ == "__main__":
    unittest.main()
