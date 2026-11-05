"""
v6 (21 Oct): accounts. A username and password replace the anonymous browser id for people who
sign up; guests keep using the site exactly as before.

    users      id, username, display name, password hash, created, last login
    sessions   SHA-256 of a random 32-byte token (the token itself lives only in the browser's
               HttpOnly, SameSite=Lax cookie), user id, expiry (30 days); logout deletes it
    login_fail failed tries; 5 per username per 15 minutes, then "try again later"

Every other part of the site keys its data by one string, the "user key": the browser id for a
guest, "acct<id>" for a signed-in account (server.user_of). So chats, film choices, the taste
profile, settings and memory all follow the account once someone signs in.

Password hash: Argon2id (argon2-cffi, OWASP minimum m=19 MiB, t=2, p=1) when the package is
installed, otherwise scrypt from Python's standard library at OWASP's setting (N=2^17, r=8, p=1).
Both are stored with their parameters, so either can verify the other's hashes later.

The site is plain HTTP, so the password crosses the network unencrypted and the cookie cannot be
marked Secure: the sign-up form says not to reuse a password, and the access code stays as the
first gate.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(os.environ.get("USERS_DB", Path(__file__).resolve().parent.parent / "logs" / "users.db"))
COOKIE = "mb_session"
SESSION_DAYS = 30
MAX_FAILS, FAIL_WINDOW = 5, 15 * 60
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
MIN_PASSWORD = 8

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    _ARGON = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
except ImportError:                       # not installed: scrypt (standard library) instead
    _ARGON = None

_lock = threading.Lock()
_C = {"c": None}


def conn():
    if _C["c"] is None:
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB), check_same_thread=False, timeout=30)
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        c.executescript("""
            create table if not exists accounts(id integer primary key autoincrement, username text unique collate nocase,
              display_name text, pw_hash text, created_at real, last_login real);
            create table if not exists sessions(token_sha text primary key, account_id integer, created_at real,
              expires_at real);
            create index if not exists sessions_acct on sessions(account_id);
            create table if not exists login_fail(username text collate nocase, at real);
            create index if not exists login_fail_u on login_fail(username, at);""")
        _C["c"] = c
    return _C["c"]


def user_key(account_id):
    return f"acct{int(account_id)}"


# ------------------------------------------------------------------ passwords
def hash_password(pw):
    if _ARGON is not None:
        return _ARGON.hash(pw)
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 17, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32)
    return "scrypt$17$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(pw, stored):
    if not stored:
        return False
    if stored.startswith("scrypt$"):
        try:
            _, ln, r, p, salt, dk = stored.split("$")
            got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2 ** int(ln), r=int(r), p=int(p),
                                 maxmem=256 * 1024 * 1024, dklen=32)
            return hmac.compare_digest(got, base64.b64decode(dk))
        except (ValueError, TypeError):
            return False
    if _ARGON is None:
        return False                      # an Argon2 hash but the package is gone
    try:
        return _ARGON.verify(stored, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ sessions
def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(account_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        c = conn()
        c.execute("delete from sessions where expires_at < ?", (now,))
        c.execute("insert into sessions values (?,?,?,?)", (_sha(token), account_id, now, now + SESSION_DAYS * 86400))
        c.execute("update accounts set last_login = ? where id = ?", (now, account_id))
        c.commit()
    return token


def account_for(token):
    """The signed-in account for a cookie value, or None."""
    if not token or len(token) > 100:
        return None
    with _lock:
        row = conn().execute("select a.id, a.username, a.display_name from sessions s join accounts a on a.id = s.account_id "
                             "where s.token_sha = ? and s.expires_at > ?", (_sha(token), time.time())).fetchone()
    return {"id": row[0], "username": row[1], "display_name": row[2] or row[1]} if row else None


def end_session(token):
    if token:
        with _lock:
            conn().execute("delete from sessions where token_sha = ?", (_sha(token),))
            conn().commit()


# ------------------------------------------------------------------ sign up / log in
class AccountError(ValueError):
    pass


def sign_up(username, password, display_name=""):
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AccountError("Usernames are 3-32 letters, numbers, dots, dashes or underscores.")
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"Use at least {MIN_PASSWORD} characters for the password.")
    if len(password) > 200:
        raise AccountError("That password is too long.")
    if password.lower() == username.lower():
        raise AccountError("The password can't be the same as the username.")
    h = hash_password(password)
    with _lock:
        c = conn()
        try:
            cur = c.execute("insert into accounts(username, display_name, pw_hash, created_at) values (?,?,?,?)",
                            (username, (display_name or "").strip()[:40] or None, h, time.time()))
        except sqlite3.IntegrityError:
            raise AccountError("That username is taken.") from None
        c.commit()
        return cur.lastrowid


def log_in(username, password):
    """Account id, or raises AccountError (wrong details, or too many tries)."""
    username = (username or "").strip()[:32]
    now = time.time()
    with _lock:
        c = conn()
        c.execute("delete from login_fail where at < ?", (now - FAIL_WINDOW,))
        fails = c.execute("select count(*) from login_fail where username = ?", (username,)).fetchone()[0]
        c.commit()
        if fails >= MAX_FAILS:
            raise AccountError("Too many tries. Wait 15 minutes and try again.")
        row = c.execute("select id, pw_hash from accounts where username = ?", (username,)).fetchone()
    ok = check_password(password or "", row[1]) if row else (hash_password("timing-pad") and False)
    if not ok:
        with _lock:
            conn().execute("insert into login_fail values (?,?)", (username, now))
            conn().commit()
        raise AccountError("Wrong username or password.")
    if _ARGON is not None and row[1].startswith("scrypt$"):        # upgrade to Argon2id when it becomes available
        with _lock:
            conn().execute("update accounts set pw_hash = ? where id = ?", (hash_password(password), row[0]))
            conn().commit()
    return row[0]


def set_display_name(account_id, name):
    with _lock:
        conn().execute("update accounts set display_name = ? where id = ?", ((name or "").strip()[:40] or None, account_id))
        conn().commit()


# ------------------------------------------------------------------ moving and deleting data
def _rag():
    return Path(__file__).resolve().parent.parent


def _tables():
    """(database path, table, user column) for everything stored per user key."""
    logs = _rag() / "logs"
    return [
        (Path(os.environ.get("CHATS_DB", logs / "chats.db")), "chats", "user_id"),
        (DB, "film_prefs", "user_id"),
        (DB, "stated_prefs", "user_id"),
        (DB, "watch", "user_id"),
        (DB, "profiles", "user_key"),
        (Path(os.environ.get("MEMORY_DB", logs / "memory.db")), "notes", "user_key"),
    ]


def move_data(old_key, new_key):
    """Sign-up with "keep this browser's history": every row of the guest id goes to the account.
    Where both have a row for the same film/setting, the account's own row is kept."""
    moved = {}
    if not old_key or not new_key or old_key == new_key:
        return moved
    for path, table, col in _tables():
        if not path.exists():
            continue
        c = sqlite3.connect(str(path), timeout=30)
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        try:
            n = c.execute(f"update or ignore {table} set {col} = ? where {col} = ?", (new_key, old_key)).rowcount
            c.execute(f"delete from {table} where {col} = ?", (old_key,))
            c.commit()
            moved[table] = n
        except sqlite3.Error:
            pass                                        # table not created yet
        finally:
            c.close()
    return moved


def delete_data(key):
    """Everything stored under a user key: chats (and their turns), film choices, profile, settings,
    memory. The request logs keep questions without the user key's personal data."""
    gone = {}
    for path, table, col in _tables():
        if not path.exists():
            continue
        c = sqlite3.connect(str(path), timeout=30)
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        try:
            if table == "chats":
                c.execute("delete from turns where conv_id in (select conv_id from chats where user_id = ?)", (key,))
            gone[table] = c.execute(f"delete from {table} where {col} = ?", (key,)).rowcount
            c.commit()
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return gone


def delete_account(account_id, password):
    with _lock:
        row = conn().execute("select pw_hash from accounts where id = ?", (account_id,)).fetchone()
    if not row or not check_password(password or "", row[0]):
        raise AccountError("That password isn't right.")
    gone = delete_data(user_key(account_id))
    with _lock:
        c = conn()
        c.execute("delete from sessions where account_id = ?", (account_id,))
        c.execute("delete from accounts where id = ?", (account_id,))
        c.commit()
    return gone
