"""v8.1 round 45 (15 Nov 2026): checks on the front-end pieces that need no browser - the two language files
(8.1), the reason codes shared by web/recs.py and web/v8/why.js (6.2), the motion audit (6.1), the round's patch
script, and that none of the new page modules is loaded by an existing page (they stay hidden)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import v81_motion_audit as MA  # noqa: E402

V8 = os.path.join(ROOT, "web", "v8")
NEW_JS = ("i18n.js", "why.js", "forecast_panel.js")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def holes(v):
    return sorted(re.findall(r"\{(\w+)\}", v if isinstance(v, str) else " ".join(v.values())))


class TestLocales(unittest.TestCase):
    def setUp(self):
        self.en = json.loads(read(V8, "locales", "en.json"))
        self.es = json.loads(read(V8, "locales", "es.json"))

    def test_both_files_have_the_same_keys_and_placeholders(self):
        self.assertEqual(sorted(self.en), sorted(self.es))
        for k in self.en:
            if k == "_meta":
                continue
            self.assertEqual(type(self.en[k]), type(self.es[k]), k)
            self.assertEqual(holes(self.en[k]), holes(self.es[k]), k)
            for table in (self.en, self.es):
                v = table[k]
                if isinstance(v, dict):
                    self.assertIn("other", v, k)
                    self.assertTrue(set(v) <= {"zero", "one", "two", "few", "many", "other"}, k)
                    self.assertTrue(all(isinstance(x, str) and x.strip() for x in v.values()), k)
                else:
                    self.assertTrue(isinstance(v, str) and v.strip(), k)

    def test_spanish_is_translated_and_meta_is_valid(self):
        same = [k for k in self.en if k != "_meta" and self.en[k] == self.es[k]]
        self.assertEqual(same, ["forecast.experimental"])                 # the same word in both languages
        self.assertEqual(self.en["_meta"]["locale"], "en-US")
        self.assertTrue(re.fullmatch(r"es(-[A-Z]{2})?", self.es["_meta"]["locale"]))

    def test_no_profit_wording_in_either_language(self):
        text = (read(V8, "locales", "en.json") + read(V8, "locales", "es.json")).lower()
        for w in ("profit", "roi", "break even", "ganancia", "beneficio", "rentab", "guarantee", "garant"):
            self.assertNotIn(w, text)

    def test_every_key_the_modules_use_exists(self):
        used = set()
        for f in NEW_JS:
            src = read(V8, f)
            used |= set(re.findall(r"""\bt\(\s*'([\w.]+)'""", src))
            for tpl in re.findall(r"\bt\(\s*`([\w.]+)\.\$\{", src):       # t(`why.${code}`) / t(`forecast.horizon.${h}`)
                used |= {k for k in self.en if k.startswith(tpl + ".")}
        self.assertGreater(len(used), 15)
        self.assertEqual(sorted(k for k in used if k not in self.en), [])
        unused = sorted(k for k in self.en if k not in used and k != "_meta" and not k.startswith("count."))
        self.assertEqual(unused, ["forecast.none"])                       # shown by the film page once the panel is wired


class TestReasonsAndHiding(unittest.TestCase):
    def test_reason_codes_match_the_ranking_module(self):
        import recs
        m = re.search(r"export const REASONS = \[([^\]]+)\]", read(V8, "why.js"))
        js = re.findall(r"'(\w+)'", m.group(1))
        self.assertEqual(js, list(recs.REASONS))
        en = json.loads(read(V8, "locales", "en.json"))
        for code in recs.REASONS:
            self.assertIn("why." + code, en)

    def test_new_modules_build_text_nodes_only(self):
        for f in NEW_JS:
            src = re.sub(r"//[^\n]*", "", read(V8, f))
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
                self.assertNotIn(bad, src, f)

    def test_no_existing_page_loads_the_new_modules(self):
        for f in os.listdir(V8):
            if f in NEW_JS or not f.endswith((".js", ".html")):
                continue
            src = read(V8, f)
            for mod in NEW_JS:
                self.assertNotIn(mod, src, "%s loads %s" % (f, mod))
        for f in ("server.py", "v8routes.py", "v8extra.py"):
            src = read(ROOT, "web", f)
            for mod in ("forecast_api", "tool_router", "plan_adapter", "plan_sql_tv", "import recs", "recs."):
                self.assertNotIn(mod, src, "%s uses %s" % (f, mod))


CSS = """:root { --motion-instant: 100ms; --motion-select: 150ms; --motion-card: 220ms; --motion-panel: 300ms; --t-fast: var(--motion-select); }
.a { transition: opacity var(--t-fast), transform var(--motion-card) ease; }
.b { animation: spin .8s linear infinite; }
/* .c { transition: width 2s; } */
.skip { transition: top var(--t-fast); }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { *, *::before { animation-duration: .01ms !important; transition-duration: .01ms !important; } }
"""


class TestMotionAudit(unittest.TestCase):
    def test_a_clean_sheet_passes(self):
        r = MA.audit(CSS)
        self.assertTrue(r["pass"], r["rule"])
        self.assertEqual(len(r["items"]), 3)
        self.assertEqual([i["selector"] for i in r["literal"]], [".b"])
        self.assertEqual(r["literal"][0]["literal_ms"], [800.0])
        self.assertEqual(len(r["loops"]), 1)
        self.assertIn("PASS", MA.report(r, "x.css"))

    def test_each_rule_can_fail(self):
        self.assertFalse(MA.audit(CSS.replace("--motion-card: 220ms", "--motion-card: 250ms"))["rule"]["1_tokens"])
        self.assertFalse(MA.audit(CSS.replace("--motion-panel: 300ms;", ""))["rule"]["1_tokens"])
        self.assertFalse(MA.audit(CSS.replace("transition-duration: .01ms !important;", ""))["rule"]["2_reduced_motion"])
        self.assertFalse(MA.audit(CSS.replace("*, *::before {", ".x {"))["rule"]["2_reduced_motion"])
        bad = MA.audit(CSS + ".d { transition: opacity .2s, max-width .2s; }\n.e { transition: margin-left var(--t-fast); }\n"
                             ".f { transition: height var(--t-fast); }")
        self.assertEqual([x["property"] for x in bad["layout"]], ["margin-left", "height"])     # max-width is not width
        self.assertFalse(bad["pass"])

    def test_the_sites_stylesheet_has_the_tokens_and_the_reduced_motion_block(self):
        r = MA.audit(read(V8, "app.css"))
        self.assertTrue(r["rule"]["1_tokens"], r["tokens"])
        self.assertTrue(r["rule"]["2_reduced_motion"])
        self.assertGreater(len(r["items"]), 30)
        css = read(V8, "app.css")
        self.assertIn("--t-fast: var(--motion-select)", css)              # the older names keep their values
        self.assertIn("--t-med: var(--motion-card)", css)


class TestPatchScript(unittest.TestCase):
    def run_patch(self, cwd, *args):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "patch_r45.py"), *args], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout

    def test_apply_is_whole_repeatable_and_reversible(self):
        import patch_r45 as PR
        tmp = tempfile.mkdtemp()
        try:
            orig = {}
            for g, edits in PR.GROUPS.items():
                for path, old, new in edits:
                    src = read(ROOT, path)
                    src = src.replace(new, old) if new in src else src            # the unpatched text, whatever the tree's state
                    self.assertEqual(src.count(old), 1, (g, path))
                    orig[path] = src
            for path, src in orig.items():
                os.makedirs(os.path.dirname(os.path.join(tmp, path)), exist_ok=True)
                with open(os.path.join(tmp, path), "w", encoding="utf-8") as fh:
                    fh.write(src)
            groups = list(PR.GROUPS)
            rc, out = self.run_patch(tmp, "--list")
            self.assertEqual([l.split()[1] for l in out.strip().split("\n")], ["ready"] * len(groups))
            self.assertEqual(self.run_patch(tmp, "--check", *groups)[0], 0)
            self.assertEqual(read(tmp, "web/user_prefs.py"), orig["web/user_prefs.py"])   # --check writes nothing
            self.assertEqual(self.run_patch(tmp, *groups)[0], 0)
            rc, out = self.run_patch(tmp, *groups)
            self.assertEqual(rc, 0)
            self.assertEqual(out.count("already applied"), len(groups))
            self.assertEqual(read(tmp, "web/user_prefs.py").count('f["strict"] = True'), 1)
            self.assertEqual(self.run_patch(tmp, "--undo", *groups)[0], 0)
            for path, src in orig.items():
                self.assertEqual(read(tmp, path), src, path)
            with open(os.path.join(tmp, "web/user_prefs.py"), "w") as fh:               # a file that moved on: refuse, write nothing
                fh.write("def filter_tag(settings):\n    return ''\n")
            rc, out = self.run_patch(tmp, "cache")
            self.assertEqual(rc, 1)
            self.assertIn("NOT applied", out)
            self.assertEqual(self.run_patch(tmp, "nonsense")[0], 2)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
