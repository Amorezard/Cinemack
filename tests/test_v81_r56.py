"""v8.1 round 56 (20 Nov 2026): the public copy gets one branch per version (main = the current best version), made
with git plumbing so shared history stays shared; --replace keeps the origin remote; --push never prompts. Also checks
that the new README keeps the licence-register markers and has its diagrams. Builds toy repositories only."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "v81_public_export.py")


def git(cwd, *args, env=None):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout


class TestBranches(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        s = self.src = os.path.join(self.d, "src")
        os.makedirs(s)
        git(s, "init", "-q", "-b", "main")
        git(s, "config", "user.email", "you@example.edu")
        git(s, "config", "user.name", "Someone")
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-09-23T10:00:00", GIT_COMMITTER_DATE="2026-09-23T10:00:00")
        open(os.path.join(s, "app.py"), "w").write("print('v1')\n")
        git(s, "add", "-A")
        git(s, "commit", "-qm", "v1 start", env=env)
        git(s, "tag", "v1.0")
        git(s, "checkout", "-q", "-b", "v2")
        open(os.path.join(s, "notes.md"), "w").write("mail you@example.edu, files in /home/you/x\n")
        os.makedirs(os.path.join(s, "sets"))
        open(os.path.join(s, "sets", "sealed_a.json"), "w").write("{}")
        git(s, "add", "-A")
        git(s, "commit", "-qm", "v2 notes")
        open(os.path.join(s, "facts.db"), "w").write("data")
        git(s, "add", "-f", "facts.db")
        git(s, "commit", "-qm", "v2 database only")
        git(s, "checkout", "-q", "-b", "v8.1")
        open(os.path.join(s, "web.py"), "w").write("KEYVAL = 'abcd1234efgh5678'\n")
        git(s, "add", "-A")
        git(s, "commit", "-qm", "v8 work")
        self.keys = os.path.join(self.d, "keys.env")
        open(self.keys, "w").write("TMDB_API_KEY=abcd1234efgh5678\n")
        self.out = os.path.join(self.d, "pub")

    def tearDown(self):
        shutil.rmtree(self.d)

    def export(self, *extra):
        return subprocess.run([sys.executable, TOOL, "--src", self.src, "--out", self.out, "--keys", self.keys, "--name", "Owner",
                               "--email", "you@example.edu", "--branches", "main=v8.1,v1=v1.0,v2=v2,v9=no-such-ref"] + list(extra),
                              capture_output=True, text=True)

    def test_branches_shared_and_clean(self):
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertIn("READY", r.stdout)
        branches = set(git(self.out, "branch", "--format=%(refname:short)").split())
        self.assertEqual(branches, {"main", "v1", "v2"})
        # shared history is shared: v2's "v2 notes" commit is an ancestor of main
        notes = git(self.out, "log", "--format=%H", "--grep", "^v2 notes", "v2").split()
        self.assertEqual(len(notes), 1)
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", notes[0], "main"], cwd=self.out).returncode, 0)
        # cleaned in every commit of every branch
        allp = git(self.out, "log", "--all", "-p")
        self.assertNotIn("abcd1234efgh5678", allp)
        self.assertNotIn("school-example.org", allp)
        self.assertNotIn("/home/you/", allp)
        self.assertNotIn("sealed_a.json", git(self.out, "log", "--all", "--name-only", "--format="))
        self.assertNotIn("facts.db", git(self.out, "log", "--all", "--name-only", "--format="))
        # every branch ends with the public-copy commit; only main has the commit list
        for b in ("main", "v1", "v2"):
            files = git(self.out, "ls-tree", "-r", "--name-only", b).split()
            self.assertIn("LICENSE", files)
            self.assertEqual("docs/HISTORY.md" in files, b == "main")
        self.assertEqual(git(self.out, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertIn("v9 <- no-such-ref", r.stdout)

    def test_replace_keeps_origin_and_pushes(self):
        bare = os.path.join(self.d, "remote.git")
        git(self.d, "init", "-q", "--bare", bare)
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stdout[-500:])
        git(self.out, "remote", "add", "origin", bare)
        r = self.export("--replace", "--push")
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-500:])
        self.assertIn("push: OK", r.stdout)
        self.assertEqual(git(self.out, "remote", "get-url", "origin").strip(), bare)
        self.assertEqual(set(git(bare, "branch", "--format=%(refname:short)").split()), {"main", "v1", "v2"})


class TestReadme(unittest.TestCase):
    def test_readme(self):
        s = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        self.assertEqual(s.count("<!-- register:start -->"), 1)
        self.assertEqual(s.count("<!-- register:end -->"), 1)
        self.assertGreaterEqual(len(re.findall(r"```mermaid\n", s)), 8)
        for h in ("How a chat becomes searches", "How retrieval works", "Versions and branches"):
            self.assertIn("## " + h, s)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "BUILD_NOTES.md")))
        for m in re.findall(r"```mermaid\n(.*?)```", s, re.S):
            if m.startswith("sequenceDiagram"):
                self.assertNotIn(";", m)            # a semicolon ends a sequence-diagram statement


if __name__ == "__main__":
    unittest.main()
