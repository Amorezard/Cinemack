"""30 Oct: TV routing guards and IMDb alternate-title guards (no database needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aliases as A  # noqa: E402
import tv  # noqa: E402

SHOWS = [("breaking bad", 1, "Breaking Bad", 2008, 15000), ("the office", 2, "The Office", 2005, 9000),
         ("friends", 3, "Friends", 1994, 8000), ("the sopranos", 4, "The Sopranos", 1999, 5000),
         ("a time machine", 5, "A Time Machine", 2019, 3), ("the missing", 6, "The Missing", 2014, 600),
         ("young girl", 7, "Young Girl", 2011, 2), ("fargo", 8, "Fargo", 2014, 3000)]


_SAVED = {}


def setup_module(_m):
    _SAVED.update(tv_q=tv._q, tv_idx=dict(tv._IDX), tv_coll=dict(tv._COLL), tv_db=dict(tv._DB), tv_on=tv.TV,
                  a_idx=dict(A._IDX))
    tv.TV = True
    tv._IDX.update(shows=SHOWS, films={"fargo"}, all_films={"fargo", "the missing", "heat"})
    tv._COLL["names"] = {"harry potter"}
    tv._DB.update(tried=True, conn=object())
    tv._q = lambda sql, p=(), conn=None: [(1,)]          # "select 1 from tv_shows": the tables exist
    A._IDX.clear()
    A._IDX["map"] = {}
    A._IDX["map_w"] = 0
    imdb = {"time machine": {10: ("Time Machine", "Hot Tub Time Machine", "2010", "imdb")},
            "the genie": {11: ("The Genie", "Jinn", "2014", "imdb")},
            "dark night of the soul": {12: ("Dark Night of the Soul", "Some Film", "2001", "imdb")},
            "t2": {13: ("T2", "Terminator 2: Judgment Day", "1991", "curated")}}
    A._IDX["map_imdb"] = {k: v for k, v in imdb.items() if v[next(iter(v))][3] != "imdb" or A.imdb_alias_ok(k)}
    A._IDX["map_imdb_w"] = 4


def teardown_module(_m):
    tv._q = _SAVED["tv_q"]
    tv.TV = _SAVED["tv_on"]
    for d, k in ((tv._IDX, "tv_idx"), (tv._COLL, "tv_coll"), (tv._DB, "tv_db"), (A._IDX, "a_idx")):
        d.clear()
        d.update(_SAVED[k])


def why(q, recent=None):
    return tv.wants_tv(q, recent)


def test_tv_set_still_routes():
    for q in ["What's that TV show where a chemistry teacher with cancer starts making meth?",
              "A series about a mob boss in New Jersey who sees a therapist",
              "the show where kids in the 80s look for their missing friend and find a monster from another dimension",
              "a sitcom about the employees of a paper company in Scranton filmed like a documentary",
              "the anthology show where each episode is a dark story about technology",
              "Who created Breaking Bad?", "How many episodes of Friends are there?", "who created breaking bad",
              "shows like Breaking Bad", "a short miniseries I can finish in a weekend",
              "What network aired The Office?"]:
        assert why(q), q


def test_film_descriptions_stay_on_film_path():
    for q in ["A man commits a series of murders in a small town and the police chase him",
              "she has a psychotic episode after her husband dies",
              "a girl wins a talent show and becomes famous",
              "two brothers go binge drinking in Las Vegas",
              "a guy builds a time machine in his garage and goes back to fix his life",
              "the missing girl is found in the woods years later by a hunter",
              "this movie about the show where a young girl wants to sing",
              ("I remember watching this as a kid, there was a long scene where the family goes to a show and the "
               "boy gets lost and has to find his way home through the city at night, and there is a dog that helps "
               "him and an old man who gives him food and tells him a story about the war")]:
        assert why(q) is None, q


def test_named_needs_title_form_in_long_questions():
    q = "I am looking for something where a woman finds a time machine in the basement of her old house"
    assert tv.named_show(q) is None
    assert tv.named_show("What year did The Missing start?")[2] == "The Missing"


def test_followup_not_when_last_title_is_a_film():
    assert why("who directed it?", ["The Missing"]) is None
    assert why("who created it?", ["Breaking Bad"]) == "followup"


def test_imdb_alias_needs_title_form():
    assert A.expand("a guy builds a time machine in a hot tub", imdb=True) == "a guy builds a time machine in a hot tub"
    assert "Hot Tub Time Machine" in A.expand("is Time Machine the one with John Cusack?", imdb=True)
    assert A.expand("The genie grants three wishes to a boy", imdb=True) == "The genie grants three wishes to a boy"
    assert "Jinn" not in A.expand("who is in The Genie", imdb=True)            # one content word: dropped at load
    assert "Terminator 2" in A.expand("who directed T2?", imdb=True)           # curated aliases unchanged
    long_q = "I saw a film once " + "and it had a lot going on " * 6 + "called Time Machine maybe"
    assert "Hot Tub" not in A.expand(long_q, imdb=True)


def test_imdb_off_unchanged():
    assert A.expand("is Time Machine the one with John Cusack?", imdb=False) == "is Time Machine the one with John Cusack?"


# v8.1 (12 Nov 2026, audit finding F2): the six tests above are pytest-style functions, so `python -m unittest
# tests.test_v7_oct30_fixes` (how every round runs the tests) collected none of them. The lines below hand them to
# unittest unchanged (the module's own set-up and tear-down around each); pytest still works as before.
import unittest  # noqa: E402


def load_tests(_loader, _tests, _pattern):
    suite = unittest.TestSuite()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            suite.addTest(unittest.FunctionTestCase(fn, setUp=lambda: setup_module(None),
                                                    tearDown=lambda: teardown_module(None), description=name))
    return suite
