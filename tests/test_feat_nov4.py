"""4 Nov features (themes, TV critics, issues, YouTube stats / comments), no real database or model:
    python -m unittest tests.test_feat_nov4"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _defs(path, start, end_marker):
    """Run lines of a module from `start` up to `end_marker` (for modules that need the server's data to import)."""
    src = open(os.path.join(HERE, path)).read()
    i = src.index(start)
    j = src.index(end_marker, i)
    ns = {"re": re, "os": os}
    exec(src[i:j], ns)
    return ns


class Themes(unittest.TestCase):
    def test_sections(self):
        import v7_data as V
        wt = ("Intro.\n== Plot ==\nA man.\n== Themes ==\nThe film explores [[mental illness]] and neglect.<ref name=\"a\">"
              "{{cite web|title=x}}</ref> Critics such as [[Richard Brody]] argued it sympathises with its protagonist, "
              "while others read it as a critique of austerity; the debate ran for weeks in print and online, and the "
              "studio answered it in interviews.\n=== Depiction of violence ===\nSome worried the film could inspire "
              "violence.\n== Controversy ==\nShort.\n== Box office ==\nMoney.\n")
        secs = V.themes_sections(wt)
        self.assertEqual([(k, h) for k, h, _t in secs], [("themes", "Themes")])
        self.assertIn("mental illness", secs[0][2])
        self.assertNotIn("<ref", secs[0][2])
        self.assertNotIn("[[", secs[0][2])
        self.assertIn("inspire violence", secs[0][2])          # its subsection comes with it


class TVCritics(unittest.TestCase):
    def test_lines(self):
        import tv
        txt = ("Show.\n== Reception ==\n=== Critical response ===\nIt received widespread critical acclaim from critics "
               "everywhere. On Rotten Tomatoes, the first season holds an approval rating of 86% based on 43 reviews. "
               "Tim Goodman of The Hollywood Reporter called it a masterpiece of tension. The show had many fans.\n"
               "== Legacy ==\nLater.")
        saved = tv._q
        tv._q = lambda sql, p=(): [(txt,)]
        try:
            lines = tv.critics_lines(1)
        finally:
            tv._q = saved
        self.assertTrue(lines[0].startswith("On Rotten Tomatoes"))
        self.assertIn("Tim Goodman of The Hollywood Reporter called it a masterpiece of tension.", lines)
        self.assertNotIn("The show had many fans.", lines)


class IssueQuestions(unittest.TestCase):
    def test_pattern(self):
        ns = _defs("tools.py", "R_ISSUE_TOPIC = re.compile", "\n\n\nIMDB = ")
        yes = ["How did audiences react to Joker's portrayal of mental illness?", "Was Barbie's feminism divisive among viewers?",
               "How was the depiction of war received in 1917?"]
        no = ["Who directed Joker?", "What did critics praise about Mad Max: Fury Road?", "Why was Barbie so popular with audiences?",
              "What themes appear throughout Denis Villeneuve's films?", "What did critics say about the score of Dune?"]
        for q in yes:
            self.assertTrue(ns["is_issue_question"](q), q)
        for q in no:
            self.assertFalse(ns["is_issue_question"](q), q)


class EvidenceUnits(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "f.db")
        c = sqlite3.connect(self.db)
        c.executescript("""create table yt_analysis(video_key text, channel_id text, channel text, kind text, title text,
            description text, published_at text, duration text, views integer, fetched_at text, likes integer, comment_count integer);
            create table yt_analysis_units(video_key text, tmdb_id integer, chapter integer, start_s integer, chapter_title text,
            text text, channel text, published_at text, match_ok integer, techniques text);
            create table yt_analysis_comments(comment_id text primary key, video_key text, text text, likes integer,
            published_at text, fetched_at text);
            create table wiki_themes(tmdb_id integer, pageid integer, revid integer, kind text, heading text, text text, fetched_at text);
            create table review_sentiment(item_key text, label text);""")
        c.execute("insert into yt_analysis values ('v1','c','RedLetterMedia','review','Joker review','',"
                  "'2019-10-05','PT10M',1000,'2026-11-04',50,7)")
        c.execute("insert into yt_analysis_units values ('v1', 475557, 0, 0, null, 'x', 'RedLetterMedia', '2019-10-05', 1, null)")
        for i in range(7):
            c.execute("insert into yt_analysis_comments values (?,?,?,?,?,?)",
                      (f"k{i}", "v1", f"comment number {i} says the film handles its themes with real care and nuance", i, "2019-10-06", "2026-11-04"))
        c.execute("insert into yt_analysis_comments values ('short','v1','too short',99,'2019-10-06','2026-11-04')")
        c.execute("insert into wiki_themes values (475557, 10, 11, 'themes', 'Themes', 'Paragraph one about mental illness.', '2026-11-04')")
        c.commit()
        self.c = c

    def test_units(self):
        import evidence as E
        got = [(pk, d["source_type"]) for pk, _ch, d in E.units(self.c)]
        comments = [pk for pk, st in got if st == "video_comment"]
        self.assertEqual(len(comments), 4)                    # top 5 by likes, minus the one under 8 words
        self.assertNotIn("ytc:short", comments)
        self.assertIn(("th:10:themes:Themes", "themes"), got)

    def test_switches_hide_new_kinds(self):
        import evidence as E
        os.environ.pop("YT_COMMENTS", None)
        os.environ["ISSUES"] = "1"
        try:
            self.assertEqual(E._off_types(), ["video_comment"])
        finally:
            os.environ.pop("ISSUES", None)
        self.assertEqual(sorted(E._off_types()), ["themes", "video_comment"])


class YouTube(unittest.TestCase):
    def test_migration_stats_and_comments(self):
        sys.modules.setdefault("fetch_free_data", types.SimpleNamespace())
        import youtube_analysis as Y
        path = os.path.join(tempfile.mkdtemp(), "f.db")
        old = sqlite3.connect(path)
        old.execute("""create table yt_analysis(video_key text primary key, channel_id text, channel text, kind text, title text,
            description text, published_at text, duration text, views integer, fetched_at text)""")
        old.commit()
        old.close()
        Y.F.connect = lambda p=None: sqlite3.connect(p or path)
        c = Y.connect(path)
        self.assertTrue({"likes", "comment_count"} <= {r[1] for r in c.execute("pragma table_info(yt_analysis)")})

        def call(api, **p):
            if api == "videos":
                return {"items": [{"id": "v1", "snippet": {"channelId": "c", "channelTitle": "RLM", "title": "Joker review"},
                                   "contentDetails": {"duration": "PT9M"},
                                   "statistics": {"viewCount": "1000", "likeCount": "55", "commentCount": "9"}}]}
            return {"items": [{"id": "t1", "snippet": {"topLevelComment": {"snippet": {
                "textDisplay": "A long comment about how the film treats mental illness with care", "likeCount": 4,
                "publishedAt": "2019-10-06T00:00:00Z"}}}}]}
        Y._details(call, c, ["v1"], {"c": ("@RedLetterMedia", "review")})
        self.assertEqual(c.execute("select views, likes, comment_count from yt_analysis").fetchone(), (1000, 55, 9))
        c.execute("insert into yt_analysis_units values ('v1', 1, 0, 0, null, 'x', 'RLM', '2019', 1, null)")
        Y.cmd_comments(types.SimpleNamespace(limit=0), c, call)
        self.assertEqual(c.execute("select count(*) from yt_analysis_comments").fetchone()[0], 1)
        self.assertEqual(c.execute("select status from yt_analysis_comments_done").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
