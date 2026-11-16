"""v8.1 round 46 (16 Nov 2026): the home banner dots edit (slice 6.1) - no layout property is animated any more."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


class TestDotsPatch(unittest.TestCase):
    def test_banner_dots_no_longer_animate_width_and_the_audit_passes(self):
        import v81_motion_audit as MA
        css = open(os.path.join(ROOT, "web", "v8", "app.css"), encoding="utf-8").read()
        r = MA.audit(css)
        self.assertEqual(r["layout"], [])
        self.assertTrue(r["pass"], r["rule"])
        self.assertIn("clip-path: inset(0 8px round 4px)", css)
        self.assertNotIn("transition: width", css)

    def test_patch_applies_once_and_undoes_exactly(self):
        import patch_r46 as PR
        tmp = tempfile.mkdtemp()
        try:
            (path, old, new), = PR.GROUPS["dots"]
            src = open(os.path.join(ROOT, path), encoding="utf-8").read()
            orig = src.replace(new, old) if new in src else src
            self.assertEqual(orig.count(old), 1)
            os.makedirs(os.path.join(tmp, "web", "v8"))
            with open(os.path.join(tmp, path), "w", encoding="utf-8") as fh:
                fh.write(orig)
            run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "tools", "patch_r46.py"), *a], cwd=tmp,
                                            capture_output=True, text=True)
            self.assertEqual(run("--check", "dots").returncode, 0)
            self.assertEqual(open(os.path.join(tmp, path), encoding="utf-8").read(), orig)
            self.assertIn("patched", run("dots").stdout)
            self.assertIn("already applied", run("dots").stdout)
            self.assertIn("undone", run("--undo", "dots").stdout)
            self.assertEqual(open(os.path.join(tmp, path), encoding="utf-8").read(), orig)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
