"""Round 47 (16 Nov 2026), v8.1 worktree only: edits to files that already exist. Each edit is an exact text anchor
found exactly once; a group is applied whole or not at all; an applied group is skipped.

    python tools/patch_r47.py --list | --check GROUP | GROUP | --undo GROUP

Groups
  press   web/server.py installs web/guardian_live.py's route (404 while GUARDIAN_LIVE is off);
          web/v8/film.js shows "In the Guardian" under a film when the route answers (nothing while it is off)
  topup   tools/v81_guardian_quota.py: the top-up runs just before the reset and spends every call left
          (new reviews first, then articles), keeping 2 as a margin (user decision 16 Nov)
"""
import sys
from pathlib import Path

GROUPS = {}

GROUPS["press"] = [('web/server.py', '    v8routes.install(app, user_of=user_of, account_of=account_of, gate=_gate)\nexcept Exception as _e:  # noqa: BLE001  - the classic page keeps working if v8 fails to load\n    print(f"[cinemack] v8 routes not loaded: {_e!r}", flush=True)\n', '    v8routes.install(app, user_of=user_of, account_of=account_of, gate=_gate)\nexcept Exception as _e:  # noqa: BLE001  - the classic page keeps working if v8 fails to load\n    print(f"[cinemack] v8 routes not loaded: {_e!r}", flush=True)\n# v8.1 round 47: Guardian articles for one film (web/guardian_live.py). 404 for everything while GUARDIAN_LIVE is off.\ntry:\n    import guardian_live\n    guardian_live.install(app, _gate, user_of)\nexcept Exception as _e:  # noqa: BLE001\n    print(f"[cinemack] guardian lookups not loaded: {_e!r}", flush=True)\n'), ('web/v8/film.js', "  const sim = h('div', {}, skelShelf()); view.append(sim);\n", "  // v8.1 round 47: Guardian articles for this film (stored first, a live lookup only when none are stored). The server\n  // answers 404 while GUARDIAN_LIVE is off, and then nothing is shown. Text nodes only; every link opens the Guardian.\n  api(`/api/v8/film/${d.id}/press`).then(p => {\n    const arts = (p.articles || []).filter(a => a.url && /^https:\\/\\/(www\\.)?theguardian\\.com\\//.test(a.url));\n    if (!arts.length) return;\n    const stars = (n) => n ? ` \\u00b7 ${'\\u2605'.repeat(Math.round(n))}${'\\u2606'.repeat(5 - Math.round(n))}` : '';\n    main.append(h('section', { class: 'press', 'aria-label': 'In the Guardian' }, h('h2', { style: 'margin:24px 0 8px' }, 'In the Guardian'),\n      h('ul', { class: 'press-list' }, arts.slice(0, 6).map(a => h('li', {}, h('a', { href: a.url, target: '_blank', rel: 'noopener' }, a.headline || 'Article'),\n        h('div', { class: 'small' }, [a.kind === 'review' ? 'Review' : 'Article', a.published, a.byline].filter(Boolean).join(' \\u00b7 ') + stars(a.stars)),\n        a.summary ? h('p', { class: 'small' }, a.summary) : null))),\n      h('p', { class: 'small' }, p.credit || 'From the Guardian')));\n  }).catch(() => {});\n  const sim = h('div', {}, skelShelf()); view.append(sim);\n")]

