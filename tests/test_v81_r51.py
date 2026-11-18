"""v8.1 round 51 (18 Nov 2026): the site process finds the Guardian key in keys.env when its environment has none
(start_site.sh reads only the switch files). Fake values only; the key is never in an answer."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
import guardian_live as GL  # noqa: E402

FAKE = "-".join(["fake", "guardian", "value", "1234"])


class TestKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.pop("GUARDIAN_API_KEY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.old is not None:
            os.environ["GUARDIAN_API_KEY"] = self.old

    def f(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_environment_first(self):
        p = self.f("keys.env", "GUARDIAN_API_KEY=from-file-value\n")
        os.environ["GUARDIAN_API_KEY"] = FAKE
        try:
            self.assertEqual(GL.guardian_key([p]), FAKE)
        finally:
            os.environ.pop("GUARDIAN_API_KEY", None)

    def test_file_forms(self):
        for text in ("GUARDIAN_API_KEY=%s\n", "export GUARDIAN_API_KEY='%s'\n", "# c\nOTHER=x\nGUARDIAN_API_KEY=\"%s\"  # note\n"):
            self.assertEqual(GL.guardian_key([self.f("k.env", text % FAKE)]), FAKE, text)

    def test_missing_or_empty(self):
        self.assertEqual(GL.guardian_key([os.path.join(self.tmp, "none.env")]), "")
        self.assertEqual(GL.guardian_key([self.f("k.env", "GUARDIAN_API_KEY=\n#GUARDIAN_API_KEY=%s\n" % FAKE)]), "")
        self.assertEqual(GL.guardian_key([self.f("a.env", "X=1\n"), self.f("b.env", "GUARDIAN_API_KEY=%s\n" % FAKE)]), FAKE)

    def test_lookup_uses_it(self):
        src = open(os.path.join(ROOT, "web", "guardian_live.py"), encoding="utf-8").read()
        self.assertIn("k = key or guardian_key()", src)
        self.assertNotIn('os.environ.get("GUARDIAN_API_KEY")\n', src.split("def lookup")[1])


if __name__ == "__main__":
    unittest.main()
