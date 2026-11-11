"""TVmaze extras for the website (11 Nov 2026, round 25). Reads logs/tvmaze.db (tools/fetch_tvmaze.py), read-only.

Only the website reads this (show pages, calendar), never the answer pipeline or the evaluations. If the file is
missing, every function returns nothing and the pages work as before. Data: TVmaze, CC BY-SA 4.0 - every place that
shows it must show CREDIT with a link (the show's TVmaze url, or https://www.tvmaze.com).
"""
import os
import sqlite3
import threading
from pathlib import Path

DB = os.environ.get("TVMAZE_DB", str(Path(__file__).resolve().parent.parent / "logs" / "tvmaze.db"))
CREDIT = {"text": "Episode details and air times: TVmaze (CC BY-SA)", "url": "https://www.tvmaze.com"}
_TL = threading.local()


def _c():
    try:
        st = os.stat(DB)
    except OSError:
        return None
    cur = getattr(_TL, "c", None)
    if cur and cur[1] == st.st_ino:
        return cur[0]
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
        c.execute("select 1 from show limit 1")
    except sqlite3.Error:
        return None
    _TL.c = (c, st.st_ino)
    return c


def _q(sql, params=()):
    c = _c()
    if c is None:
        return []
    try:
        return c.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def available():
    return bool(_q("select 1 from show limit 1"))


def show_extra(tmdb_id):
    """Where and when a show airs, from TVmaze. {} when unknown."""
    r = _q("select tvmaze_id, network, web_channel, country, timezone, air_time, air_days, status, url, summary "
           "from show where tmdb_id = ? order by updated desc limit 1", (int(tmdb_id),))
    if not r:
        return {}
    sid, net, web, country, tz, at, days, status, url, summary = r[0]
    nxt = _q("select season, number, name, airdate, airtime, airstamp from schedule where tvmaze_id = ? "
             "order by airstamp, airdate limit 1", (sid,))
    out = {"tvmaze_id": sid, "network": net, "web_channel": web, "country": country, "timezone": tz,
           "air_time": at, "air_days": days.split(",") if days else [], "status": status, "url": url,
           "summary": summary, "credit": CREDIT}
    if nxt:
        s, n, name, d, t, stamp = nxt[0]
        out["next"] = {"season": s, "episode": n, "name": name, "date": d, "time": t, "airstamp": stamp}
    return out


def episode_extras(tmdb_id, season=None):
    """{(season, number): {summary, image, airtime, airstamp, runtime, url}} for a show (optionally one season)."""
    sql = ("select e.season, e.number, e.summary, e.image, e.airtime, e.airstamp, e.runtime, e.url, e.name, e.airdate, e.type "
           "from episode e join show s on s.tvmaze_id = e.tvmaze_id where s.tmdb_id = ? and e.number is not null")
    params = [int(tmdb_id)]
    if season is not None:
        sql += " and e.season = ?"
        params.append(int(season))
    return {(s, n): {"summary": summ or "", "image": im, "airtime": at, "airstamp": st, "runtime": rt, "url": u, "name": nm,
                     "airdate": ad, "type": ty}
            for s, n, summ, im, at, st, rt, u, nm, ad, ty in _q(sql, params)}


def upcoming(a, b, tmdb_ids=None):
    """Episodes airing between dates a and b (ISO, inclusive) for our shows: list of dicts with tmdb_id."""
    sql = ("select s.tmdb_id, s.name, c.season, c.number, c.name, c.airdate, c.airtime, c.airstamp, c.web, "
           "coalesce(s.network, s.web_channel), s.url from schedule c join show s on s.tvmaze_id = c.tvmaze_id "
           "where c.airdate between ? and ?")
    params = [a, b]
    if tmdb_ids is not None:
        ids = [int(i) for i in tmdb_ids] or [0]
        sql += f" and s.tmdb_id in ({','.join('?' * len(ids))})"
        params += ids
    sql += " order by c.airdate, c.airtime"
    return [{"tmdb_id": t, "show": sn, "season": se, "episode": n, "name": en, "date": d, "time": tm, "airstamp": st,
             "streaming": bool(w), "where": wh, "url": u} for t, sn, se, n, en, d, tm, st, w, wh, u in _q(sql, params)]


# ------------------------------------------------------------------ round 27: the site reads it
_DAYS = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu", "Friday": "Fri", "Saturday": "Sat",
         "Sunday": "Sun"}


def _clock(hhmm):
    """'21:00' -> '9:00 pm'; '' for nothing."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except (ValueError, TypeError):
        return ""
    return f"{(h % 12) or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def _tz_abbr(tz):
    """'America/New_York' -> 'ET' (US zones), else the zone's own abbreviation (GMT, CET...), else the zone name."""
    us = {"America/New_York": "ET", "America/Chicago": "CT", "America/Denver": "MT", "America/Los_Angeles": "PT",
          "America/Phoenix": "MT", "America/Anchorage": "AKT", "Pacific/Honolulu": "HT"}
    if not tz:
        return ""
    if tz in us:
        return us[tz]
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo(tz)).tzname() or tz
    except Exception:  # noqa: BLE001
        return tz


def airs_text(x):
    """A show_extra() dict -> 'Sundays at 9:00 pm ET on AMC' / 'New episodes on Netflix' / '' (nothing known)."""
    if not x:
        return ""
    where = x.get("network") or x.get("web_channel") or ""
    days = [d for d in (x.get("air_days") or []) if d]
    when = ""
    if days and len(days) < 7:
        when = (days[0] + "s") if len(days) == 1 else ", ".join(_DAYS.get(d, d) for d in days)
    elif len(days) == 7:
        when = "Daily"
    t = _clock(x.get("air_time"))
    if t and x.get("network"):                         # a time only means something for a broadcast channel
        when = f"{when} at {t} {_tz_abbr(x.get('timezone'))}".strip()
    if x.get("status") == "Ended":
        return f"Aired on {where}" if where else ""
    if not when and x.get("web_channel") and not x.get("network"):
        return f"Streams on {where}"
    return " ".join(p for p in (when, f"on {where}" if where else "") if p).strip()


def strip_html(s):
    import html as _h
    import re as _re
    return " ".join(_h.unescape(_re.sub(r"<[^>]+>", " ", s or "")).split())
