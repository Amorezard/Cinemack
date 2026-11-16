"""Round 46 (16 Nov 2026), v8.1 worktree only: the one edit this round makes to a file that already exists.
An edit is an exact text anchor that must be found exactly once; a group is applied whole or not at all, and a
group that is already applied is skipped (same machinery as tools/patch_r45.py).

    python tools/patch_r46.py --list | --check GROUP | GROUP | --undo GROUP

Groups
  dots   6.1  web/v8/app.css: the home banner dots no longer animate `width` (the one failure of the 15 Nov motion
              audit). The dot is always 24 x 8 and a clip-path shows 8 px of it, or all of it for the current
              film. Rendered before and after at 2x: the two pictures differ by at most 1 of 255 per channel.
"""
import sys
from pathlib import Path

GROUPS = {}

GROUPS["dots"] = [
    ("web/v8/app.css",
     '.hh-dots button::before { content: ""; width: 8px; height: 8px; border-radius: 4px; background: rgba(255, 255, 255, .4); '
     'transition: width var(--t-med) var(--ease), background var(--t-med); }\n'
     '.hh-dots button[aria-current="true"]::before { width: 24px; background: var(--accent); }\n',
     '.hh-dots button::before { content: ""; width: 24px; height: 8px; background: rgba(255, 255, 255, .4); '
     'clip-path: inset(0 8px round 4px); transition: clip-path var(--t-med) var(--ease), background var(--t-med); }\n'
     '.hh-dots button[aria-current="true"]::before { clip-path: inset(0 0 round 4px); background: var(--accent); }\n'),
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
