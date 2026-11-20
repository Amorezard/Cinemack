"""v8.1 round 50: the public copy (tools/v81_public_export.py) on a small made-up repository whose OLD commits hold
what must never be published (a sealed set, a database, a value from the key file, an email address). The copy is
one new commit plus docs/HISTORY.md (the cleaned commit messages); the private repository is never changed. All
values are fake."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import v81_public_export as X  # noqa: E402

FAKE_VALUE = "-".join(["fakevalue", "for", "this", "test", "only", "0000"])   # what the key file holds
MAIL = "dev" + "@" + "uni.example-mail.org"                                   # an address that must disappear
OLD_KEYLIKE = "".join(["abcd", "1234", "efgh", "5678", "ijkl"])               # looks like a key; only in an old commit


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


class TestRules(unittest.TestCase):
    def test_paths(self):
        out = {p: X.dropped(p) for p in (
            "sets/sealed_v8_an.json", "results/old_sealed_eval.json", "results/v81_recs_v3.json", "results/llm_cache.sqlite",
            "keys.env", ".env", "logs/requests.jsonl", "archive/old.sh", "planning/cinemack-v8.1/audit_raw/machine.txt",
            "data.zip", "web/x.db-wal", "adapters/a.safetensors",
            "results/v81_recs_v3.txt", "results/performance_summary.txt", "v8_lock.env", "web/server.py",
            "web/accounts.py", "sets/tune_v8_invented.json", "docs/audit/home_1440.png", "web/v8/app.js")}
        self.assertTrue(all(out[p] for p in list(out)[:12]), out)
        self.assertFalse(any(out[p] for p in list(out)[12:]), out)

    def test_text(self):
        s = X.scrub("write to %s or noreply@anthropic.com, a@example.com, git@github.com:me/r, icon@2x.png; "
                    "key %s; /home/you/movie-buff" % (MAIL, FAKE_VALUE), [FAKE_VALUE])
        self.assertNotIn(MAIL, s)
        self.assertNotIn(FAKE_VALUE, s)
        for keep in ("noreply@anthropic.com", "a@example.com", "git@github.com:me/r", "icon@2x.png", "/home/you/movie-buff", "REDACTED"):
            self.assertIn(keep, s)

    def test_values_are_read_not_printed(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "values.txt")
            with open(p, "w") as f:
                f.write("# comment\nA_VALUE=%s\nSHORT=abc\nREF=$OTHER_ONE_LONG\n" % FAKE_VALUE)
            self.assertEqual(X.secret_values(p), [FAKE_VALUE])
            self.assertEqual(X.secret_values(os.path.join(tmp, "none")), [])
        finally:
            shutil.rmtree(tmp)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        src = self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(src, "sets"))
        os.makedirs(os.path.join(src, "results"))
        os.makedirs(os.path.join(src, "web"))
        shutil.copytree(os.path.join(ROOT, "tools"), os.path.join(src, "tools"), ignore=shutil.ignore_patterns("__pycache__"))
        sh(src, "git", "init", "-q", "-b", "v8.1")
        sh(src, "git", "config", "user.email", MAIL)
        sh(src, "git", "config", "user.name", "Dev Person")
        w = lambda p, t: open(os.path.join(src, p), "w").write(t)  # noqa: E731
        w("sets/sealed_v8_an.json", '{"q": "held out"}')
        w("sets/tune_v8.json", '{"q": "tune"}')
        w("results/llm_cache.sqlite", "x")
        w("results/v81_recs_v3.txt", "rule FAIL")
        w("web/settings.py", "VALUE = '%s'\n# contact %s\n" % (FAKE_VALUE, MAIL))
        w("web/old.py", "api_key = '%s'\n" % OLD_KEYLIKE)                 # key-like, NOT in the key file
        sh(src, "git", "add", "-A")
        sh(src, "git", "commit", "-q", "-m", "first, by %s" % MAIL)
        sh(src, "git", "rm", "-q", "sets/sealed_v8_an.json", "results/llm_cache.sqlite", "web/old.py")
        w("web/settings.py", "import os\nVALUE = os.environ.get('VALUE')\n")
        w("README.md", "# Demo\n")
        sh(src, "git", "add", "-A")
        sh(src, "git", "commit", "-q", "-m", "second")
        self.values = os.path.join(self.tmp, "values.txt")
        with open(self.values, "w") as f:
            f.write("VALUE=%s\n" % FAKE_VALUE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, out, history=False):
        everything = sh(out, "git", "log", "--all", "-p", "--format=%ae %an %s%n%b")
        self.assertNotIn(MAIL, everything)
        self.assertNotIn(FAKE_VALUE, everything)
        self.assertNotIn("sealed_v8_an", everything)
        self.assertNotIn("llm_cache", everything)
        self.assertNotIn(OLD_KEYLIKE, everything)
        files = sh(out, "git", "ls-files").split()
        for f in ("LICENSE", "DATA_SOURCES.md", "docs/HISTORY.md", "README.md", "sets/tune_v8.json", "results/v81_recs_v3.txt", "web/settings.py"):
            self.assertIn(f, files)
        self.assertNotIn(X.MARK, files)
        self.assertIn("MIT License", open(os.path.join(out, "LICENSE")).read())
        self.assertEqual(sh(out, "git", "branch", "--show-current").strip(), "main")
        self.assertEqual(int(sh(out, "git", "rev-list", "--count", "HEAD")), 3 if history else 2)
        hist = open(os.path.join(out, "docs", "HISTORY.md")).read()
        self.assertIn("- first, by you@example.edu", hist)
        self.assertIn("- second", hist)
        self.assertLess(hist.index("- first"), hist.index("- second"))
        before = sh(self.src, "git", "log", "--format=%H %ae %s")            # the private repository is untouched
        self.assertIn(MAIL, before)
        self.assertEqual(len(before.splitlines()), 2)
        self.assertEqual(sh(out, "git", "remote").strip(), "")
        self.assertIn("Dev Person", sh(out, "git", "log", "-1", "--format=%an"))
        self.assertEqual(set(sh(out, "git", "log", "--format=%ae").split()), {"me@users.noreply.github.com"})

    def test_snapshot_mode(self):
        out = os.path.join(self.tmp, "pub")
        X.main(["--src", self.src, "--out", out, "--snapshot", "--email", "me@users.noreply.github.com", "--keys", self.values])
        self.check(out)
        with self.assertRaises(SystemExit):                       # never over a folder it did not make
            os.makedirs(os.path.join(self.tmp, "other"))
            open(os.path.join(self.tmp, "other", "f"), "w").close()
            X.main(["--src", self.src, "--out", os.path.join(self.tmp, "other"), "--replace"])
        X.main(["--src", self.src, "--out", out, "--replace", "--snapshot", "--email", "me@users.noreply.github.com", "--keys", self.values])
        self.check(out)
        with self.assertRaises(SystemExit):
            X.main(["--src", self.src, "--out", os.path.join(self.src, "inside")])


    def test_history_mode_keeps_every_commit_cleaned(self):
        out = os.path.join(self.tmp, "pub")
        X.main(["--src", self.src, "--out", out, "--email", "me@users.noreply.github.com", "--name", "Dev Person",
                "--holder", "Dev Person", "--keys", self.values])
        self.check(out, history=True)
        subjects = sh(out, "git", "log", "--reverse", "--format=%s").splitlines()
        self.assertEqual(subjects[:2], ["first, by you@example.edu", "second"])
        src_dates = sh(self.src, "git", "log", "--reverse", "--format=%aI %cI").splitlines()
        self.assertEqual(sh(out, "git", "log", "--reverse", "--format=%aI %cI").splitlines()[:2], src_dates)
        first = sh(out, "git", "show", "--name-only", "--format=", "HEAD~2").split()
        self.assertIn("sets/tune_v8.json", first)
        self.assertNotIn("sets/sealed_v8_an.json", first)
        self.assertIn("REDACTED", sh(out, "git", "show", "HEAD~2:web/old.py"))
        import v81_publish_check as PC
        self.assertTrue(PC.scan(out, history=True, deep=True)["ready"])
        self.assertEqual(len(sh(self.src, "git", "log", "--format=%H").split()), 2)   # the source is untouched

if __name__ == "__main__":
    unittest.main()
