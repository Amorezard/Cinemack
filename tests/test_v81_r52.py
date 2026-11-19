"""v8.1 round 52 (19 Nov 2026): Guardian bulk work moves to an evening top-up an hour before the daily reset
(00:00 UTC, measured 17/18 Nov). The nightly job skips its two Guardian steps while that crontab line exists, and the
command-line top-up keeps the owner's margin of 2 calls (it kept 40 before). No network: the top-up is replaced."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import v81_guardian_quota as Q  # noqa: E402

NIGHTLY = open(os.path.join(ROOT, "nightly.sh"), encoding="utf-8").read()


class TestReserve(unittest.TestCase):
    def run_main(self, argv):
        got = {}
        old_t, old_k = Q.topup, os.environ.get("GUARDIAN_API_KEY")
        Q.topup = lambda key, reserve, dry: got.setdefault("reserve", reserve) and "stub" or "stub"
        os.environ["GUARDIAN_API_KEY"] = "-".join(["fake", "value"])
        try:
            Q.main(argv)
        finally:
            Q.topup = old_t
            if old_k is None:
                os.environ.pop("GUARDIAN_API_KEY", None)
            else:
                os.environ["GUARDIAN_API_KEY"] = old_k
        return got.get("reserve")

    def test_topup_keeps_two_unless_told(self):
        self.assertEqual(Q.RESET_RESERVE, 2)
        self.assertEqual(self.run_main(["topup", "--dry"]), 2)
        self.assertEqual(self.run_main(["topup", "--dry", "--reserve", "15"]), 15)

    def test_plan_spends_all_but_the_margin(self):
        self.assertEqual(Q.plan_topup(352, 2, min_spend=1), 350)
        self.assertEqual(Q.plan_topup(2, 2, min_spend=1), 0)
        self.assertEqual(Q.plan_topup(None, 2, min_spend=1), 0)


class TestNightly(unittest.TestCase):
    def guard(self, crontab_text):
        tmp = tempfile.mkdtemp()
        try:
            cr = os.path.join(tmp, "crontab")
            with open(os.path.join(tmp, "lines"), "w") as f:
                f.write(crontab_text)
            with open(cr, "w") as f:
                f.write("#!/bin/sh\ncat %s/lines\n" % tmp)
            os.chmod(cr, os.stat(cr).st_mode | stat.S_IEXEC)
            fn = re.search(r"(?m)^guardian_by_topup\(\) \{.*\}", NIGHTLY).group(0)
            p = subprocess.run(["bash", "-c", "set -u -o pipefail\n%s\nguardian_by_topup" % fn],
                               env=dict(os.environ, PATH=tmp + ":" + os.environ["PATH"]), capture_output=True)
            return p.returncode == 0
        finally:
            shutil.rmtree(tmp)

    def test_guard_follows_the_crontab_line(self):
        line = "0 18 * * * cd x && python tools/v81_guardian_quota.py topup >> logs/guardian_topup.log 2>&1 # v81-guardian-topup\n"
        self.assertTrue(self.guard(line))
        self.assertTrue(self.guard(line + "".join("%d 1 * * * true # filler line %d\n" % (i % 60, i) for i in range(20000))))
        self.assertFalse(self.guard("17 4 * * * bash nightly.sh\n47 */2 * * * x # v81-guardian-probe\n"))
        self.assertFalse(self.guard(line.replace("# v81-guardian-topup", "# v81-guardian-topup-old")))
        self.assertFalse(self.guard(""))

    def test_both_guardian_steps_are_guarded(self):
        for step in ('python pf5_data.py press --max', 'python fetch_free_data.py guardian'):
            lines = [l for l in NIGHTLY.splitlines() if step in l and not l.lstrip().startswith("#")]
            self.assertEqual(len(lines), 1, step)
            self.assertIn("! guardian_by_topup &&", lines[0])
        self.assertLess(NIGHTLY.index("guardian_by_topup()"), NIGHTLY.index("light() {"))


if __name__ == "__main__":
    unittest.main()