GROUPS["topup"] = [('tests/test_v81_r46.py', '    def test_how_much_a_top_up_spends(self):\n        self.assertEqual(G.plan_topup(100), 60)                         # 40 always stay unspent\n        self.assertEqual(G.plan_topup(100, reserve=10), 90)\n        self.assertEqual(G.plan_topup(59), 0)                           # under 20 to spend: not worth a run\n        self.assertEqual(G.plan_topup(0), 0)\n        self.assertEqual(G.plan_topup(None), 0)\n\n    def test_top_up_is_off_by_default_and_makes_no_call_while_off(self):\n        seen, ran = [], []\n        msg = G.topup(KEY, opener=opener(300, seen=seen), ps="", runner=lambda *a, **k: ran.append(a), path=self.log)\n        self.assertIn("switched off", msg)\n        self.assertEqual((seen, ran), ([], []))\n        self.assertFalse(os.path.exists(self.log))\n\n    def test_top_up_never_overlaps_another_job(self):\n        for line in ("bash nightly.sh", "python pf5_data.py press --max 400", "python eval_v3.py --tag x", "bash run_final_v8.sh"):\n            seen, ran = [], []\n            msg = G.topup(KEY, enabled=True, opener=opener(300, seen=seen), ps="init\\n" + line + "\\n",\n                          runner=lambda *a, **k: ran.append(a), path=self.log)\n            self.assertIn("another job is running", msg, line)\n            self.assertEqual((seen, ran), ([], []))\n        self.assertIsNone(G.busy("init\\npython tools/v81_guardian_quota.py topup\\nsshd\\n"))   # itself does not count\n\n    def test_top_up_runs_the_nightly_command_with_the_spare_calls(self):\n        ran = []\n\n        class Done:\n            returncode, stdout = 0, "press: 600 films done\\n  reached 260 calls"\n\n        def runner(cmd, **kw):\n            ran.append((cmd, kw.get("cwd")))\n            return Done()\n        msg = G.topup(KEY, enabled=True, opener=opener(300), ps="", runner=runner, path=self.log)\n        self.assertEqual(ran, [(["python", "pf5_data.py", "press", "--max", "260"], G.LIVE)])\n        self.assertIn("ran press --max 260 (exit 0)", msg)\n        self.assertNotIn(KEY, msg)\n        ran.clear()\n        self.assertIn("would run", G.topup(KEY, enabled=True, dry=True, opener=opener(300), ps="", runner=runner, path=self.log))\n        self.assertIn("nothing to spend", G.topup(KEY, enabled=True, opener=opener(45), ps="", runner=runner, path=self.log))\n        self.assertEqual(ran, [])\n\n\n', '    def test_how_much_a_top_up_spends(self):\n        self.assertEqual(G.plan_topup(100), 60)                         # the plan helper\'s own default reserve\n        self.assertEqual(G.plan_topup(100, reserve=10), 90)\n        self.assertEqual(G.plan_topup(59), 0)                           # under 20 to spend: not worth a run\n        self.assertEqual(G.plan_topup(3, reserve=2, min_spend=1), 1)    # round 47: before the reset every call counts\n        self.assertEqual(G.plan_topup(0), 0)\n        self.assertEqual(G.plan_topup(None), 0)\n        self.assertEqual(G.RESET_RESERVE, 2)\n\n    def test_top_up_is_off_by_default_and_makes_no_call_while_off(self):\n        seen, ran = [], []\n        msg = G.topup(KEY, opener=opener(300, seen=seen), ps="", runner=lambda *a, **k: ran.append(a), path=self.log)\n        self.assertIn("switched off", msg)\n        self.assertEqual((seen, ran), ([], []))\n        self.assertFalse(os.path.exists(self.log))\n\n    def test_top_up_never_overlaps_another_job(self):\n        for line in ("bash nightly.sh", "python pf5_data.py press --max 400", "python eval_v3.py --tag x", "bash run_final_v8.sh"):\n            seen, ran = [], []\n            msg = G.topup(KEY, enabled=True, opener=opener(300, seen=seen), ps="init\\n" + line + "\\n",\n                          runner=lambda *a, **k: ran.append(a), path=self.log)\n            self.assertIn("another job is running", msg, line)\n            self.assertEqual((seen, ran), ([], []))\n        self.assertIsNone(G.busy("init\\npython tools/v81_guardian_quota.py topup\\nsshd\\n"))   # itself does not count\n\n    def test_top_up_spends_every_call_left_reviews_first(self):\n        ran, left = [], [300, 288]\n\n        class Done:\n            returncode, stdout = 0, "press: 600 films done\\n  reached 286 calls"\n\n        def runner(cmd, **kw):\n            ran.append((cmd, kw.get("cwd")))\n            return Done()\n\n        def op(req, timeout=0):                                         # 300 left, then 288 after the review fetch\n            return opener(left.pop(0) if left else 0)(req, timeout)\n        msg = G.topup(KEY, enabled=True, opener=op, ps="", runner=runner, path=self.log)\n        self.assertEqual(ran, [(["python", "fetch_free_data.py", "guardian"], G.LIVE),\n                               (["python", "pf5_data.py", "press", "--max", "286"], G.LIVE)])\n        self.assertIn("ran press --max 286 (exit 0)", msg)\n        self.assertNotIn(KEY, msg)\n        ran.clear()\n        self.assertIn("would run", G.topup(KEY, enabled=True, dry=True, opener=opener(300), ps="", runner=runner, path=self.log))\n        self.assertIn("nothing to spend", G.topup(KEY, enabled=True, opener=opener(2), ps="", runner=runner, path=self.log))\n        self.assertEqual(ran, [])\n\n\n'), ('tools/v81_guardian_quota.py', 'RESERVE = 40                # calls always left unspent (the next review fetch, any later live lookup)\nMIN_SPEND = 20              # not worth starting a fetch for fewer calls than this\n', 'RESERVE = 40                # calls always left unspent (the next review fetch, any later live lookup)\nMIN_SPEND = 20              # not worth starting a fetch for fewer calls than this\nRESET_RESERVE = 2           # round 47 (user, 16 Nov): just before the reset every call is spent; 2 stay as a margin\n'), ('tools/v81_guardian_quota.py', 'def topup(key, reserve=RESERVE, dry=False, enabled=None, opener=urllib.request.urlopen, ps=None, runner=subprocess.run,\n          path=None):\n    on = (os.environ.get("GUARDIAN_TOPUP", "0") == "1") if enabled is None else enabled\n    if not on:\n        return "top-up is switched off (GUARDIAN_TOPUP is not 1): nothing done, no call made"\n    b = busy(ps)\n    if b:\n        return f"another job is running ({b}): nothing done"\n    rec = probe(key, path, opener, cap=10 ** 9)\n    n = plan_topup(rec.get("remaining_day"), reserve)\n    if not n:\n        return f"{rec.get(\'remaining_day\')} calls left (reserve {reserve}): nothing to spend"\n    if dry:\n        return f"{rec[\'remaining_day\']} calls left: would run `python pf5_data.py press --max {n}` in {LIVE}"\n    r = runner(["python", "pf5_data.py", "press", "--max", str(n)], cwd=LIVE, capture_output=True, text=True, timeout=7200)\n    tail = "\\n".join((r.stdout or "").strip().splitlines()[-4:])\n    return f"{rec[\'remaining_day\']} calls left: ran press --max {n} (exit {r.returncode})\\n{tail}"', 'def topup(key, reserve=RESET_RESERVE, dry=False, enabled=None, opener=urllib.request.urlopen, ps=None, runner=subprocess.run,\n          path=None):\n    """Round 47 (user decision 16 Nov): run shortly BEFORE the daily reset and spend every call left - first the\n    new-review fetch (a few calls), then the pre-release / news articles with whatever remains. Calls not spent now\n    would be lost at the reset anyway. Off unless GUARDIAN_TOPUP=1; no cron line until the reset time is known."""\n    on = (os.environ.get("GUARDIAN_TOPUP", "0") == "1") if enabled is None else enabled\n    if not on:\n        return "top-up is switched off (GUARDIAN_TOPUP is not 1): nothing done, no call made"\n    b = busy(ps)\n    if b:\n        return f"another job is running ({b}): nothing done"\n    rec = probe(key, path, opener, cap=10 ** 9)\n    if plan_topup(rec.get("remaining_day"), reserve, min_spend=1) == 0:\n        return f"{rec.get(\'remaining_day\')} calls left (margin {reserve}): nothing to spend"\n    if dry:\n        return (f"{rec[\'remaining_day\']} calls left: would run `python fetch_free_data.py guardian`, then "\n                f"`python pf5_data.py press --max <calls left - {reserve}>` in {LIVE}")\n    out = [f"{rec[\'remaining_day\']} calls left before the reset"]\n    r1 = runner(["python", "fetch_free_data.py", "guardian"], cwd=LIVE, capture_output=True, text=True, timeout=3600)\n    out.append(f"new reviews: exit {r1.returncode}")\n    rec2 = probe(key, path, opener, cap=10 ** 9)\n    n = plan_topup(rec2.get("remaining_day"), reserve, min_spend=1)\n    if not n:\n        out.append(f"{rec2.get(\'remaining_day\')} calls left after the reviews: no articles fetched")\n        return "\\n".join(out)\n    r = runner(["python", "pf5_data.py", "press", "--max", str(n)], cwd=LIVE, capture_output=True, text=True, timeout=7200)\n    tail = "\\n".join((r.stdout or "").strip().splitlines()[-4:])\n    out.append(f"{rec2[\'remaining_day\']} calls left: ran press --max {n} (exit {r.returncode})\\n{tail}")\n    return "\\n".join(out)')]


