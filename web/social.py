"""
v8 (9 Nov 2026): profiles, privacy and friends - private by default, checked in one place.

Who can see what (can_view): the owner always; nobody else unless the owner shared that kind with friends AND the
viewer is an accepted friend AND neither has blocked the other. Kinds: profile, watchlist, history, ratings, reviews.
Each defaults to "private". There is no "public" level in v8 on purpose: nothing a person adds is visible to strangers.
Items the owner hid (per film, per kind) are removed from what friends see.

Only signed-in accounts ("acct<id>") can have friends: a browser id is not a person anyone can find.
Friend codes (8 characters, no look-alike letters) are the only way to find someone: no search, no listing.
A request needs the other side to accept; declining, removing and blocking work at any time; a block removes the
friendship and any pending request and stops new ones in both directions.

Tables in logs/social.db (separate from users.db, so deleting an account clears both: delete_user()).
"""
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
DB = os.environ.get("SOCIAL_DB", str(RAG_DIR / "logs" / "social.db"))
KINDS = ("profile", "watchlist", "history", "ratings", "reviews")
LEVELS = ("private", "friends")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L
_lock = threading.Lock()
_C = {}


class SocialError(Exception):
    pass


def conn():
    if DB not in _C:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")
        c.executescript("""
            create table if not exists privacy(user_key text, kind text, level text, primary key (user_key, kind));
            create table if not exists codes(user_key text primary key, code text unique, created_at text);
            create table if not exists friends(a text, b text, status text, requested_by text, created_at text,
              primary key (a, b));
            create table if not exists blocks(blocker text, blocked text, created_at text, primary key (blocker, blocked));
            create table if not exists hidden(user_key text, kind text, tmdb_id integer, primary key (user_key, kind, tmdb_id));
            create table if not exists reviews(user_key text, tmdb_id integer, rating real, text text, created_at text,
              updated_at text, primary key (user_key, tmdb_id));""")
        _C[DB] = c
    return _C[DB]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def is_account(key):
    return isinstance(key, str) and key.startswith("acct") and key[4:].isdigit()


def _pair(x, y):
    return (x, y) if x < y else (y, x)


# ------------------------------------------------------------------ privacy
def privacy(key):
    rows = dict(conn().execute("select kind, level from privacy where user_key = ?", (key,)).fetchall())
    return {k: rows.get(k, "private") for k in KINDS}


def set_privacy(key, changes):
    if not is_account(key):
        raise SocialError("Sign in to share anything with friends.")
    bad = [k for k, v in (changes or {}).items() if k not in KINDS or v not in LEVELS]
    if bad:
        raise SocialError(f"Unknown privacy setting: {', '.join(map(str, bad))}")
    with _lock:
        c = conn()
        for k, v in changes.items():
            c.execute("insert or replace into privacy values (?,?,?)", (key, k, v))
        c.commit()
    return privacy(key)


def hide(key, kind, tmdb_id, hidden=True):
    if kind not in KINDS[1:]:
        raise SocialError("Only watchlist, history, ratings and reviews items can be hidden.")
    with _lock:
        c = conn()
        if hidden:
            c.execute("insert or ignore into hidden values (?,?,?)", (key, kind, int(tmdb_id)))
        else:
            c.execute("delete from hidden where user_key = ? and kind = ? and tmdb_id = ?", (key, kind, int(tmdb_id)))
        c.commit()


def hidden_ids(key, kind):
    return {r[0] for r in conn().execute("select tmdb_id from hidden where user_key = ? and kind = ?", (key, kind))}


# ------------------------------------------------------------------ codes, friends, blocks
def code_of(key):
    if not is_account(key):
        return None
    with _lock:
        c = conn()
        r = c.execute("select code from codes where user_key = ?", (key,)).fetchone()
        if r:
            return r[0]
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            try:
                c.execute("insert into codes values (?,?,?)", (key, code, _now()))
                c.commit()
                return code
            except sqlite3.IntegrityError:
                continue


def key_of_code(code):
    r = conn().execute("select user_key from codes where code = ?", ((code or "").strip().upper(),)).fetchone()
    return r[0] if r else None


def blocked_between(x, y):
    return conn().execute("select 1 from blocks where (blocker = ? and blocked = ?) or (blocker = ? and blocked = ?)",
                          (x, y, y, x)).fetchone() is not None


def status(x, y):
    a, b = _pair(x, y)
    r = conn().execute("select status, requested_by from friends where a = ? and b = ?", (a, b)).fetchone()
    return r if r else (None, None)


def are_friends(x, y):
    return x != y and status(x, y)[0] == "accepted" and not blocked_between(x, y)


