"""
v6 (20 Oct night): links under an answer, chosen by what the question asks.

    for_answer(question, films, intent, answer)  -> [{"kind", "label", "items": [link, ...]}, ...]
    for_film(fid, title, year)                   -> {"trailer": {...}|None, "watch": {...}, "reviews": [...],
                                                     "success": [...]}   (the film panel)

    question asks about ...                         groups shown (at most 3, in this order)
    where to watch / stream / rent / Netflix ...    Where to watch
    critics, reviews, reception, "is it good"       Reviews
    box office, budget, hit, flop, why it worked    Box office and success
    a trailer, or a recommendation / plot search    Trailers
    (anything else about a film)                    Trailers

A link is {"film", "film_id", "text", "url", "kind"} plus "embed" (a YouTube key) for trailers and
"note" for extra detail (stars, critic, providers). Real pages come first (the film's own trailer
from TMDB, the Guardian / NYT review it was matched to, Box Office Mojo by IMDb id); a search link
stands in when the page itself is unknown, and its text says "search". Nothing here calls the
network: every link is built from facts.db (fetch_free_data.py) and wiki.db.

Which films: the one a plot search found; the films a question or answer names; otherwise the
first cards (up to 4 for recommendations and rankings).
"""
import os
import re
import sqlite3

import dbsafe   # 10 Nov: shared read connection, one call at a time
import urllib.parse
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG_DIR / "facts.db"))
WIKI = os.environ.get("WIKI_DB", str(RAG_DIR / "wiki.db"))
_C = {}

_W = lambda p: re.compile(p, re.I)   # noqa: E731
R_WATCH = _W(r"\b(where (can|could|do|should|to) (i |we |you )?(watch|stream|see|rent|buy|find)|stream(ing|ed)?|"
             r"netflix|hulu|prime video|amazon prime|disney\+|disney plus|hbo( max)?|peacock|paramount\+|apple tv|"
             r"tubi|rent(al)?|to buy|on demand|available (on|to)|watch (it )?(online|at home|tonight))\b")
R_REVIEWS = _W(r"\b(critics?|reviews?|reviewers?|reception|received|rotten tomatoes|rotten|metacritic|"
               r"ratings?|(highly|best|top|well|poorly|low|lowest|highest)[- ]rated|acclaim\w*|panned|praised|worth (watching|seeing|it)|any good|is it good|"
               r"(liked|loved|hated|disliked) (it|the)|what did (people|critics|audiences) think|overrated|underrated)\b")
R_SUCCESS = _W(r"\b(box[- ]office|gross\w*|budgets?|profit\w*|money|revenue|earn\w*|made (more|less|the most|so much|money)|"
               r"lost money|flop\w*|bombed|box[- ]office bomb|(a|big|huge|total) bomb|(a|big|huge|box[- ]office|surprise|sleeper) hit|blockbuster|succe\w*|fail\w*|perform\w*|underperform\w*|"
               r"disappoint\w*|opening weekend|roi|why did .{0,60} (work|do so|do well|do badly))\b")
# plot searches ("a man who fails...", "a heist for money") use money words without asking about money
R_MONEY_STRICT = _W(r"\b(box[- ]office|gross\w*|budgets?|profit\w*|revenue|opening weekend|how much did .{0,40} (make|earn|cost))\b")
R_TRAILER = _W(r"\b(trailers?|teasers?|preview|clips?|footage|watch the trailer|what does it look like)\b")

LABEL = {"watch": "Where to watch", "reviews": "Reviews", "success": "Box office and success", "trailer": "Trailers"}


def _ro(path):
    if path not in _C:
        _C[path] = (dbsafe.wrap(sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False))
                    if os.path.exists(path) else None)
    return _C[path]


def _q(conn, sql, p=()):
    if conn is None:
        return []
    try:
        return conn.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def _enc(s):
    return urllib.parse.quote_plus(s or "")


