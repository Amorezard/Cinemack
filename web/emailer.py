"""Email alerts (post-v8 workstream F, "email only in a sandbox"; built 11 Nov 2026, round 27).

A signed-in person can add an email address in Profile > Settings > Notifications. Nothing is sent to an address
until they type the 6-digit code mailed to it (so nobody can sign someone else up), and every email has a one-click
unsubscribe link. Then once a day (nightly.sh -> tools/send_digests.py) they get ONE digest of their new in-app alerts
(a saved film is out or starts streaming, a followed show has a dated or new season). An alert is emailed at most
once (table `sent`); an opted-out or unverified address gets nothing; muted films never make alerts at all.

Sandbox by default: EMAIL_SANDBOX=1 (the default) writes each email as a .eml file to logs/email_outbox/ instead of
sending it. Real sending (round 31): EMAIL_SANDBOX=0 + EMAIL_PROVIDER (brevo / resend / sendgrid / mailgun / smtp) +
that provider's key + EMAIL_FROM in keys.env, and SITE_URL for the unsubscribe link (see "email APIs" below). Push notifications are not offered: the browser Push API only works
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


# Round 55 (20 Nov): the website process gets its switches from start_site.sh, which does not read keys.env, so the
# site stayed in the sandbox even when keys.env said EMAIL_SANDBOX=0 (the nightly digests read keys.env; the 6-digit
# codes come from the site). Email settings missing from the environment are now read from keys.env, else
# ~/.movie-buff.env - the same rule as the Guardian and Trakt keys. Values are never printed.
_EMAIL_NAMES = ("EMAIL_SANDBOX", "EMAIL_PROVIDER", "EMAIL_FROM", "SMTP_FROM", "SITE_URL", "BREVO_API_KEY", "RESEND_API_KEY",
                "SENDGRID_API_KEY", "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_BASE", "SMTP_HOST", "SMTP_PORT",
                "SMTP_USER", "SMTP_PASS")


def _settings_from_files(paths=None, names=_EMAIL_NAMES, env=None):
    env = os.environ if env is None else env
    found = {}
    for p in paths or (RAG / "keys.env", Path.home() / ".movie-buff.env"):
        try:
            lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if not m or m.group(1) not in names or m.group(1) in env or m.group(1) in found:
                continue
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].strip()
            found[m.group(1)] = v
    for k, v in found.items():
        env.setdefault(k, v)
    return sorted(found)


import sys as _sys
# never under a test run: a test that relies on the sandbox default must not pick up EMAIL_SANDBOX=0 from keys.env
if os.environ.get("EMAIL_FILES", "1") == "1" and "unittest" not in _sys.modules and "pytest" not in _sys.modules:
    _settings_from_files()
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
        return {"email": None, "verified": False, "digest": False, "sandbox": not configured()[0]}
    e = r[0]
    masked = e[0] + "***" + e[e.index("@"):] if "@" in e else e
    return {"email": masked, "verified": bool(r[1]), "digest": bool(r[2]), "last_sent": r[3], "sandbox": not configured()[0]}


# ------------------------------------------------------------------ round 31: email APIs (11 Nov)
# EMAIL_PROVIDER picks how mail leaves the server (keys live in keys.env, chmod 600, never printed):
#   brevo     POST https://api.brevo.com/v3/smtp/email        BREVO_API_KEY       (free plan: 300 emails a day;
#             the sender address EMAIL_FROM must be verified in Brevo first - no own domain needed to start)
#   resend    POST https://api.resend.com/emails              RESEND_API_KEY      (needs a verified domain to mail others)
#   sendgrid  POST https://api.sendgrid.com/v3/mail/send      SENDGRID_API_KEY    (single-sender verification)
#   mailgun   POST https://api.mailgun.net/v3/<domain>/messages  MAILGUN_API_KEY + MAILGUN_DOMAIN (+ MAILGUN_BASE for EU)
#   smtp      SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS   (e.g. a mail account's app password)
# Nothing is sent while EMAIL_SANDBOX=1 (the default) or when the chosen provider has no key: the email is written
# to logs/email_outbox/ instead. Every real send is logged in table send_log (time, provider, status, provider id;
# no addresses or content).
PROVIDER = os.environ.get("EMAIL_PROVIDER", "smtp").strip().lower()
FROM = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_FROM") or "Cinemack <no-reply@cinemack.invalid>"
_KEYS = {"brevo": ("BREVO_API_KEY",), "resend": ("RESEND_API_KEY",), "sendgrid": ("SENDGRID_API_KEY",),
         "mailgun": ("MAILGUN_API_KEY", "MAILGUN_DOMAIN"), "smtp": ("SMTP_HOST",)}


def _from_parts():
    import email.utils
    name, addr = email.utils.parseaddr(FROM)
    return name or "Cinemack", addr


def configured():
    """(live?, why): live only when the sandbox is off and the provider's settings are present."""
    if SANDBOX:
        return False, "sandbox (EMAIL_SANDBOX=1)"
    need = _KEYS.get(PROVIDER)
    if not need:
        return False, f"unknown EMAIL_PROVIDER {PROVIDER!r}"
    miss = [k for k in need if not os.environ.get(k)]
    if miss:
        return False, f"{PROVIDER}: missing {', '.join(miss)}"
    if PROVIDER != "smtp" and not _from_parts()[1].count("@"):
        return False, "EMAIL_FROM has no address"
    if not os.environ.get("SITE_URL"):
        return False, "SITE_URL not set (the unsubscribe link in every email must point at the real site)"
    return True, PROVIDER


