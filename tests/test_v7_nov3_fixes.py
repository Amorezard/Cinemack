"""3 Nov fixes, no database or model needed:  python -m unittest tests.test_v7_nov3_fixes"""
import ast
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _chat_parts(names, extra=None):
    """Run only these top-level definitions from chat.py (it opens movies.db and imports ollama when imported)."""
    tree = ast.parse(open(os.path.join(HERE, "chat.py")).read())
    keep = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            keep.append(n)
        elif isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets):
            keep.append(n)
    ns = {"_os": os, "re": re}
    ns.update(extra or {})
    exec(compile(ast.Module(body=keep, type_ignores=[]), "chat_parts", "exec"), ns)
    return ns


class ContextBuilderV2(unittest.TestCase):
    CARD = ("Titanic (1997)\nGenres: Drama, Romance\nDirector: James Cameron\nTMDB user rating: 7.9/10 from 23,637 votes\n"
            "[TMDB credits; money: TMDB / Wikipedia infobox / Wikidata]\n"
            "Wikipedia intro: Titanic is a 1997 American epic romance and disaster film directed, written, produced and "
            "co-edited by James Cameron.")
    BLOCK = ("Titanic (1997) - structured data\nDirector: James Cameron [TMDB]\n"
             "Wikipedia intro: Titanic is a 1997 American epic romance and disaster film directed, written, produced and "
             "co-edited by James Cameron.\nAwards: 11 Academy Awards won [Wikidata]")

    def build(self, version):
        ns = _chat_parts({"build_context", "_build_context_v1", "_line_key", "CTX_CHARS", "PRIMARY", "_LABELS",
                          "CTX_DEDUP_MIN"}, {"CTX_BUILDER_V": version, "context_for": lambda h: self.CARD})
        ns["CTX_BUILDER_V"] = version
        c, b = ns["build_context"]([{"id": 1}], {"blocks": [("facts", self.BLOCK)]})
        return c + "\n" + b

    def test_keeps_fact_lines_and_source_labels(self):
        out = self.build("2")
        self.assertIn("Director: James Cameron\n", out + "\n")          # the card's own line stays
        self.assertIn("TMDB user rating: 7.9/10", out)
        self.assertIn("[TMDB credits; money: TMDB / Wikipedia infobox / Wikidata]", out)
        self.assertIn("Awards: 11 Academy Awards won", out)

    def test_drops_only_the_repeated_passage(self):
        out = self.build("2")
        self.assertEqual(out.count("Wikipedia intro: Titanic is a 1997"), 1)

    def test_old_version_still_available(self):
        out = self.build("1")
        self.assertNotIn("[TMDB credits; money", out)                    # the 27 Oct bug, kept for CTX_BUILDER_V=1


class PromptOrderV2(unittest.TestCase):
    def prompt(self, version):
        how = ("How to answer: this is a factual question. Answer it in one or two sentences.\n\n"
               "Lines in the structured data end with their source in brackets.\n\nNo disclaimers: don't hedge.")
        ns = _chat_parts({"_v3_prompt"}, {
            "CTX_BUILDER": False, "PROMPT_ORDER": True, "PROMPT_ORDER_V": version, "context_for": lambda h: "Sinners (2025)",
            "_spot": lambda t: t, "_guard_rule": lambda: "", "ANALYSIS_HOW": "", "CURRENT_HOW": "", "RANKING_HOW": "",
            "REVIEWS_HOW": "", "FACT_HOW": how, "NEWS_HOW": "", "RECEPTION_HOW": "", "RECEPTION_LIST_HOW": "",
            "TV_HOW": "", "TV_REC_HOW": "", "ISSUE_HOW": "", "HOW_TO_ANSWER": "", "DATA_NOTE": "", "NO_HEDGING": ""})
        return ns["_v3_prompt"]("Who directed Sinners?", [{"id": 1}], {"blocks": [("facts", "Director: Ryan Coogler")],
                                                                        "style": "fact"}, "")

    def test_own_paragraph_stays_after_the_question(self):
        p = self.prompt("2")
        self.assertLess(p.index("Question: Who directed Sinners?"), p.index("How to answer: this is a factual"))
        self.assertLess(p.index("No disclaimers"), p.index("Question:"))          # the shared text goes first

    def test_old_order_available(self):
        p = self.prompt("1")
        self.assertLess(p.index("How to answer"), p.index("Question:"))


