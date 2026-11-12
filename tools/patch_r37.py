"""Round 37 (12 Nov 2026, on top of round 36 = commit 355a0ec): anchored edits, all or nothing.
1. web/v8/app.css: .field align-content: start (a field without a hint beside one with a hint no longer drops its input:
   onboarding "Age rating limit" / "Longest film").
2. Trakt export upload: trakt_sync.import_export, Upload.name, kind "trakt" on /api/v8/import, join.js + profile.js
   (Settings > Imports); no Trakt app key needed. "Connect Trakt" text explains why it is unavailable.
3. TV in onboarding: web/tv_prefs.py routes (from v8routes.install), join.js "And TV?"; tvdata.pick leaves out shows
   marked watching / loved / not for me and lifts shows that fit the person's TV kinds or loved shows.
Blocks of new code are in tools/r37_blocks.   python tools/patch_r37.py [--check]"""
import sys
from pathlib import Path

BLOCKS = Path(__file__).resolve().parent / "r37_blocks"


def BLOCK(name):
    return (BLOCKS / name).read_text(encoding="utf-8")


EDITS = [
    # round 37: form alignment, Trakt export upload, TV in onboarding (web/tv_prefs.py)
    ("web/v8/app.css", ".field { display: grid; gap: 6px; margin-bottom: var(--sp-4); }",
     ".field { display: grid; gap: 6px; margin-bottom: var(--sp-4); align-content: start; }"),
    ("web/trakt_sync.py", "def disconnect(user):\n", BLOCK("trakt_export.py.txt") + "def disconnect(user):\n"),
    ("web/v8routes.py", '    kind: str = "netflix"          # netflix | letterboxd_watched\n',
     '    kind: str = "netflix"          # netflix | letterboxd_watched | trakt (12 Nov: Trakt export JSON)\n'
     '    name: str = ""                 # 12 Nov: the file name (Trakt watchlist files are told apart by it)\n'),
    ("web/v8routes.py",
     "        def run():\n            rows = history.parse_netflix(body.text) if body.kind == \"netflix\" else history.parse_letterboxd_watched(body.text)\n",
     "        def run():\n"
     "            if body.kind == \"trakt\":                  # 12 Nov: Trakt's export file, no Trakt app key needed\n"
     "                return trakt_sync.import_export(key, body.text, body.name)\n"
     "            rows = history.parse_netflix(body.text) if body.kind == \"netflix\" else history.parse_letterboxd_watched(body.text)\n"),
    ("web/v8routes.py", '    @app.get("/api/v8/similar/{fid}")\n',
     "    import tv_prefs as _tvp                             # 12 Nov: TV taste from onboarding\n"
     "    _tvp.install(app, user_of, gate, _run)\n\n"
     '    @app.get("/api/v8/similar/{fid}")\n'),
    ("web/tvdata.py", "    seen = _watched_or_followed(user) | {int(x) for x in exclude}\n",
     "    seen = _watched_or_followed(user) | {int(x) for x in exclude}\n"
     "    try:                                                # 12 Nov: TV taste from onboarding (web/tv_prefs.py)\n"
     "        import tv_prefs as _tvp\n"
     "        seen |= _tvp.skip_ids(user) if user else set()\n"
     "    except ImportError:\n"
     "        _tvp = None\n"),
    ("web/tvdata.py", "    scored = []\n    for sid in keep:\n",
     "    tvb = _tvp.boost(user, keep) if _tvp is not None and user else {}\n    scored = []\n    for sid in keep:\n"),
    ("web/tvdata.py", "        scored.append((-s, sid))\n", "        scored.append((-(s + tvb.get(sid, 0.0)), sid))\n"),
    ("web/v8/join.js", "const STEP = {\n", BLOCK("trakt_files.js.txt") + "const STEP = {\n"),
    ("web/v8/join.js", "      box.append(row);\n    }\n  },\n  limits(b, st, next, back) {\n",
     "      box.append(row);\n    }\n" + BLOCK("join_tv.js.txt") + "  },\n  limits(b, st, next, back) {\n"),
    ("web/v8/join.js",
     "      upload('letterboxd_watched', 'Letterboxd watched or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'));",
     "      upload('letterboxd_watched', 'Letterboxd watched or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'),\n"
     "      traktFiles(res, () => { CW.items = null; emit('cm:imported'); }));"),
    ("web/v8/join.js", "h('h3', {}, 'Trakt'), h('p', { class: 'muted small' }, 'Checking…')",
     "h('h3', {}, 'Trakt account'), h('p', { class: 'muted small' }, 'Checking…')"),
    ("web/v8/join.js", "if (!t.available) tr.append(h('p', { class: 'muted small' }, 'Trakt is not set up on this server.'));",
     "if (!t.available) tr.append(h('p', { class: 'muted small' }, 'Signing in with Trakt is not set up on this server yet. The Trakt export above brings in the same history, ratings and watchlist.'));"),
    ("web/v8/profile.js", "const TABS = [['overview'", BLOCK("trakt_files.js.txt") + "const TABS = [['overview'"),
    ("web/v8/profile.js",
     "      upload('letterboxd_watched', 'Letterboxd watched films or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'), res);",
     "      upload('letterboxd_watched', 'Letterboxd watched films or diary', 'From your Letterboxd data export: watched.csv or diary.csv.'),\n"
     "      traktFiles(res, () => { CW.items = null; emit('cm:imported'); }), res);"),
    ("web/v8/profile.js", "if (!t.available) tc.append(h('p', { class: 'muted small' }, 'Trakt is not set up on this server.'));",
     "if (!t.available) tc.append(h('p', { class: 'muted small' }, 'Signing in with Trakt is not set up on this server yet. The Trakt export above brings in the same history, ratings and watchlist.'));"),
]


def main():
    check = "--check" in sys.argv
    if "tv_prefs" in Path("web/v8routes.py").read_text(encoding="utf-8"):
        print("already patched; nothing written")
        return 1
    text, bad = {}, 0
    for path, old, new in EDITS:
        if path not in text:
            text[path] = Path(path).read_text(encoding="utf-8")
        n = text[path].count(old)
        if n != 1:
            print(f"ANCHOR {path}: found {n} times (want 1): {old.strip()[:70]!r}")
            bad = 1
            continue
        text[path] = text[path].replace(old, new)
    if bad:
        print("nothing written")
        return 1
    if check:
        print("all anchors match (check only)")
        return 0
    for path, t in text.items():
        Path(path).write_text(t, encoding="utf-8")
        print(f"patched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
