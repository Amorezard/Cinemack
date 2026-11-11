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
    sql = ("select e.season, e.number, e.summary, e.image, e.airtime, e.airstamp, e.runtime, e.url, e.name "
           "from episode e join show s on s.tvmaze_id = e.tvmaze_id where s.tmdb_id = ? and e.number is not null")
    params = [int(tmdb_id)]
    if season is not None:
        sql += " and e.season = ?"
        params.append(int(season))
    return {(s, n): {"summary": summ or "", "image": im, "airtime": at, "airstamp": st, "runtime": rt, "url": u, "name": nm}
            for s, n, summ, im, at, st, rt, u, nm in _q(sql, params)}


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
