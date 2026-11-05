"""1 Nov fixes, no database or model needed:  python -m unittest tests.test_v7_nov1_fixes"""
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


class TVFilmTitleGuard(unittest.TestCase):
    def setUp(self):
        import tv
        self.tv = tv
        self.saved = dict(tv._IDX)
        n = tv.norm
        tv._IDX["shows"] = [(n(t), i, t, 2000, v) for i, (t, v) in enumerate(
            [("Dark", 3000), ("Paranormal", 120), ("Breaking Bad", 15000), ("The Office", 5000), ("Friends", 9000)])]
        tv._IDX["films"] = {n("The Dark Knight"), n("Paranormal Activity")}
        tv._IDX["all_films"] = {n("The Dark Knight"), n("Paranormal Activity"), n("El Camino: A Breaking Bad Movie")}

    def tearDown(self):
        self.tv._IDX.clear()
        self.tv._IDX.update(self.saved)

    def name(self, q):
        r = self.tv.named_show(q)
        return r[2] if r else None

    def test_film_titles_are_not_shows(self):
        self.assertIsNone(self.name("Where was The Dark Knight filmed?"))
        self.assertIsNone(self.name("Why did Paranormal Activity make so much money?"))

    def test_shows_still_named(self):
        self.assertEqual(self.name("Who created Breaking Bad?"), "Breaking Bad")
        self.assertEqual(self.name("How many seasons does Dark have?"), "Dark")
        self.assertEqual(self.name("Where can I stream The Office?"), "The Office")
        self.assertEqual(self.name("How many episodes of Friends are there?"), "Friends")


class ShortFilmWords(unittest.TestCase):
    def setUp(self):
        import shorts
        self.sh = shorts
        self.saved = shorts.SHORTS
        shorts.SHORTS = True

    def tearDown(self):
        self.sh.SHORTS = self.saved

    def test_short_questions(self):
        for q in ("What's the Pixar short about a dumpling that comes to life?", "the animated short film where a "
                  "bird is scared of the waves", "Who directed the short film Bao?", "any good Pixar shorts?",
                  "the short that played before Inside Out"):
            self.assertTrue(self.sh.wants_short(q), q)

    def test_not_short_questions(self):
        for q in ("a movie based on a short story by Stephen King", "in short, who directed it",
                  "a short miniseries I can finish in a weekend", "long story short I forgot the title",
                  "Where was The Dark Knight filmed?", "a film about a short man who becomes king"):
            self.assertFalse(self.sh.wants_short(q), q)

    def test_off_means_off(self):
        self.sh.SHORTS = False
        self.assertFalse(self.sh.wants_short("Who directed the short film Bao?"))
        self.assertIsNone(self.sh.label(123))

    def test_recommendation_words(self):
        self.assertTrue(self.sh.R_REC.search("any good Pixar shorts to show my kids?"))
        self.assertFalse(self.sh.R_REC.search("did Bao win the oscar for best animated short"))


class ReceptionGapNeedsKnownCount(unittest.TestCase):
    def make(self, n):
        c = sqlite3.connect(":memory:")
        c.executescript("""
          create table critic_scores(tmdb_id, rt_pct, rt_n, rt_avg, mc_score, mc_n, cinemascore, snapshot, mc_text);
          create table wikidata_scores(tmdb_id, source, score_text, value, scale, n_reviews, as_of);
          create table omdb(tmdb_id, rt_pct, mc_score, fetched_at, status);""")
        c.execute("insert into wikidata_scores values (1, 'Rotten Tomatoes', '100%', 100, 100, ?, '2026-10-29')", (n,))
        return c

    def test_unknown_and_small_counts_give_no_gap_score(self):
        import reception_cards as RC
        self.assertEqual(RC.critic_scores(self.make(None), 1)[1], (None, None))
        self.assertEqual(RC.critic_scores(self.make(7), 1)[1], (None, None))
        self.assertEqual(RC.critic_scores(self.make(150), 1)[1], (100.0, "Rotten Tomatoes %"))


if __name__ == "__main__":
    unittest.main()
