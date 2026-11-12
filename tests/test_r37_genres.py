"""Round 37 (12 Nov): GENRE_MAIN variants 2 and 3 (web/genres.py).   python -m unittest tests.test_r37_genres"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(HERE, "web"), HERE]
import genres as G  # noqa: E402

STAR_WARS = (["Adventure", "Action", "Science Fiction"], "Action,Adventure,Fantasy")
INSIDE_OUT = (["Animation", "Family", "Adventure", "Drama", "Comedy"], "Adventure,Animation,Comedy")
GUMP = (["Comedy", "Drama", "Romance"], "Drama,Romance")
KNIVES = (["Comedy", "Crime", "Mystery"], "Comedy,Crime,Drama")
PULP = (["Thriller", "Crime"], "Crime,Drama")


def main(film, v):
    return G.film_split(film[0], film[1], v)[0]


class Variants(unittest.TestCase):
    def test_v1_needs_imdb_for_every_genre(self):
        self.assertNotIn("Science Fiction", main(STAR_WARS, "1"))
        self.assertNotIn("Mystery", main(KNIVES, "1"))

    def test_v2_keeps_specific_genres(self):
        self.assertIn("Science Fiction", main(STAR_WARS, "2"))
        self.assertIn("Mystery", main(KNIVES, "2"))
        self.assertNotIn("Drama", main(INSIDE_OUT, "2"))
        self.assertNotIn("Comedy", main(GUMP, "2"))
        self.assertNotIn("Thriller", main(PULP, "2"))
        self.assertIn("Family", main(INSIDE_OUT, "2"))

    def test_v3_keeps_tmdb_first_genre(self):
        self.assertIn("Comedy", main(GUMP, "3"))
        self.assertIn("Thriller", main(PULP, "3"))
        self.assertNotIn("Drama", main(INSIDE_OUT, "3"))

    def test_no_imdb_genres_keeps_tmdb(self):
        for v in "123":
            self.assertEqual(G.film_split(["Drama", "Comedy"], None, v)[0], ["Drama", "Comedy"])
            self.assertEqual(G.film_split(["Drama", "Comedy"], "\\N", v)[0], ["Drama", "Comedy"])

    def test_switch_values(self):
        self.assertIn(G.VARIANT, ("0", "1", "2", "3", ""))
        self.assertEqual(G.ON, G.VARIANT in ("1", "2", "3"))


if __name__ == "__main__":
    unittest.main()
