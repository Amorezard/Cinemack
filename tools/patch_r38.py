"""Round 38 (12 Nov 2026, on top of round 37): anchored edits, all or nothing.
1. Trakt: the client ID in keys.env never reached the site (start_site.sh does not load keys.env) -> trakt_sync reads
   it itself; sign-in host auth.trakt.tv; Connect Trakt uses Trakt's device-code sign-in (Trakt redirects only to
   https, this site is http): /api/v8/trakt/device/start + /poll, join.js + profile.js.
2. Profile: one Reviews tab (write, filter, sort, friends' reviews via /api/v8/reviews/friends) replaces Ratings and
   Reviews; ?tab=ratings opens it. Settings > Movie and TV preferences: "Your taste" (film and TV kinds).
3. Onboarding taste step starts from the person's saved film groups (it started empty).
4. Pick Tonight "Mind-bending": sci-fi without superhero films (chat filter "mood": "mind").
5. Ask Cinemack show recommendations use TV taste (web/tv_prefs.py install_chat, TV_TASTE=1): server.py records whose
   question is answered (user_prefs.CURRENT["user"]).
Blocks of new code are in tools/r38_blocks.   python tools/patch_r38.py [--check]"""
import sys
from pathlib import Path

BLOCKS = Path(__file__).resolve().parent / "r38_blocks"


def BLOCK(name):
    return (BLOCKS / name).read_text(encoding="utf-8")


