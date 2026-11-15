"""v8.1 slice 5.1: recommendation events (what was shown, what was chosen), kit contract recommendation_event v1.

Switch REC_EVENTS (off by default): while it is off record() stores nothing and returns None. Nothing imports this yet.
Storage is a NEW database (REC_EVENTS_DB, default logs/rec_events.db); no existing database is touched.
Privacy rules built in:
  - the person is never taken from the client: record() takes the user id the SERVER got from the session;
  - the stored subject_key is a salted hash of that id, and only when the person agreed to personalisation
    (consent 'consented_personalization'); otherwise it is null and only the session key links events;
  - no chat text: context_key is a hash of whatever the caller passes (filters, page), never the text itself;
  - forget(user_id) deletes everything stored for a person; purge(days) deletes old events.
An impression is not a like, a missing click is not a dislike, watch_started is not a like: strength() says how
each event type may be used by a ranker (None = exposure only)."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENABLED = os.environ.get("REC_EVENTS", "0") == "1"
DB = Path(os.environ.get("REC_EVENTS_DB", Path(__file__).resolve().parent.parent / "logs" / "rec_events.db"))
SCHEMA_VERSION = 1
PLACEMENTS = ("home", "chat", "pick_tonight", "watchlist", "detail", "calendar")
EVENT_TYPES = ("impression", "click", "watchlist_add", "watchlist_remove", "watch_started", "watch_completed",
               "rating_positive", "rating_negative", "not_interested", "pick_tonight_selected", "already_watched",
               "more_like_this")
CONSENT = ("anonymous", "consented_personalization", "service_required")
REQUIRED = ("schema_version", "event_id", "occurred_at", "subject_key", "session_key", "media_id", "placement",
            "event_type", "ranking_version", "exposure_id", "position", "context_key")
OPTIONAL = ("consent_scope", "explicit_rating")
# how a ranker may read each event: sign and weight, or None when it is exposure / bookkeeping only
STRENGTH = {"impression": None, "click": ("weak", +0.2), "watchlist_add": ("interest", +0.6),
            "watchlist_remove": ("interest", -0.3), "watch_started": ("weak", +0.2), "watch_completed": ("weak", +0.4),
            "rating_positive": ("strong", +1.0), "rating_negative": ("strong", -1.0), "not_interested": ("strong", -0.8),
            "pick_tonight_selected": ("interest", +0.6), "already_watched": None, "more_like_this": ("interest", +0.7)}


class EventError(ValueError):
    pass


def _dt(s):
    if not isinstance(s, str):
        raise EventError("date-time must be text")
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        raise EventError("not a date-time: %r" % s)
    if d.tzinfo is None:
        raise EventError("date-time needs a time zone: %r" % s)
    return d


def validate(ev):
    """Raises EventError unless ev matches the kit's recommendation_event v1 exactly (unknown fields rejected)."""
    if not isinstance(ev, dict):
        raise EventError("event must be an object")
    extra = set(ev) - set(REQUIRED) - set(OPTIONAL)
    if extra:
        raise EventError("unknown fields: %s" % ", ".join(sorted(extra)))
    missing = [k for k in REQUIRED if k not in ev]
    if missing:
        raise EventError("missing fields: %s" % ", ".join(missing))
    if ev["schema_version"] != 1 or isinstance(ev["schema_version"], bool):
        raise EventError("schema_version must be 1")
    for k in ("event_id", "session_key", "media_id", "ranking_version"):
        if not isinstance(ev[k], str) or not ev[k]:
            raise EventError("%s must be non-empty text" % k)
    for k in ("subject_key", "exposure_id", "context_key"):
        if ev[k] is not None and not isinstance(ev[k], str):
            raise EventError("%s must be text or null" % k)
    _dt(ev["occurred_at"])
    if ev["placement"] not in PLACEMENTS:
        raise EventError("unknown placement")
    if ev["event_type"] not in EVENT_TYPES:
        raise EventError("unknown event_type")
    p = ev["position"]
    if p is not None and (isinstance(p, bool) or not isinstance(p, int) or p < 0):
        raise EventError("position must be a whole number >= 0 or null")
    if "consent_scope" in ev and ev["consent_scope"] not in CONSENT:
        raise EventError("unknown consent_scope")
    r = ev.get("explicit_rating")
    if r is not None and (isinstance(r, bool) or not isinstance(r, (int, float)) or not 0 <= r <= 5):
        raise EventError("explicit_rating must be 0-5 or null")
    return ev


