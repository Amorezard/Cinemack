"""v8.1 slice 3.4 (15 Nov 2026): cache keys and the same-meaning rule, without a model.

Three questions (kit acceptance rows on caches): can one person's answer be replayed to another; can an answer made
under one set of limits be replayed under another; can a question and its negation share an answer. The embedding
similarity is forced to 1.0 here, the worst case: the word / entity rules alone have to tell the pairs apart."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MOVIE_BUFF_MOCK", "1")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
import semcache as SC  # noqa: E402
import site_cache as C  # noqa: E402
import user_prefs as UP  # noqa: E402

NEGATIONS = [
    ("a sci-fi movie without aliens", "a sci-fi movie with aliens"),
    ("movies that are not scary", "movies that are scary"),
    ("comedies that aren't romantic", "comedies that are romantic"),
    ("films that don't have subtitles", "films that have subtitles"),
    ("thrillers that never show gore", "thrillers that show gore"),
    ("a non-violent western", "a violent western"),
    ("movies with no jump scares", "movies with jump scares"),
    ("horror films excluding sequels", "horror films including sequels"),
    ("dramas nobody dies in", "dramas somebody dies in"),
    ("something unlike Inception", "something like Inception"),
    ("Is Heat rated R?", "Is Heat not rated R?"),
    ("comedies minus Adam Sandler", "comedies with Adam Sandler"),
    ("movies I haven't seen with Tom Hanks", "movies I have seen with Tom Hanks"),
    ("anything but a musical", "a musical"),
    ("neither scary nor sad movies", "scary and sad movies"),
    ("films rated below PG-13", "films rated above PG-13"),
    ("least violent war films", "most violent war films"),
    ("movies under two hours", "movies over two hours"),
    ("worst Nolan film", "best Nolan film"),
    ("a family film, no animation", "a family film, animation"),
]
LIMITS = [("max_cert", "PG"), ("max_cert", "R"), ("max_runtime", 100), ("max_runtime", 120), ("min_year", 1990), ("max_year", 2000),
          ("never_genres", ["Horror"]), ("never_genres", ["Horror", "War"]), ("only_genres", ["Comedy"]), ("services", ["netflix"]),
          ("services_only", True), ("genres_all", True), ("strict", True), ("mood", "funny")]


class TestSameMeaning(unittest.TestCase):
    def test_a_question_and_its_negation_never_share_an_answer(self):
        wrong = [(a, b) for a, b in NEGATIONS if SC.same_meaning(a, b, 1.0) or SC.same_meaning(b, a, 1.0)]
        self.assertEqual(wrong, [])

    def test_the_sites_own_near_miss_list_holds_at_similarity_one(self):
        wrong = [(a, b) for a, b in SC.NEAR_MISS if SC.same_meaning(a, b, 1.0)]
        self.assertEqual(wrong, [])

    def test_only_trivial_rewordings_are_reused_and_never_below_the_threshold(self):
        self.assertTrue(SC.same_meaning("Who directed Jaws?", "Who directed Jaws", 1.0))
        self.assertTrue(SC.same_meaning("Who directed Jaws?", "who was the director of Jaws", 0.96))
        self.assertFalse(SC.same_meaning("Who directed Jaws?", "who directed jaws", 1.0))   # no capital: the title is not seen; a miss, never a wrong reuse
        self.assertFalse(SC.same_meaning("Who directed Jaws?", "Who directed Jaws", SC.SEM_MIN - 0.001))
        self.assertFalse(SC.same_meaning("", "", 1.0))                     # nothing to compare: no reuse

    def test_lookup_stays_inside_one_tag(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        tmp = tempfile.TemporaryDirectory()
        old = (SC.SEMCACHE, SC.PATH, SC._C["c"], SC._embed)
        try:
            SC.SEMCACHE, SC._C["c"] = True, None
            SC.PATH = type(SC.PATH)(os.path.join(tmp.name, "sem.sqlite"))
            SC._embed = lambda text: np.ones(4, dtype=np.float32) / 2.0      # every question "identical": worst case
            SC.add("recommend a comedy", "user-a|fPG", "answer-of-a")
            self.assertEqual(SC.lookup("recommend a comedy", "user-a|fPG"), "answer-of-a")
            self.assertIsNone(SC.lookup("recommend a comedy", "user-b|fPG"))  # another person
            self.assertIsNone(SC.lookup("recommend a comedy", "user-a|fR"))   # the same person, other limits
            self.assertIsNone(SC.lookup("recommend a comedy", ""))
            self.assertIsNone(SC.lookup("recommend a horror film", "user-a|fPG"))
        finally:
            if SC._C["c"] is not None:
                SC._C["c"].close()
            SC.SEMCACHE, SC.PATH, SC._C["c"], SC._embed = old
            tmp.cleanup()


class TestKeys(unittest.TestCase):
    def test_every_limit_changes_the_search_key(self):
        base = dict(UP.DEFAULTS)
        self.assertEqual(UP.filter_tag(base), "")
        self.assertEqual(UP.filter_tag(None), "")
        tags = {}
        for k, v in LIMITS:
            t = UP.filter_tag(dict(base, **{k: v}))
            self.assertTrue(t, f"{k}={v!r} does not change the search-cache key")
            tags[(k, str(v))] = t
        self.assertEqual(len(set(tags.values())), len(tags), "two different limits share a search-cache key")

    def test_strict_mode_keys_the_search(self):
        s = dict(UP.DEFAULTS, max_cert="PG", max_runtime=100)
        self.assertNotEqual(UP.filter_tag(s), UP.filter_tag(dict(s, strict=True)))
        self.assertEqual(UP.filter_tag(s), UP.filter_tag(dict(s, strict=False)))   # off = the key it always had
        self.assertNotEqual(UP.tag(s), UP.tag(dict(s, strict=True)))               # the answer key already had it

    def test_hiding_money_or_critics_keys_the_search(self):
        s = dict(UP.DEFAULTS)
        self.assertNotEqual(UP.filter_tag(dict(s, money=False)), UP.filter_tag(dict(s, critics=False)))
        self.assertNotEqual(UP.filter_tag(dict(s, money=False)), "")

    def test_two_people_and_two_sets_of_limits_never_share_a_key(self):
        q = "recommend a comedy for tonight"
        a = C.answer_key(q, [], [], None, "user-a:3:0:0|f11111111")
        self.assertEqual(a, C.answer_key(q, [], [], None, "user-a:3:0:0|f11111111"))
        self.assertNotEqual(a, C.answer_key(q, [], [], None, "user-b:3:0:0|f11111111"))
        self.assertNotEqual(a, C.answer_key(q, [], [], None, "user-a:3:0:0|f22222222"))
        self.assertNotEqual(a, C.answer_key(q, [], [], None, "user-a:4:0:0|f11111111"))    # their tastes moved
        self.assertNotEqual(a, C.answer_key(q, [], [], None, ""))
        r = C.retrieval_key(q, [], None, "f11111111")
        self.assertNotEqual(r, C.retrieval_key(q, [], None, "f22222222"))
        self.assertNotEqual(r, C.retrieval_key(q, [], None, ""))
        self.assertNotEqual(r, a)                                                          # the two tables never collide

    def test_light_normalising_does_not_merge_different_questions(self):
        k = lambda q, recent=(): C.answer_key(q, list(recent), [])
        self.assertEqual(k("Who directed Jaws?"), k("hey, who directed jaws"))
        self.assertEqual(k("Who directed Jaws?"), k("  WHO DIRECTED JAWS   please"))
        for other in ("Who directed Jaws 2?", "Who did not direct Jaws?", "Who directed Jaws? Not the sequel", "Who produced Jaws?"):
            self.assertNotEqual(k("Who directed Jaws?"), k(other), other)
        self.assertNotEqual(k("who directed it?", ["Jaws"]), k("who directed it?", ["Alien"]))   # a follow-up is about its films
        self.assertNotEqual(C.answer_key("and the sequel?", [], [["user", "Tell me about Jaws"]]),
                            C.answer_key("and the sequel?", [], [["user", "Tell me about Alien"]]))


if __name__ == "__main__":
    unittest.main()
