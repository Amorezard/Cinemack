"""Adds the v8 page hook to web/server.py once (idempotent). The old page and every existing route are unchanged."""
import sys
from pathlib import Path
p = Path(sys.argv[1] if len(sys.argv) > 1 else "web/server.py")
s = p.read_text()
HOOK = ('\n# v8 (9 Nov 2026): new page at /v8 plus profile, friends, history, calendar and alerts routes (web/v8routes.py).\n'
        'try:\n    import v8routes\n    v8routes.install(app, user_of=user_of, account_of=account_of, gate=_gate)\n'
        'except Exception as _e:  # noqa: BLE001  - the classic page keeps working if v8 fails to load\n'
        '    print(f"[cinemack] v8 routes not loaded: {_e!r}", flush=True)\n\n')
if "v8routes.install" in s:
    print("already patched"); sys.exit(0)
anchor = '\nif __name__ == "__main__":'
assert s.count(anchor) == 1, "anchor not found"
p.write_text(s.replace(anchor, HOOK + anchor))
print("patched")