def strength(event_type):
    if event_type not in STRENGTH:
        raise EventError("unknown event_type")
    return STRENGTH[event_type]


def _conn(db=None):
    path = Path(db or DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=30)
    c.execute("pragma busy_timeout=30000")
    c.executescript("""create table if not exists rec_event(event_id text primary key, occurred_at text not null,
        subject_key text, session_key text not null, media_id text not null, placement text not null,
        event_type text not null, ranking_version text not null, exposure_id text, position integer, context_key text,
        consent_scope text, explicit_rating real);
      create index if not exists rec_event_subject on rec_event(subject_key, occurred_at);
      create index if not exists rec_event_exposure on rec_event(exposure_id);
      create table if not exists rec_meta(key text primary key, value text);""")
    return c


def _salt(c):
    r = c.execute("select value from rec_meta where key='salt'").fetchone()
    if r:
        return r[0]
    s = secrets.token_hex(16)
    c.execute("insert or ignore into rec_meta values ('salt', ?)", (s,))
    c.commit()
    return c.execute("select value from rec_meta where key='salt'").fetchone()[0]


def _key(salt, kind, value):
    return hashlib.sha256(("%s|%s|%s" % (salt, kind, value)).encode()).hexdigest()[:32]


def context_key(context):
    """Hash of the limits / page a list was built for. Text typed by the person must not be passed here; if it is,
    only its hash is kept."""
    if context is None:
        return None
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()[:24]


def new_exposure():
    return secrets.token_hex(12)


def record(server_user_id, session_id, media_id, placement, event_type, ranking_version, *, consent="anonymous",
           exposure_id=None, position=None, context=None, explicit_rating=None, now=None, db=None, enabled=None):
    """Stores one event and returns it, or returns None while REC_EVENTS is off. server_user_id must come from the
    server's own session lookup (None for a visitor); there is deliberately no way to pass a ready-made subject key."""
    if not (ENABLED if enabled is None else enabled):
        return None
    if consent not in CONSENT:
        raise EventError("unknown consent_scope")
    if not session_id:
        raise EventError("session needed")
    c = _conn(db)
    try:
        salt = _salt(c)
        subject = _key(salt, "u", server_user_id) if (server_user_id is not None and consent == "consented_personalization") else None
        ev = {"schema_version": SCHEMA_VERSION, "event_id": secrets.token_hex(16),
              "occurred_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "subject_key": subject, "session_key": _key(salt, "s", session_id), "media_id": media_id,
              "placement": placement, "event_type": event_type, "ranking_version": ranking_version,
              "exposure_id": exposure_id, "position": position, "context_key": context_key(context),
              "consent_scope": consent, "explicit_rating": explicit_rating}
        validate(ev)
        c.execute("insert into rec_event values (?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(ev[k] for k in (
            "event_id", "occurred_at", "subject_key", "session_key", "media_id", "placement", "event_type",
            "ranking_version", "exposure_id", "position", "context_key", "consent_scope", "explicit_rating")))
        c.commit()
        return ev
    finally:
        c.close()


def record_exposure(server_user_id, session_id, media_ids, placement, ranking_version, **kw):
    """One impression per shown title, sharing one exposure id, positions 0.. in the order shown."""
    if not (ENABLED if kw.get("enabled") is None else kw["enabled"]):
        return None
    ex = new_exposure()
    for i, m in enumerate(media_ids):
        record(server_user_id, session_id, m, placement, "impression", ranking_version, exposure_id=ex, position=i, **kw)
    return ex


def for_user(server_user_id, db=None, limit=1000):
    """Events of one person (only those stored with consent). The caller passes the server-side user id."""
    c = _conn(db)
    try:
        k = _key(_salt(c), "u", server_user_id)
        cols = [x[1] for x in c.execute("pragma table_info(rec_event)")]
        return [dict(zip(cols, r)) for r in c.execute(
            "select * from rec_event where subject_key=? order by occurred_at, rowid limit ?", (k, int(limit)))]
    finally:
        c.close()


def forget(server_user_id, db=None):
    c = _conn(db)
    try:
        n = c.execute("delete from rec_event where subject_key=?", (_key(_salt(c), "u", server_user_id),)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def purge(days=365, now=None, db=None):
    cut = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = _conn(db)
    try:
        n = c.execute("delete from rec_event where occurred_at < ?", (cut,)).rowcount
        c.commit()
        return n
    finally:
        c.close()
