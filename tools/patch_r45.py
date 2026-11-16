"""Round 45 (15 Nov 2026), v8.1 worktree only: the few edits this round makes to files that already exist.
Every edit is an exact text anchor that must be found exactly once; a group is applied whole or not at all, and a
group that is already applied is skipped. New files are not handled here (the round's package carries them).

    python tools/patch_r45.py --list                 groups and their state (applied / ready / ANCHOR problem)
    python tools/patch_r45.py --check GROUP [...]    exit 0 if every named group is ready or already applied
    python tools/patch_r45.py GROUP [...]            apply the named groups
    python tools/patch_r45.py --undo GROUP [...]     put the old text back (exact reverse)

Groups
  cache     3.4  web/user_prefs.py: strict mode is part of the search-cache key (the cache tests found that a search
                 stored in normal mode could be replayed for the same limits in strict mode)
  motion    6.1  web/v8/app.css: the kit's four motion tokens; the two older tokens now point at them (same values)
"""
import sys
from pathlib import Path

GROUPS = {}

GROUPS["cache"] = [
    ("web/user_prefs.py",
     '    if settings.get("genres_all"):                    # round 27 (only when on, so older cache keys stay the same)\n'
     '        f["genres_all"] = True\n',
     '    if settings.get("genres_all"):                    # round 27 (only when on, so older cache keys stay the same)\n'
     '        f["genres_all"] = True\n'
     '    if settings.get("strict"):                        # v8.1 slice 3.4 (only when on): unknown facts fail in strict mode,\n'
     '        f["strict"] = True                            # so a search stored without it must not be replayed with it\n'),
]

GROUPS["motion"] = [
    ("web/v8/app.css",
     "  --t-fast: 150ms;  --t-med: 220ms;  --ease: cubic-bezier(.2, .7, .2, 1);\n",
     "  /* v8.1 slice 6.1: the kit's motion tokens (docs/UX_MOTION_SPEC.md). --t-fast / --t-med keep their values. */\n"
     "  --motion-instant: 100ms;  --motion-select: 150ms;  --motion-card: 220ms;  --motion-panel: 300ms;\n"
     "  --t-fast: var(--motion-select);  --t-med: var(--motion-card);  --ease: cubic-bezier(.2, .7, .2, 1);\n"),
]


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
