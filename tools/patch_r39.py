"""Round 39 (12 Nov 2026): Trakt requests carry a User-Agent. Round 38's live test got HTTP 403 from
api.trakt.tv/oauth/device/code with the client ID from keys.env; urllib's default "Python-urllib/3.x" agent is a common
reason for a 403 from Cloudflare in front of Trakt. All or nothing.   python tools/patch_r39.py [--check]"""
import sys
from pathlib import Path

EDITS = [
    ("web/trakt_sync.py", 'API = "https://api.trakt.tv"\n',
     'API = "https://api.trakt.tv"\n'
     'UA = "Cinemack/1.0 (Merrimack College student research project)"   # 12 Nov: Trakt behind Cloudflare\n'),
    ("web/trakt_sync.py", 'headers=dict({"Content-Type": "application/json"},',
     'headers=dict({"Content-Type": "application/json", "User-Agent": UA},'),
]


def main():
    if "User-Agent" in Path("web/trakt_sync.py").read_text(encoding="utf-8"):
        print("already patched; nothing written")
        return 1
    text, bad = {}, 0
    for path, old, new in EDITS:
        text.setdefault(path, Path(path).read_text(encoding="utf-8"))
        n = text[path].count(old)
        if n != 1:
            print(f"ANCHOR {path}: found {n} times (want 1): {old.strip()[:70]!r}")
            bad = 1
            continue
        text[path] = text[path].replace(old, new)
    if bad:
        print("nothing written")
        return 1
    if "--check" in sys.argv:
        print("all anchors match (check only)")
        return 0
    for path, t in text.items():
        Path(path).write_text(t, encoding="utf-8")
        print(f"patched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
