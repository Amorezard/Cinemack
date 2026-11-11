"""Email alerts (post-v8 workstream F, "email only in a sandbox"; built 11 Nov 2026, round 27).

A signed-in person can add an email address in Profile > Settings > Notifications. Nothing is sent to an address
until they type the 6-digit code mailed to it (so nobody can sign someone else up), and every email has a one-click
unsubscribe link. Then once a day (nightly.sh -> tools/send_digests.py) they get ONE digest of their new in-app alerts
(a saved film is out or starts streaming, a followed show has a dated or new season). An alert is emailed at most
once (table `sent`); an opted-out or unverified address gets nothing; muted films never make alerts at all.

Sandbox by default: EMAIL_SANDBOX=1 (the default) writes each email as a .eml file to logs/email_outbox/ instead of
sending it. Real sending needs EMAIL_SANDBOX=0 and SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM in
keys.env, and SITE_URL for the unsubscribe link. Push notifications are not offered: the browser Push API only works
on HTTPS pages, and the site is plain HTTP for now.
"""
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from email.message import EmailMessage
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("EMAIL_DB", RAG / "logs" / "email.db"))
OUTBOX = Path(os.environ.get("EMAIL_OUTBOX", RAG / "logs" / "email_outbox"))
SANDBOX = os.environ.get("EMAIL_SANDBOX", "1") != "0"
SITE_URL = os.environ.get("SITE_URL", "REDACTED").rstrip("/")
R_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
CODE_TTL = 3600
MAX_CODES_PER_HOUR = 3
_lock = threading.Lock()
_C = {}


def conn():
    if "c" not in _C:
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB), check_same_thread=False, timeout=30)
        c.executescript("""
            create table if not exists emails(user_key text primary key, email text, verified integer default 0,
              code_hash text, code_at real, codes_sent text default '', digest integer default 0, unsub text,
              created_at real, last_sent_at real);
            create table if not exists sent(user_key text, alert_key text, sent_at real, primary key (user_key, alert_key));""")
        _C["c"] = c
    return _C["c"]


def _h(code, user):
    return hashlib.sha256(f"{user}|{code}".encode()).hexdigest()


def status(user):
    with _lock:
        r = conn().execute("select email, verified, digest, last_sent_at from emails where user_key = ?", (user,)).fetchone()
    if not r:
        return {"email": None, "verified": False, "digest": False, "sandbox": SANDBOX}
    e = r[0]
    masked = e[0] + "***" + e[e.index("@"):] if "@" in e else e
    return {"email": masked, "verified": bool(r[1]), "digest": bool(r[2]), "last_sent": r[3], "sandbox": SANDBOX}


def send(to, subject, body):
    """Send (or, in the sandbox, write logs/email_outbox/<time>.eml). Returns where it went."""
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM", "Cinemack <no-reply@cinemack.invalid>")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if SANDBOX or not os.environ.get("SMTP_HOST"):
        OUTBOX.mkdir(parents=True, exist_ok=True)
        p = OUTBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.eml"
        p.write_bytes(bytes(msg))
        return str(p)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(msg)
    return "smtp"