EDITS = [
    # ---- Trakt
    ("web/trakt_sync.py", 'CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID", "").strip().strip("\'\\"")\n', BLOCK("trakt_keys.py.txt")),
    ("web/trakt_sync.py", 'AUTH = "https://trakt.tv/oauth/authorize"',
     'AUTH = "https://auth.trakt.tv/oauth/authorize"           # 12 Nov: Trakt\'s sign-in host (developer.trakt.tv)'),
    ("web/trakt_sync.py", BLOCK("old_refresh.txt"),
     'dict({"refresh_token": refresh, "client_id": CLIENT_ID, "grant_type": "refresh_token",\n'
     '                                                                "redirect_uri": REDIRECT if REDIRECT.startswith("https://") else "urn:ietf:wg:oauth:2.0:oob"},\n'
     '                                                               **({"client_secret": CLIENT_SECRET} if CLIENT_SECRET else {}))'),
    ("web/trakt_sync.py", "def disconnect(user):\n", BLOCK("trakt_device.py.txt") + "def disconnect(user):\n"),
    ("web/v8routes.py", "    _tvp.install(app, user_of, gate, _run)\n",
     "    _tvp.install(app, user_of, gate, _run)\n"
     "    import reviews_feed as _rvf                         # 12 Nov: friends' reviews (Profile > Reviews)\n"
     "    _rvf.install(app, user_of, gate, _run)\n"
     "    trakt_sync.install_device(app, user_of, gate, _run)  # 12 Nov: Connect Trakt with a code\n"),
    ("web/v8/join.js", "const STEP = {\n", BLOCK("trakt_connect.js.txt") + "const STEP = {\n"),
    ("web/v8/join.js", BLOCK("old_join_connect.txt"),
     "      else { tr.append(h('p', { class: 'sub' }, 'Import history, ratings and watchlist from Trakt: you get a short code to enter on trakt.tv.'));\n"
     "        traktConnect(tr, () => { CW.items = null; emit('cm:imported'); }); }"),
    ("web/v8/profile.js",
     "    else tc.append(h('button', { class: 'btn primary sm', type: 'button', onclick: async () => { try { const j = await api('/trakt/start'); location.href = j.url; } catch (e) { toast(e.message, null, true); } } }, 'Connect Trakt'));",
     "    else traktConnect(tc, () => { CW.items = null; emit('cm:imported'); });"),
    # ---- Profile: Reviews tab, taste in Settings
    ("web/v8/profile.js", "const TABS = [['overview'", BLOCK("trakt_connect.js.txt") + BLOCK("taste_card.js.txt") + "const TABS = [['overview'"),
    ("web/v8/profile.js",
     "const TABS = [['overview', 'Overview'], ['ratings', 'Ratings'], ['reviews', 'Reviews'], ['history', 'Watch History'], ['friends', 'Friends']];",
     "const TABS = [['overview', 'Overview'], ['reviews', 'Reviews'], ['history', 'Watch History'], ['friends', 'Friends']];   // 12 Nov: Ratings merged into Reviews"),
    ("web/v8/profile.js", "k === 'reviews' && sum.stats.reviews ? h('span', { class: 'count' }, String(sum.stats.reviews)) : null",
     "k === 'reviews' && (sum.stats.ratings || sum.stats.reviews) ? h('span', { class: 'count' }, String(Math.max(sum.stats.ratings || 0, sum.stats.reviews || 0))) : null"),
    ("web/v8/profile.js", "  const t0 = params.get('tab') === 'settings' || tr ? 'settings'",
     "  if (params.get('tab') === 'ratings') params.set('tab', 'reviews');          // 12 Nov: old links to the Ratings tab\n"
     "  const t0 = params.get('tab') === 'settings' || tr ? 'settings'"),
    ("web/v8/profile.js", BLOCK("old_panes.js.txt"), BLOCK("reviews_pane.js.txt")),
    ("web/v8/profile.js",
     "    head(pane, 'Movie and TV preferences', 'Decades and languages gently lift recommendations. Never-recommend genres are a hard limit everywhere.');\n",
     "    head(pane, 'Movie and TV preferences', 'Decades and languages gently lift recommendations. Never-recommend genres are a hard limit everywhere.');\n"
     "    pane.append(tasteCard(me));\n"),
    ("web/v8/app.css", ".tgroups { display: grid;", BLOCK("css.txt") + ".tgroups { display: grid;"),
    # ---- onboarding starts from saved film groups
    ("web/v8/join.js", "const st = { i: 0, groups: new Set(), avoid: new Set(), films: {},",
     "const mg = (S.me && S.me.groups) || [];                // 12 Nov: start from the saved taste\n"
     "  const st = { i: 0, groups: new Set(mg.filter(k => !String(k).startsWith('-'))), avoid: new Set(mg.filter(k => String(k).startsWith('-')).map(k => String(k).slice(1))), films: {},"),
    # ---- Pick Tonight "Mind-bending" without superhero films
    ("web/v8/pick.json", '        "genre": "Science Fiction"\n      },\n      "words": "mind-bending"',
     '        "genre": "Science Fiction",\n        "mood": "mind"\n      },\n      "words": "mind-bending"'),
    ("web/user_prefs.py",
     '    if raw.get("strict") is True:            # v8 Pick tonight: a film whose fact is unknown fails that limit too\n        out["strict"] = True\n',
     '    if raw.get("strict") is True:            # v8 Pick tonight: a film whose fact is unknown fails that limit too\n        out["strict"] = True\n'
     '    if raw.get("mood") in ("mind",):         # 12 Nov: Pick Tonight "Mind-bending" = sci-fi without superhero films\n'
     '        out["mood"] = raw["mood"]\n'),
    ("web/user_prefs.py", '    if c.get("strict"):\n        s["strict"] = True\n',
     '    if c.get("strict"):\n        s["strict"] = True\n    if c.get("mood"):\n        s["mood"] = c["mood"]\n'),
    ("web/user_prefs.py", '"max_year", "only_genres", "services_only")}', '"max_year", "only_genres", "services_only", "mood")}'),
    ("web/user_prefs.py", '    max_y = s.get("max_year")\n',
     '    heroes = set()\n'
     '    if s.get("mood") == "mind":                         # 12 Nov: "Mind-bending" leaves superhero films out\n'
     '        hid = [int(h["id"]) for h in hits if str(h.get("id") or "").isdigit()]\n'
     '        if hid:\n'
     '            heroes = {r[0] for r in _q(_facts(), "select distinct tmdb_id from keywords where name in ("\n'
     '                                      + ",".join("?" * len(_NO_HEROES)) + ") and tmdb_id in (" + ",".join("?" * len(hid)) + ")",\n'
     '                                      list(_NO_HEROES) + hid)}\n'
     '    max_y = s.get("max_year")\n'),
    ("web/user_prefs.py", "        elif only and gm and not (only & gm):\n            out.add(fid)\n",
     "        elif only and gm and not (only & gm):\n            out.add(fid)\n"
     "        elif heroes and fid in heroes:\n            out.add(fid)\n"),
    # ---- whose question (TV taste in chat)
    ("web/server.py",
     '            user_prefs.CURRENT["settings"] = prefs           # v6: the person\'s limits, for chat._personalize\n',
     '            user_prefs.CURRENT["settings"] = prefs           # v6: the person\'s limits, for chat._personalize\n'
     '            user_prefs.CURRENT["user"] = user                # 12 Nov: whose TV taste (web/tv_prefs.py)\n'),
    ("web/server.py", '            finally:\n                user_prefs.CURRENT["settings"] = None\n                clear_ctx()\n',
     '            finally:\n                user_prefs.CURRENT["settings"] = None\n                user_prefs.CURRENT["user"] = None\n                clear_ctx()\n'),
]


def main():
    check = "--check" in sys.argv
    if "install_device" in Path("web/v8routes.py").read_text(encoding="utf-8"):
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