def _name(title, year):
    return f"{title} ({year})" if year else (title or "this film")


def _imdb_id(fid):
    r = _q(_ro(FACTS), "select imdb_id from films where tmdb_id = ?", (fid,))
    return r[0][0] if r and r[0][0] and str(r[0][0]).startswith("tt") else None


def _wiki(fid, kind):
    """Wikipedia page URL, with the anchor of the film's reception / box-office section when known."""
    w = _q(_ro(WIKI), "select url from pages where film_id = ? limit 1", (fid,))
    if not w or not w[0][0]:
        return None
    url = w[0][0].split("#")[0]
    kinds = {"reviews": ("reception",), "success": ("box_office", "release")}.get(kind, ())
    for k in kinds:
        s = _q(_ro(FACTS), "select section_key from sections where tmdb_id = ? and kind = ? order by position limit 1",
               (fid, k))
        if s and s[0][0]:
            head = s[0][0].strip()
            return url + "#" + urllib.parse.quote(head[:1].upper() + head[1:]).replace("%20", "_")
    return url


def _trailer(fid):
    rows = _q(_ro(FACTS), "select video_key, type, official, published_at, name from film_videos where tmdb_id = ? "
                          "and site = 'YouTube' and type in ('Trailer','Teaser')", (fid,))
    if not rows:
        return None
    key, typ, off, pub, name = min(rows, key=lambda r: (r[1] != "Trailer", not r[2], r[3] or "9999"))
    return {"key": key, "type": typ, "name": name, "published_at": (pub or "")[:10],
            "url": f"https://www.youtube.com/watch?v={key}"}


def _providers(fid, region="US"):
    rows = _q(_ro(FACTS), "select kind, provider_name from watch_providers where tmdb_id = ? and region = ? "
                          "order by priority", (fid, region))
    out = {}
    for kind, name in rows:
        if name not in out.setdefault(kind, []):
            out[kind].append(name)
    return out


def _critic_reviews(fid):
    return _q(_ro(FACTS), "select source, critic, stars, published, url from critic_reviews where tmdb_id = ? "
                          "and match >= 1 and url is not null order by source = 'guardian' desc, published desc limit 4",
              (fid,))


# ------------------------------------------------------------------ one film, one kind
def film_links(kind, fid, title, year):
    name = _name(title, year)
    q = f"{title} {year or ''}".strip()
    L = lambda text, url, **kw: dict(film=name, film_id=fid, text=text, url=url, kind=kind, **kw)  # noqa: E731
    out = []
    tt = _imdb_id(fid)
    if kind == "trailer":
        t = _trailer(fid)
        if t:
            out.append(L(f"{t['type']}: {t['name'] or name}", t["url"], embed=t["key"],
                         note=f"YouTube, {t['published_at']}" if t["published_at"] else "YouTube"))
        else:
            out.append(L("Search YouTube for the trailer",
                         f"https://www.youtube.com/results?search_query={_enc(q + ' official trailer')}"))
    elif kind == "reviews":
        for src, critic, stars, pub, url in _critic_reviews(fid):
            outlet = "The Guardian" if src == "guardian" else "The New York Times"
            note = ", ".join(x for x in (critic, (f"{stars:g}/5 stars" if stars is not None else None), (pub or "")[:4]) if x)
            out.append(L(f"{outlet} review", url, note=note))
        out.append(L("Rotten Tomatoes (search)", f"https://www.rottentomatoes.com/search?search={_enc(title)}"))
        out.append(L("Metacritic (search)", f"https://www.metacritic.com/search/{urllib.parse.quote(title or '')}/?category=2"))
        if tt:
            out.append(L("Critic reviews listed on IMDb", f"https://www.imdb.com/title/{tt}/externalreviews/"))
            out.append(L("IMDb user reviews", f"https://www.imdb.com/title/{tt}/reviews/"))
        out.append(L("Letterboxd", f"https://letterboxd.com/tmdb/{fid}/"))
        out.append(L("Video reviews (YouTube search)",
                     f"https://www.youtube.com/results?search_query={_enc(q + ' movie review')}"))
        w = _wiki(fid, "reviews")
        if w:
            out.append(L("Wikipedia: reception", w))
    elif kind == "success":
        if tt:
            out.append(L("Box Office Mojo", f"https://www.boxofficemojo.com/title/{tt}/"))
        w = _wiki(fid, "success")
        if w:
            out.append(L("Wikipedia: box office", w))
        out.append(L("The Numbers: budget and grosses (search)",
                     f"https://duckduckgo.com/?q={_enc('site:the-numbers.com ' + title + ' ' + str(year or '') + ' box office')}"))
        out.append(L("Trade press coverage (Deadline, Variety search)",
                     f"https://duckduckgo.com/?q={_enc(title + ' ' + str(year or '') + ' box office (site:deadline.com OR site:variety.com)')}"))
    elif kind == "watch":
        pv = _providers(fid)
        parts = [f"{lbl}: {', '.join(pv[k][:4])}" for k, lbl in (("flatrate", "stream"), ("free", "free"),
                 ("ads", "free with ads"), ("rent", "rent"), ("buy", "buy")) if pv.get(k)]
        out.append(L("Streaming, rental and purchase options", f"https://www.themoviedb.org/movie/{fid}/watch?locale=US",
                     note=("; ".join(parts) + " · data from JustWatch") if parts else "data from JustWatch"))
        out.append(L("JustWatch (search)", f"https://www.justwatch.com/us/search?q={_enc(title)}"))
    return out