def set_email(user, email):
    """Start (or restart) verification: (ok, message)."""
    email = (email or "").strip()
    if not user or not str(user).startswith("acct"):
        return False, "Sign in to get email alerts."
    if not R_EMAIL.match(email):
        return False, "That doesn't look like an email address."
    now = time.time()
    with _lock:
        c = conn()
        r = c.execute("select codes_sent from emails where user_key = ?", (user,)).fetchone()
        recent = [float(x) for x in ((r[0] if r else "") or "").split(",") if x and now - float(x) < 3600]
        if len(recent) >= MAX_CODES_PER_HOUR:
            return False, "Too many codes in the last hour. Try again later."
        code = f"{secrets.randbelow(10 ** 6):06d}"
        unsub = secrets.token_urlsafe(24)
        c.execute("insert into emails(user_key, email, verified, code_hash, code_at, codes_sent, digest, unsub, created_at) "
                  "values (?,?,0,?,?,?,0,?,?) on conflict(user_key) do update set email = excluded.email, verified = 0, "
                  "code_hash = excluded.code_hash, code_at = excluded.code_at, codes_sent = excluded.codes_sent, digest = 0, "
                  "unsub = excluded.unsub", (user, email, _h(code, user), now, ",".join(map(str, recent + [now])), unsub, now))
        c.commit()
    send(email, "Your Cinemack code", f"Your Cinemack code is {code}. It works for one hour.\n\n"
         "If you didn't ask for it, ignore this email: nothing will be sent to this address.")
    return True, "We sent a 6-digit code" + (" (sandbox: saved on the server, not mailed)." if SANDBOX else ".")


def verify(user, code):
    code = re.sub(r"\D", "", str(code or ""))
    with _lock:
        c = conn()
        r = c.execute("select code_hash, code_at from emails where user_key = ?", (user,)).fetchone()
        if not r or not r[0] or time.time() - (r[1] or 0) > CODE_TTL:
            return False, "No valid code: ask for a new one."
        if not hmac.compare_digest(r[0], _h(code, user)):
            return False, "That code isn't right."
        c.execute("update emails set verified = 1, digest = 1, code_hash = null where user_key = ?", (user,))
        c.commit()
    return True, "Email confirmed. You'll get one digest a day when there's something new."


def set_digest(user, on):
    with _lock:
        n = conn().execute("update emails set digest = ? where user_key = ? and verified = 1", (1 if on else 0, user)).rowcount
        conn().commit()
    return bool(n)


def unsubscribe(token):
    if not token or len(token) < 20:
        return False
    with _lock:
        n = conn().execute("update emails set digest = 0 where unsub = ?", (token,)).rowcount
        conn().commit()
    return bool(n)


def delete_user(user):
    with _lock:
        conn().execute("delete from emails where user_key = ?", (user,))
        conn().execute("delete from sent where user_key = ?", (user,))
        conn().commit()


def _key(a):
    return f"{(a.get('film') or {}).get('id')}|{a.get('kind')}|{a.get('detail')}"


def digest_all(alerts_fn, limit_users=None):
    """One digest per verified, opted-in person with new alerts. alerts_fn(user) -> discovery.alerts(user)."""
    with _lock:
        people = conn().execute("select user_key, email, unsub from emails where verified = 1 and digest = 1").fetchall()
    done = {"people": len(people), "emails": 0, "alerts": 0, "skipped_nothing_new": 0}
    for user, email, unsub in people[:limit_users or None]:
        try:
            items = [a for a in alerts_fn(user).get("alerts", [])]
        except Exception as e:  # noqa: BLE001
            print(f"[email] alerts for {user[:8]}... failed: {e!r}", flush=True)
            continue
        with _lock:
            sent = {r[0] for r in conn().execute("select alert_key from sent where user_key = ?", (user,))}
        new = [a for a in items if _key(a) not in sent]
        if not new:
            done["skipped_nothing_new"] += 1
            continue
        body = ("New on your Cinemack watchlist:\n\n" + "\n".join(f"- {a['text']}" for a in new[:20])
                + (f"\n...and {len(new) - 20} more." if len(new) > 20 else "")
                + f"\n\nSee them: {SITE_URL}/#/watchlist\nStop these emails: {SITE_URL}/email/unsubscribe?t={unsub}\n")
        send(email, f"Cinemack: {len(new)} new alert{'s' if len(new) != 1 else ''}", body)
        now = time.time()
        with _lock:
            conn().executemany("insert or ignore into sent values (?,?,?)", [(user, _key(a), now) for a in new])
            conn().execute("update emails set last_sent_at = ? where user_key = ?", (now, user))
            conn().commit()
        done["emails"] += 1
        done["alerts"] += len(new)
    return done
