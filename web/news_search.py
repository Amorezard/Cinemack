"""v8.1 round 55 (20 Nov 2026): news and reviews next to search results - stored articles only, no API call.

Owner, 20 Nov: "If I search up something it should bring up the news articles." Until now Guardian articles showed
only on a film's own page ("In the Guardian", web/guardian_live.py, at most 20 live calls a day). The search box
now also lists, for the films it found, articles that are ALREADY stored, so a search never spends a Guardian call:

  1. Guardian and NYT reviews the nightly fetch matched to the film (facts.db critic_reviews, match >= 1)
  2. Guardian articles from before the film's release (facts.db film_press, pf5 B) whose headline or summary
     contains the film's title word for word AND that sit in the film / culture / TV / media sections (a one-word
     title such as "Home" would otherwise pull in house-price stories)
  3. Guardian articles stored by film-page lookups (logs/guardian_live.db articles)

Only https links to theguardian.com and nytimes.com are ever served. Shown: headline, outlet, date, which film, and
the outlet's own short summary; article text is not stored or shown. Newest first, at most PER_FILM per film and
TOTAL in all.

    GET /api/v8/news?ids=603,604      -> {"articles": [...], "credit": "..."}   (404 while SEARCH_NEWS is off)
    for_films(facts, ids, live_db=None) -> the same list, for tests and other pages
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Dict, List

try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:                     # pragma: no cover
    Request = JSONResponse = None

_ROOT = Path(__file__).resolve().parent.parent
SEARCH_NEWS = os.environ.get("SEARCH_NEWS", "0") == "1"
LIVE_DB = Path(os.environ.get("GUARDIAN_LIVE_DB", _ROOT / "logs" / "guardian_live.db"))
PER_FILM = 4
TOTAL = 6
MAX_IDS = 3
PRESS_SECTIONS = ("film", "culture", "tv-and-radio", "media")
CREDIT = "Articles from the Guardian and The New York Times; links open their sites."
_OK_URL = re.compile(r"^https://(www\.)?(theguardian\.com|nytimes\.com)/")
_TAG = re.compile(r"<[^>]+>")


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _clean(s, n=240) -> str:
    s = " ".join(_TAG.sub(" ", str(s or "")).replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _outlet(url: str) -> str:
    return "The New York Times" if "nytimes.com" in url else "The Guardian"


def _q(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _has_title(title: str, *texts) -> bool:
    t = norm(title)
    return bool(t) and any(f" {t} " in f" {norm(x)} " for x in texts if x)


def for_films(facts, ids, live_db=None) -> List[Dict]:
    """Stored articles for these films (first MAX_IDS ids), newest first. Never raises."""
    out, seen = [], set()
    clean_ids = []
    for i in ids or []:
        try:
            v = int(i)
        except (TypeError, ValueError):
            continue
        if 0 < v < 10 ** 10 and v not in clean_ids:
            clean_ids.append(v)
    clean_ids = clean_ids[:MAX_IDS]
    live = None
    p = Path(live_db) if live_db else LIVE_DB
    if p.exists():
        try:
            live = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            live = None
    try:
        for fid in clean_ids:
            row = _q(facts, "select title, year from films where tmdb_id = ?", (fid,))
            if not row:
                continue
            title, year = row[0]
            film = f"{title}{f' ({year})' if year else ''}"
            mine = []
            for key, src, head, url, pub, summ, critic in _q(
                    facts, "select review_key, source, headline, url, published, summary, critic from critic_reviews "
                           "where tmdb_id = ? and match >= 1 order by published desc limit 8", (fid,)):
                mine.append({"kind": "review", "headline": _clean(head, 160), "url": url or "", "published": (pub or "")[:10],
                             "summary": _clean(summ), "byline": critic or ""})
            for aid, pub, head, trail, sec in _q(
                    facts, "select article_id, pub_date, headline, trail, section from film_press where tmdb_id = ? "
                           "order by pub_date desc limit 40", (fid,)):
                if not aid or (sec or "film") not in PRESS_SECTIONS or not _has_title(title, head, trail):
                    continue
                mine.append({"kind": "news", "headline": _clean(head, 160), "url": "https://www.theguardian.com/" + str(aid).lstrip("/"),
                             "published": (pub or "")[:10], "summary": _clean(trail), "byline": ""})
            if live is not None:
                for kind, head, url, pub, trail, by in _q(
                        live, "select kind, headline, url, published, trail, byline from articles where tmdb_id = ? "
                              "order by published desc limit 8", (fid,)):
                    mine.append({"kind": kind or "news", "headline": _clean(head, 160), "url": url or "",
                                 "published": (pub or "")[:10], "summary": _clean(trail), "byline": by or ""})
            kept = []
            for a in sorted(mine, key=lambda a: a["published"], reverse=True):
                if not _OK_URL.match(a["url"]) or not a["headline"] or a["url"] in seen:
                    continue
                seen.add(a["url"])
                kept.append(dict(a, film=film, film_id=fid, outlet=_outlet(a["url"])))
                if len(kept) >= PER_FILM:
                    break
            out.extend(kept)
    finally:
        if live is not None:
            live.close()
    # keep each film's best first, then fill: the top film's articles first, as the search ranked them
    return out[:TOTAL]


def install(app, gate, facts_path=None):
    """GET /api/v8/news?ids=1,2,3 - 404 for everything while SEARCH_NEWS is off."""
    import asyncio

    fp = facts_path or os.environ.get("FACTS_DB", str(_ROOT / "facts.db"))

    @app.get("/api/v8/news")
    async def news(request: Request, ids: str = ""):
        if not SEARCH_NEWS:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if (g := gate(request)):
            return g
        want = [x for x in ids.split(",")[:MAX_IDS] if x.strip().isdigit()]

        def go():
            facts = sqlite3.connect(f"file:{fp}?mode=ro", uri=True, timeout=5)
            try:
                return for_films(facts, want)
            finally:
                facts.close()
        try:
            arts = await asyncio.wait_for(asyncio.to_thread(go), 4)
        except Exception:  # noqa: BLE001  - search must never break because of this
            arts = []
        return JSONResponse({"articles": arts, "credit": CREDIT}, headers={"Cache-Control": "private, max-age=600"})
    return news
