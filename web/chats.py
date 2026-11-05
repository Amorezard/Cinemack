"""
v3: saved conversations per browser user (logs/chats.db).

The page has always kept the conversation only in the open tab: a reload or "New chat" lost
it. Now every finished turn is stored under the anonymous browser id (X-MB-User, the same id
the taste profile uses) and the conversation id (X-MB-Conv), with what the page needs to show
it again and carry on: the question, the answer, the film cards, and the conversation state
(recent titles + conv.py state) after that turn. Reopening a chat restores the state, so
"the first one" or "compare it with Barbie" still resolve.

Not an account: no login, nothing personal. Whoever has the browser id sees its chats; each
request can only read or delete chats stored under its own id.

    python chats.py stats            # users, chats, turns
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

DB = Path(os.environ.get("CHATS_DB", Path(__file__).resolve().parent.parent / "logs" / "chats.db"))
MAX_CHATS_LISTED = 50
MAX_TURNS_KEPT = 60            # per conversation; older turns stay in the log, not in the reopen view
_lock = threading.Lock()
_conn = {"c": None}


def conn():
    if _conn["c"] is None:
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB), check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        c.executescript("""
            create table if not exists chats(conv_id text primary key, user_id text, title text,
              created_at real, updated_at real, turns integer default 0, state_json text);
            create index if not exists chats_user on chats(user_id, updated_at);
            create table if not exists turns(conv_id text, seq integer, message_id text, question text,
              answer text, films_json text, cached integer, created_at real, primary key (conv_id, seq));""")
        _conn["c"] = c
    return _conn["c"]


def _clean_id(s, n=64):
    return "".join(ch for ch in (s or "")[:n] if ch.isalnum() or ch in "-_")


def save_turn(user_id, conv_id, question, answer, films=None, recent_titles=None, conv=None,
              message_id="", cached=False):
    """Append one finished turn. Never raises (saving must not break an answer)."""
    user_id, conv_id = _clean_id(user_id), _clean_id(conv_id, 40)
    if not (user_id and conv_id and question and answer):
        return False
    try:
        now = time.time()
        state = json.dumps({"recent_titles": recent_titles or [], "conv": conv}, default=str)
        if len(state) > 60000:                   # a runaway conv state is not worth keeping
            state = json.dumps({"recent_titles": recent_titles or [], "conv": None})
        cards = json.dumps(films or [], default=str)[:60000]
        with _lock:
            c = conn()
            row = c.execute("select user_id, turns from chats where conv_id = ?", (conv_id,)).fetchone()
            if row and row[0] != user_id:
                return False                     # someone else's conversation id: never mix
            seq = (row[1] if row else 0) + 1
            if not row:
                c.execute("insert into chats values (?,?,?,?,?,?,?)",
                          (conv_id, user_id, " ".join(question.split())[:80], now, now, 0, state))
            c.execute("insert or replace into turns values (?,?,?,?,?,?,?,?)",
                      (conv_id, seq, (message_id or "")[:40], question[:4000], answer[:20000], cards,
                       int(bool(cached)), now))
            c.execute("update chats set updated_at = ?, turns = ?, state_json = ? where conv_id = ?",
                      (now, seq, state, conv_id))
            c.commit()
        return True
    except Exception as e:
        print(f"[movie-buff] chat save failed: {e!r}", flush=True)
        return False


def list_chats(user_id, limit=MAX_CHATS_LISTED):
    user_id = _clean_id(user_id)
    if not user_id:
        return []
    with _lock:
        rows = conn().execute("select conv_id, title, created_at, updated_at, turns from chats "
                              "where user_id = ? order by updated_at desc limit ?", (user_id, limit)).fetchall()
    return [{"id": r[0], "title": r[1], "created": r[2], "updated": r[3], "turns": r[4]} for r in rows]


def load_chat(user_id, conv_id):
    user_id, conv_id = _clean_id(user_id), _clean_id(conv_id, 40)
    with _lock:
        c = conn()
        row = c.execute("select title, state_json, updated_at from chats where conv_id = ? and user_id = ?",
                        (conv_id, user_id)).fetchone()
        if not row:
            return None
        turns = c.execute("select seq, message_id, question, answer, films_json from turns where conv_id = ? "
                          "order by seq desc limit ?", (conv_id, MAX_TURNS_KEPT)).fetchall()
    state = json.loads(row[1] or "{}")
    return {"id": conv_id, "title": row[0], "updated": row[2],
            "recent_titles": state.get("recent_titles") or [], "conv": state.get("conv"),
            "turns": [{"id": m, "question": q, "answer": a, "films": json.loads(f or "[]")}
                      for _s, m, q, a, f in reversed(turns)]}


def delete_chat(user_id, conv_id):
    user_id, conv_id = _clean_id(user_id), _clean_id(conv_id, 40)
    with _lock:
        c = conn()
        n = c.execute("delete from chats where conv_id = ? and user_id = ?", (conv_id, user_id)).rowcount
        if n:
            c.execute("delete from turns where conv_id = ?", (conv_id,))
        c.commit()
    return bool(n)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        c = conn()
        u, ch, t = c.execute("select count(distinct user_id), count(*), coalesce(sum(turns), 0) from chats").fetchone()
        print(f"{DB}: {u} browser users, {ch} chats, {t} turns")
    else:
        print(__doc__)