def _http(url, payload, headers, form=False):
    import base64 as _b64
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode(payload).encode() if form else _json.dumps(payload).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", "Accept": "application/json",
         "User-Agent": "Cinemack/8 (student research project)"}
    h.update(headers)
    if "basic" in h:
        h["Authorization"] = "Basic " + _b64.b64encode(h.pop("basic").encode()).decode()
    last = None
    for attempt in range(3):                       # 429 / 5xx: wait and try again, at most 3 times
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method="POST"), timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
                try:
                    j = _json.loads(body or "{}")
                except ValueError:
                    j = {}
                return r.status, str(j.get("messageId") or j.get("id") or r.headers.get("X-Message-Id") or "")[:120]
        except urllib.error.HTTPError as e:
            last = (e.code, e.read().decode("utf-8", "replace")[:300])
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"{PROVIDER} HTTP {e.code}: {last[1]}") from None
    raise RuntimeError(f"{PROVIDER} failed: {last}")


def _send_api(to, subject, body):
    name, addr = _from_parts()
    if PROVIDER == "brevo":
        return _http("https://api.brevo.com/v3/smtp/email",
                     {"sender": {"name": name, "email": addr}, "to": [{"email": to}], "subject": subject, "textContent": body},
                     {"api-key": os.environ["BREVO_API_KEY"]})
    if PROVIDER == "resend":
        return _http("https://api.resend.com/emails", {"from": f"{name} <{addr}>", "to": [to], "subject": subject, "text": body},
                     {"Authorization": "Bearer " + os.environ["RESEND_API_KEY"]})
    if PROVIDER == "sendgrid":
        return _http("https://api.sendgrid.com/v3/mail/send",
                     {"personalizations": [{"to": [{"email": to}]}], "from": {"email": addr, "name": name},
                      "subject": subject, "content": [{"type": "text/plain", "value": body}]},
                     {"Authorization": "Bearer " + os.environ["SENDGRID_API_KEY"]})
    if PROVIDER == "mailgun":
        base = os.environ.get("MAILGUN_BASE", "https://api.mailgun.net").rstrip("/")
        return _http(f"{base}/v3/{os.environ['MAILGUN_DOMAIN']}/messages",
                     {"from": f"{name} <{addr}>", "to": to, "subject": subject, "text": body},
                     {"basic": "api:" + os.environ["MAILGUN_API_KEY"]}, form=True)
    raise RuntimeError(f"unknown EMAIL_PROVIDER {PROVIDER!r}")


def _log_send(status, ref, err=""):
    try:
        with _lock:
            c = conn()
            c.execute("create table if not exists send_log(at real, provider text, status text, ref text, error text)")
            c.execute("insert into send_log values (?,?,?,?,?)", (time.time(), PROVIDER, str(status), ref or "", err[:300]))
            c.commit()
    except sqlite3.Error:
        pass


def send(to, subject, body):
    """Send through EMAIL_PROVIDER, or in the sandbox write logs/email_outbox/<time>.eml. Returns where it went;
    raises RuntimeError when a live provider refuses (the caller keeps the alert unsent for the next run)."""
    live, why = configured()
    unsub = re.search(r"https?://\S+/email/unsubscribe\?t=\S+", body or "")
    if not live:
        msg = EmailMessage()
        msg["From"] = FROM
        msg["To"] = to
        msg["Subject"] = subject
        if unsub:
            msg["List-Unsubscribe"] = f"<{unsub.group(0)}>"
        msg.set_content(body)
        OUTBOX.mkdir(parents=True, exist_ok=True)
        p = OUTBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.eml"
        p.write_bytes(bytes(msg))
        return str(p)
    try:
        if PROVIDER == "smtp":
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = FROM, to, subject
            if unsub:
                msg["List-Unsubscribe"] = f"<{unsub.group(0)}>"
            msg.set_content(body)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.send_message(msg)
            _log_send("ok", "")
            return "smtp"
        status, ref = _send_api(to, subject, body)
        _log_send(status, ref)
        return f"{PROVIDER}:{ref or status}"
    except Exception as e:  # noqa: BLE001
        _log_send("error", "", repr(e))
        raise RuntimeError(str(e)) from None


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
    try:
        send(email, "Your Cinemack code", f"Your Cinemack code is {code}. It works for one hour.\n\n"
             "If you didn't ask for it, ignore this email: nothing will be sent to this address.")
    except RuntimeError as e:
        print(f"[email] code not sent: {e}", flush=True)
        return False, "The email service didn't accept the message. Try again later."
    return True, "We sent a 6-digit code" + ("." if configured()[0] else " (test mode: saved on the server, not mailed).")


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
        body = ("New for you on Cinemack (your watchlist, shows and people you follow):\n\n"
                + "\n".join(f"- {a['text']}" + (f" ({a['url']})" if a.get("url") else "") for a in new[:20])
                + (f"\n...and {len(new) - 20} more." if len(new) > 20 else "")
                + f"\n\nSee them: {SITE_URL}/#/watchlist\nPeople alerts on or off: {SITE_URL}/#/profile?tab=settings&sec=notifications\nStop these emails: {SITE_URL}/email/unsubscribe?t={unsub}\n")
        try:
            send(email, f"Cinemack: {len(new)} new alert{'s' if len(new) != 1 else ''}", body)
        except RuntimeError as e:                     # round 31: nothing marked sent; tried again next night
            print(f"[email] digest for {user[:8]}... not sent: {e}", flush=True)
            done["failed"] = done.get("failed", 0) + 1
            continue
        now = time.time()
        with _lock:
            conn().executemany("insert or ignore into sent values (?,?,?)", [(user, _key(a), now) for a in new])
            conn().execute("update emails set last_sent_at = ? where user_key = ?", (now, user))
            conn().commit()
        done["emails"] += 1
        done["alerts"] += len(new)
    return done