def state(group):
    """'applied', 'ready', or a list of anchor problems."""
    edits = GROUPS[group]
    text, bad, done = {}, [], 0
    for path, old, new in edits:
        if path not in text:
            p = Path(path)
            if not p.exists():
                bad.append(f"{path}: file missing")
                continue
            text[path] = p.read_text(encoding="utf-8")
        t = text[path]
        if new in t and t.count(old) == (1 if old in new else 0):
            done += 1
        elif t.count(old) != 1:
            bad.append(f"{path}: anchor found {t.count(old)} times (want 1): {old.strip()[:70]!r}")
    if done == len(edits):
        return "applied"
    if bad or done:
        return bad or [f"{group}: partly applied ({done}/{len(edits)})"]
    return "ready"


def apply(group, undo=False):
    text = {}
    for path, old, new in GROUPS[group]:
        text.setdefault(path, Path(path).read_text(encoding="utf-8"))
        a, b = (new, old) if undo else (old, new)
        if text[path].count(a) != 1:
            raise SystemExit(f"{group}: {path}: text to replace found {text[path].count(a)} times; nothing written")
        text[path] = text[path].replace(a, b)
    for path, t in text.items():
        Path(path).write_text(t, encoding="utf-8")
    return sorted(text)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if "--list" in argv or not argv:
        for g in GROUPS:
            s = state(g)
            print(f"{g:9s} {s if isinstance(s, str) else 'ANCHOR ' + '; '.join(s)}")
        return 0
    unknown = [g for g in args if g not in GROUPS]
    if unknown:
        print(f"unknown group(s) {unknown}; known: {list(GROUPS)}")
        return 2
    rc = 0
    for g in args:
        s = state(g)
        if "--undo" in argv:
            if s == "applied":
                print(f"{g}: undone in {', '.join(apply(g, undo=True))}")
            else:
                print(f"{g}: not applied; nothing to undo")
        elif s == "applied":
            print(f"{g}: already applied")
        elif s != "ready":
            print(f"{g}: NOT applied - " + "; ".join(s))
            rc = 1
        elif "--check" in argv:
            print(f"{g}: ready")
        else:
            print(f"{g}: patched {', '.join(apply(g))}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
