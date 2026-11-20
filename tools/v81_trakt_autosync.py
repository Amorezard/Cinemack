#!/usr/bin/env python
"""v8.1 round 49 (17 Nov 2026): refresh connected Trakt accounts without anyone pressing "Sync".

    python tools/v81_trakt_autosync.py            dry run: counts the connected accounts and what would be done.
                                                  Reads logs/trakt.db only. No network call, nothing written.
    python tools/v81_trakt_autosync.py --run      does it. Refused unless TRAKT_AUTOSYNC=1 is set AND Trakt is set up.

What "it" is: WATCH HISTORY ONLY. The Sync button (web/trakt_sync.sync) also copies ratings and the watchlist, and it
does so by writing over what is there: a rating arrives with an empty review text, and a watchlist film is set back
to "want". Pressed by the person that is their choice; run every night it would undo things they did in Cinemack
(a review they wrote here, a film they took off the list). History rows carry Trakt's own ids, so importing them
twice changes nothing. Ratings and watchlist stay with the button until an "only add what is missing" rule exists.

Order: the account synced longest ago first. Accounts synced in the last MIN_HOURS are skipped. A pause between
accounts; the run stops after MAX_ERRORS failures in a row (Trakt down or the app's access withdrawn).
The log (logs/trakt_autosync.jsonl) holds a salted hash of the account, counts and the error text - never a token,
never the account key. Round 55: the owner said yes (20 Nov); nightly.sh runs it with TRAKT_AUTOSYNC=1, history + "only add what is missing".
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

MIN_HOURS = 20
PAUSE_S = 2.0
MAX_ERRORS = 3
MAX_ACCOUNTS = 200
LOG = ROOT / "logs" / "trakt_autosync.jsonl"


def enabled() -> bool:
    return os.environ.get("TRAKT_AUTOSYNC", "0") == "1"


def tag(user_key: str) -> str:
    """Short name for the log: not reversible, the same for one account within this install."""
    salt = str(ROOT).encode()
    return hashlib.sha256(salt + b"|" + (user_key or "").encode()).hexdigest()[:10]


def _age_h(last_sync, now: datetime.datetime):
    try:
        t = datetime.datetime.strptime((last_sync or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return (now - t).total_seconds() / 3600.0


def plan(rows, now=None, min_hours=MIN_HOURS, limit=MAX_ACCOUNTS):
    """rows: (user_key, last_sync). -> (due user keys, oldest first; number skipped as recent)."""
    now = now or datetime.datetime.now()
    due, recent = [], 0
    for user, last in rows:
        age = _age_h(last, now)
        if age is not None and 0 <= age < min_hours:
            recent += 1
        else:
            due.append((age if age is not None else float("inf"), user))
    due.sort(key=lambda x: (-x[0], x[1]))
    return [u for _a, u in due][:limit], recent


def history_only(T, user):
    """The history part of trakt_sync.sync and nothing else; stamps last_sync."""
    items = T._get_all(user, "/sync/history/movies") + T._get_all(user, "/sync/history/episodes")
    n = T._import_by_id(user, T._rows_history(items))
    with T._lock:
        c = T.conn()
        c.execute("update tokens set last_sync = ? where user_key = ?", (time.strftime("%Y-%m-%dT%H:%M:%S"), user))
        c.commit()
    return n


def run(users, sync_fn, sleep=time.sleep, pause=PAUSE_S, max_errors=MAX_ERRORS, log=None):
    """-> {"done", "failed", "stopped_early"}. sync_fn(user) returns anything JSON-able or raises."""
    done = failed = streak = 0
    stopped = False
    for i, u in enumerate(users):
        rec = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "account": tag(u)}
        try:
            rec["history"] = sync_fn(u)
            done, streak = done + 1, 0
        except Exception as e:  # noqa: BLE001  - one account's failure must not stop the others
            rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            failed, streak = failed + 1, streak + 1
        if log:
            log(rec)
        if streak >= max_errors:
            stopped = i + 1 < len(users)
            break
        if i + 1 < len(users):
            sleep(pause)
    return {"done": done, "failed": failed, "stopped_early": stopped}


def _write(rec, path=LOG):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _rows(db_path):
    import sqlite3
    if not Path(db_path).exists():
        return []
    c = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10)
    try:
        return c.execute("select user_key, last_sync from tokens").fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_ACCOUNTS)
    a = ap.parse_args(argv)
    db = os.environ.get("TRAKT_DB", str(ROOT / "logs" / "trakt.db"))
    rows = _rows(db)
    due, recent = plan(rows, limit=max(1, a.max))
    print("Trakt accounts connected: %d | synced in the last %d h (skipped): %d | due: %d"
          % (len(rows), MIN_HOURS, recent, len(due)))
    if not a.run:
        print("dry run: no call made, nothing written. A real run copies watch history only (see the top of this file).")
        return 0
    if not enabled():
        print("NOT run: TRAKT_AUTOSYNC is not 1.")
        return 2
    import trakt_sync as T
    if not T.enabled():
        print("NOT run: Trakt is not set up on this server (no client id).")
        return 2
    # round 55 (owner yes, 20 Nov): history + ratings / watchlist "only add what is missing" (web/trakt_missing.py);
    # TRAKT_AUTOSYNC_HISTORY_ONLY=1 keeps the round-49 behaviour.
    if os.environ.get("TRAKT_AUTOSYNC_HISTORY_ONLY", "0") == "1":
        fn = lambda u: history_only(T, u)  # noqa: E731
    else:
        import trakt_missing
        fn = lambda u: trakt_missing.sync_missing(T, u)  # noqa: E731
    out = run(due, fn, log=_write)
    print("done: %(done)d | failed: %(failed)d | stopped early: %(stopped_early)s" % out)
    return 0 if not out["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