def request(key, code):
    if not is_account(key):
        raise SocialError("Sign in to add friends.")
    other = key_of_code(code)
    # the same reply for "no such code" and "blocked", so a block can't be detected by probing codes
    if not other or other == key or blocked_between(key, other):
        raise SocialError("No one with that friend code.")
    st, by = status(key, other)
    if st == "accepted":
        return "already_friends"
    with _lock:
        c = conn()
        a, b = _pair(key, other)
        if st == "pending" and by == other:          # they asked first: asking back accepts
            c.execute("update friends set status = 'accepted' where a = ? and b = ?", (a, b))
            c.commit()
            return "accepted"
        c.execute("insert or replace into friends values (?,?,?,?,?)", (a, b, "pending", key, _now()))
        c.commit()
    return "requested"


def respond(key, other, accept):
    st, by = status(key, other)
    if st != "pending" or by == key:
        raise SocialError("No request from that person.")
    with _lock:
        c = conn()
        a, b = _pair(key, other)
        if accept and not blocked_between(key, other):
            c.execute("update friends set status = 'accepted' where a = ? and b = ?", (a, b))
        else:
            c.execute("delete from friends where a = ? and b = ?", (a, b))
        c.commit()
    return "accepted" if accept else "declined"


def remove(key, other):
    with _lock:
        c = conn()
        a, b = _pair(key, other)
        c.execute("delete from friends where a = ? and b = ?", (a, b))
        c.commit()


def block(key, other):
    if not other or other == key:
        raise SocialError("Can't block that.")
    with _lock:
        c = conn()
        a, b = _pair(key, other)
        c.execute("delete from friends where a = ? and b = ?", (a, b))
        c.execute("insert or ignore into blocks values (?,?,?)", (key, other, _now()))
        c.commit()


def unblock(key, other):
    with _lock:
        c = conn()
        c.execute("delete from blocks where blocker = ? and blocked = ?", (key, other))
        c.commit()


def friends(key):
    rows = conn().execute("select a, b, status, requested_by, created_at from friends where a = ? or b = ?",
                          (key, key)).fetchall()
    out = {"friends": [], "incoming": [], "outgoing": []}
    for a, b, st, by, at in rows:
        other = b if a == key else a
        if blocked_between(key, other):
            continue
        if st == "accepted":
            out["friends"].append({"key": other, "since": at})
        elif by == key:
            out["outgoing"].append({"key": other, "at": at})
        else:
            out["incoming"].append({"key": other, "at": at})
    out["blocked"] = [r[0] for r in conn().execute("select blocked from blocks where blocker = ?", (key,))]
    return out


# ------------------------------------------------------------------ the one access check
def can_view(owner, viewer, kind):
    if kind not in KINDS:
        return False
    if owner and viewer and owner == viewer:
        return True
    if not (is_account(owner) and is_account(viewer)):
        return False
    if blocked_between(owner, viewer) or not are_friends(owner, viewer):
        return False
    return privacy(owner).get(kind) == "friends"


def visible_items(owner, viewer, kind, items, id_key="id"):
    """items filtered for this viewer: [] when the kind is not shared, minus the owner's hidden items."""
    if not can_view(owner, viewer, kind):
        return []
    if owner == viewer:
        return items
    hid = hidden_ids(owner, kind)
    return [i for i in items if i.get(id_key) not in hid]


# ------------------------------------------------------------------ ratings and reviews (owner-written)
def save_review(key, tmdb_id, rating=None, text=""):
    if rating is not None and not (0.5 <= float(rating) <= 5):
        raise SocialError("Ratings go from 0.5 to 5 stars.")
    text = (text or "").strip()[:4000]
    with _lock:
        c = conn()
        old = c.execute("select created_at from reviews where user_key = ? and tmdb_id = ?", (key, int(tmdb_id))).fetchone()
        c.execute("insert or replace into reviews values (?,?,?,?,?,?)",
                  (key, int(tmdb_id), rating, text, old[0] if old else _now(), _now()))
        c.commit()


def delete_review(key, tmdb_id):
    with _lock:
        c = conn()
        c.execute("delete from reviews where user_key = ? and tmdb_id = ?", (key, int(tmdb_id)))
        c.commit()


def reviews_of(key):
    return [{"id": t, "rating": r, "text": x, "updated": u} for t, r, x, u in conn().execute(
        "select tmdb_id, rating, text, updated_at from reviews where user_key = ? order by updated_at desc", (key,))]


def delete_user(key):
    """Account deletion: everything this person has here, and every link to them."""
    with _lock:
        c = conn()
        for sql in ("delete from privacy where user_key = ?", "delete from codes where user_key = ?",
                    "delete from friends where a = ? or b = ?", "delete from blocks where blocker = ? or blocked = ?",
                    "delete from hidden where user_key = ?", "delete from reviews where user_key = ?"):
            c.execute(sql, (key,) * sql.count("?"))
        c.commit()