# ------------------------------------------------------------------ an answer
def kinds_for(question, intent):
    q = question or ""
    kinds = []
    if R_WATCH.search(q):
        kinds.append("watch")
    if R_REVIEWS.search(q):
        kinds.append("reviews")
    if R_SUCCESS.search(q) and (intent != "identify" or R_MONEY_STRICT.search(q)):
        kinds.append("success")
    if R_TRAILER.search(q) or intent in ("recommend", "identify") or not kinds:
        kinds.append("trailer")
    if R_TRAILER.search(q):                      # asked for the trailer: it goes first
        kinds.remove("trailer")
        kinds.insert(0, "trailer")
    return kinds[:3]


def pick_films(question, films, intent, answer=""):
    films = [f for f in films or [] if f.get("id") is not None]
    if not films:
        return []
    if intent == "identify":
        return films[:1]
    text = f"{question or ''} {answer or ''}".casefold()
    named = [f for f in films if (f.get("title") or "").casefold() and (f.get("title") or "").casefold() in text]
    if intent in ("recommend",) or len(named) > 1:
        return (named or films)[:4]
    return (named or films)[:1] if intent in ("named_film", "other", None, "") else (named or films)[:4]


def for_answer(question, films, intent=None, answer=""):
    """Never raises: a failure gives no links, never a lost answer."""
    try:
        chosen = pick_films(question, films, intent, answer)
        if not chosen:
            return []
        groups = []
        per_film_cap = 1 if len(chosen) > 2 else (3 if len(chosen) == 2 else 6)
        for kind in kinds_for(question, intent):
            items = []
            for f in chosen:
                items += film_links(kind, f["id"], f.get("title"), f.get("year"))[:per_film_cap if kind != "trailer" else 1]
            if items:
                groups.append({"kind": kind, "label": LABEL[kind], "items": items})
        return groups
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] links failed: {e!r}", flush=True)
        return []


def for_film(fid, title, year):
    try:
        return {"trailer": _trailer(fid), "watch": film_links("watch", fid, title, year)[0],
                "providers": _providers(fid),
                "reviews": film_links("reviews", fid, title, year), "success": film_links("success", fid, title, year)}
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] film links failed: {e!r}", flush=True)
        return {}