class TVRecommendWords(unittest.TestCase):
    def setUp(self):
        src = open(os.path.join(HERE, "tv.py")).read()
        i = src.find("R_REC = re.compile")
        ns = {"re": re}
        exec(src[i:src.find("re.I)", i) + 5], ns)
        self.R = ns["R_REC"]

    def test_loved_x_what_should_i_watch(self):
        self.assertTrue(self.R.search("I loved Game of Thrones, what fantasy series should I watch next?"))
        self.assertTrue(self.R.search("I enjoyed The Wire, any other crime shows?"))

    def test_fact_questions_are_not_recommendations(self):
        for q in ("Who created Breaking Bad?", "How many seasons does The Sopranos have?",
                  "I liked the ending of Lost, what did it mean?"):
            self.assertFalse(self.R.search(q), q)


class MemoryReferBack(unittest.TestCase):
    def test_refer_back_pattern(self):
        src = open(os.path.join(HERE, "web", "memory.py")).read()
        i = src.find("R_REFER = re.compile")
        ns = {"re": re}
        exec(src[i:src.find("re.I)", i) + 5], ns)
        R = ns["R_REFER"]
        tree = ast.parse(open(os.path.join(HERE, "memory_eval.py")).read())
        mv = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("BACK", "UNRELATED"):
                exec(compile(ast.Module(body=[n], type_ignores=[]), "x", "exec"), mv)
        self.assertGreaterEqual(sum(bool(R.search(q)) for _e, q in mv["BACK"]), 28)
        self.assertEqual([q for q in mv["UNRELATED"] if R.search(q)], [])
        for q in ("What happened before the events of Rogue One?", "Is it worth watching again?",
                  "What did critics say about Dune?"):
            self.assertFalse(R.search(q), q)


class NewRules(unittest.TestCase):
    def test_shorts2_needs_only_shorts_in_recommendations(self):
        import v7_rules as V
        d = tempfile.mkdtemp()

        def write(name, verdicts, rec_ctx):
            rows = [{"id": f"s{i}", "category": "recommend" if i == 0 else "facts", "answer": "x",
                     "context": rec_ctx if i == 0 else "", "judge": {"verdict": v, "grounded": 5}}
                    for i, v in enumerate(verdicts)]
            p = os.path.join(d, name)
            json.dump({"rows": rows}, open(p, "w"))
            return p
        ok_ctx = ("Here is information on movies that might be relevant to the question:\n\nPiper (2016)\nFormat: short film"
                  ", 6 min\n\n---\n\nBao (2018)\nFormat: short film, 8 min")
        bad_ctx = ok_ctx + "\n\n---\n\nThe Animatrix (2003)\nGenres: Animation"
        off = write("off.json", ["pass", "pass", "fail"], "")
        self.assertTrue(V.cmd_shorts2(write("on1.json", ["pass", "pass", "fail"], ok_ctx), off))
        self.assertFalse(V.cmd_shorts2(write("on2.json", ["pass", "pass", "fail"], bad_ctx), off))
        self.assertFalse(V.cmd_shorts2(write("on3.json", ["pass", "fail", "fail"], ok_ctx), off))


if __name__ == "__main__":
    unittest.main()


class TVMidFilmFollowUp(unittest.TestCase):
    """4 Nov: a follow-up in a film conversation is not sent to a show that shares a word with it."""
    def setUp(self):
        import tv
        self.tv = tv
        self.saved = dict(tv._IDX)
        n = tv.norm
        tv._IDX["shows"] = [(n(t), i, t, 2000, v) for i, (t, v) in enumerate(
            [("Mars", 300), ("The Middle", 1500), ("Breaking Bad", 15000), ("Fargo", 2500)])]
        tv._IDX["films"] = {n("Fargo")}
        tv._IDX["all_films"] = {n("Fargo"), n("Cast Away"), n("Arrival"), n("Ex Machina")}

    def tearDown(self):
        self.tv._IDX.clear()
        self.tv._IDX.update(self.saved)

    def blocked(self, q, recent):
        ns = self.tv.named_show(q)
        return ns is not None and self.tv._mid_film(q, recent) and not self.tv._strong_show_name(q, ns)

    def test_film_conversation_follow_ups_stay_films(self):
        self.assertTrue(self.blocked("no, he's an astronaut left behind on Mars", ["Cast Away"]))
        self.assertTrue(self.blocked("Who's in the middle one?", ["Arrival", "Ex Machina", "Interstellar"]))

    def test_unmistakable_show_still_named_mid_film(self):
        self.assertFalse(self.blocked("Who created Breaking Bad?", ["Arrival"]))

    def test_no_earlier_turn_unchanged(self):
        self.assertFalse(self.tv._mid_film("Who's in the middle one?", None))
        self.assertFalse(self.tv._mid_film("How many seasons does The Middle have?", ["Arrival"]))   # explicit TV word
