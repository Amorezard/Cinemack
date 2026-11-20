"""
v6 (20 Oct): what the new film cards and the film panel show, and the watchlist.

    enrich(cards, user)   adds poster, runtime, critic scores, IMDb rating and this person's
                          own choice ("liked", "not", "unseen", "want") to the page's film cards
    detail(fid, user)     the film panel: backdrop, overview, top cast with photos, budget and gross,
                          critic scores, Wikipedia and TMDB links
    choose(user, fid, title, choice, message_id)
                          one of the four taste buttons on a card:
                            liked / not  -> personal.py film_prefs (taste profile, used for recommendations)
                            unseen / want -> watch table in logs/users.db (not used for recommendations yet)
                            ""           -> clears both
                          A film has at most one of the four at a time.

Reads facts.db read-only (tables from tools.py, money_extra.py and fetch_v6_data.py). Every table
is optional: a missing one just leaves that field out, so the page works before
`python fetch_v6_data.py images` has run and in MOCK mode.
"""
import json
import os
import re
import sqlite3

import dbsafe   # 10 Nov: shared connection, one call at a time (see dbsafe.py)
import threading
import time
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
FACTS = os.environ.get("FACTS_DB", str(RAG_DIR / "facts.db"))
WIKI = os.environ.get("WIKI_DB", str(RAG_DIR / "wiki.db"))
USERS = os.environ.get("USERS_DB", str(RAG_DIR / "logs" / "users.db"))
IMG = "https://image.tmdb.org/t/p/"
CHOICES = ("liked", "not", "unseen", "want")

_lock = threading.Lock()
_C = {}


_TL = threading.local()


def _ro(path):
    """A read-only connection for THIS thread. 10 Nov: one connection shared by every executor thread raised
    "SystemError: ... returned NULL" when pages fetched several films at once (Pick Tonight, Watchlist). sqlite3
    connections must not be used by two threads at the same moment; read-only ones are cheap, so each thread gets its
    own (the executor's pool is bounded, so the number of connections is too)."""
    if not os.path.exists(path):
        return None
    d = getattr(_TL, "c", None)
    if d is None:
        d = _TL.c = {}
    if path not in d:
        d[path] = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    return d[path]


def _users():
    if "users" not in _C:
        os.makedirs(os.path.dirname(USERS), exist_ok=True)
        c = dbsafe.wrap(sqlite3.connect(USERS, check_same_thread=False, timeout=30))
        c.execute("pragma journal_mode=WAL")      # v7 D4: readers never block the writer
        c.executescript("""
            create table if not exists film_prefs(user_id text, tmdb_id integer, title text, signal integer,
              source text, message_id text, created_at text, primary key (user_id, tmdb_id));
            create table if not exists watch(user_id text, tmdb_id integer, title text, status text,
              message_id text, created_at text, primary key (user_id, tmdb_id));""")
        _C["users"] = c
    return _C["users"]


def _q(conn, sql, params=()):
    """Rows, or [] when the table is not built yet."""
    if conn is None:
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _many(conn, sql_head, ids):
    ids = [int(i) for i in ids if isinstance(i, int) or (isinstance(i, str) and i.isdigit())]
    if not ids:
        return []
    return _q(conn, sql_head + " (" + ",".join("?" * len(ids)) + ")", ids)


def img(path, size):
    return f"{IMG}{size}{path}" if path else None


def _critics(row):
    rt, rt_n, mc, cs = row
    out = {}
    if rt is not None:
        out["rt"] = round(rt)
        if rt_n:
            out["rt_n"] = rt_n
    if mc is not None:
        out["mc"] = round(mc)
    if cs:
        out["cs"] = cs
    return out


def _certs(f, ids):
    """{tmdb_id: 'PG-13'} US theatrical rating (fetch_free_data.py tmdb), or {} before that has run."""
    out = {}
    for fid, c, _t in _many(f, "select tmdb_id, certification, release_type from certifications where region = 'US' "
                               "and certification != '' and tmdb_id in", ids):
        if fid not in out:
            out[fid] = c
    return out


def _omdb_critics(f, ids):
    """Rotten Tomatoes / Metacritic from OMDb for films whose Wikipedia text gave none (21 Oct)."""
    out = {}
    for fid, rt, mc in _many(f, "select tmdb_id, rt_pct, mc_score from omdb where status = 'ok' and tmdb_id in", ids):
        d = {}
        if rt is not None:
            d["rt"] = round(rt)
        if mc is not None:
            d["mc"] = round(mc)
        if d:
            d["src"] = "omdb"
            out[fid] = d
    return out


def user_choices(user, ids):
    """{tmdb_id: 'liked'|'not'|'unseen'|'want'} for this browser id."""
    user = _safe(user)
    ids = [int(i) for i in ids if isinstance(i, int) or (isinstance(i, str) and i.isdigit())]
    if not user or not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out = {}
    with _lock:
        c = _users()
        for fid, st in _q(c, f"select tmdb_id, status from watch where user_id = ? and tmdb_id in ({marks})",
                          [user, *ids]):
            out[fid] = st
        for fid, sig in _q(c, f"select tmdb_id, signal from film_prefs where user_id = ? and tmdb_id in ({marks})",
                           [user, *ids]):
            if sig:
                out[fid] = "liked" if sig > 0 else "not"
    return out


def _safe(user):
    return "".join(ch for ch in str(user or "") if ch.isalnum() or ch in "-_")[:64]


def enrich(cards, user=""):
    """Adds v6 fields in place and returns the list. Never raises."""
    try:
        ids = [c.get("id") for c in cards if c.get("id") is not None]
        if not ids:
            return cards
        f = _ro(FACTS)
        posters = {r[0]: r[1] for r in _many(f, "select tmdb_id, poster_path from film_images where tmdb_id in", ids)}
        films = {r[0]: r[1:] for r in _many(f, "select tmdb_id, runtime, genres_json, imdb_id from films where tmdb_id in", ids)}
        critics = {r[0]: _critics(r[1:]) for r in _many(
            f, "select tmdb_id, rt_pct, rt_n, mc_score, cinemascore from critic_scores where tmdb_id in", ids)}
        for fid, d in _omdb_critics(f, [i for i in ids if not critics.get(i)]).items():
            critics[fid] = d
        certs = _certs(f, ids)
        imdb_ids = [v[2] for v in films.values() if v[2]]
        imdb = {}
        if imdb_ids and f is not None:
            try:
                q = "select imdb_id, rating, votes from imdb_ratings where imdb_id in (" + ",".join("?" * len(imdb_ids)) + ")"
                imdb = {r[0]: (r[1], r[2]) for r in f.execute(q, imdb_ids)}
            except sqlite3.Error:
                imdb = {}
        mine = user_choices(user, ids)
        on = {}
        if user:                                     # 4 Nov: films on the person's streaming services
            try:
                import user_prefs
                on = user_prefs.on_services(user_prefs.load(user)["settings"], ids)
            except Exception as e:  # noqa: BLE001
                print(f"[cinemack] services badge failed: {e!r}", flush=True)
        for c in cards:
            fid = c.get("id")
            if fid is None:
                continue
            if posters.get(fid):
                c["poster"] = img(posters[fid], "w185")
                c["poster_lg"] = img(posters[fid], "w342")
            if on.get(fid):
                c["on_services"] = on[fid]
            row = films.get(fid)
            if row:
                if row[0]:
                    c["runtime"] = row[0]
                if not c.get("genres") and row[1]:
                    try:
                        c["genres"] = ", ".join(json.loads(row[1])[:3])
                    except ValueError:
                        pass
                if row[2] and row[2] in imdb:
                    c["imdb"] = {"rating": imdb[row[2]][0], "votes": imdb[row[2]][1]}
            if critics.get(fid):
                c["critics"] = critics[fid]
            if certs.get(fid):
                c["cert"] = certs[fid]
            c["mine"] = mine.get(fid, "")
    except Exception as e:  # noqa: BLE001 - cards still render without the extras
        print(f"[movie-buff] film_info.enrich failed: {e!r}", flush=True)
    return cards


# Round 57 (owner, 20 Nov): the page said "acting (audience 6 of 11; ...)" and showed a COMPLAINT as the quote for a
# praised aspect ("cinematography ... 'monster CGI is not good'"), because the stored quotes are not split by
# sentiment. On the page: "acting (6 of 11 reviews; ...)" for viewer reviews, "(3 of 5 critic reviews; ...)" for
# critics, and the quote is taken from review_aspects with the SAME sentiment (praise -> a positive quote,
# criticism -> a negative one); no matching quote -> no quote. The answer text (reception_cards.py) is unchanged.
_ASPECT = re.compile(r'^(?P<a>.+?) \((?P<who>audience|critics) (?P<k>\d+) of (?P<n>\d+)(?:; ".*")?\)$', re.S)


def _aspect_line(f, fid, s, sign):
    m = _ASPECT.match(s or "") if isinstance(s, str) else None
    if not m:
        return s
    st = "audience" if m["who"] == "audience" else "critic"
    q = _q(f, "select quote from review_aspects where tmdb_id = ? and source_type = ? and aspect = ? and sentiment = ? "
              "and length(quote) between 12 and 160 order by length(quote) desc limit 1", (fid, st, m["a"], sign))
    quote = '; "%s"' % q[0][0].strip() if q and q[0][0] else ""
    return "%s (%s of %s %s%s)" % (m["a"], m["k"], m["n"], "reviews" if st == "audience" else "critic reviews", quote)


def detail(fid, user=""):
    f = _ro(FACTS)
    out = {"id": fid}
    r = _q(f, "select title, year, release_date, runtime, genres_json, overview, tagline, imdb_id, vote_average, "
              "vote_count, collection_name from films where tmdb_id = ?", (fid,))
    if r:
        t, y, rd, rt, g, ov, tag, imdb_id, va, vc, coll = r[0]
        out.update(title=t, year=y, release_date=rd, runtime=rt, overview=ov, tagline=tag, collection=coll,
                   tmdb_rating=round(va, 1) if va else None, tmdb_votes=vc)
        try:
            out["genres"] = json.loads(g or "[]")
        except ValueError:
            out["genres"] = []
        if imdb_id:
            out["imdb_url"] = f"https://www.imdb.com/title/{imdb_id}/"
            ir = _q(f, "select rating, votes from imdb_ratings where imdb_id = ?", (imdb_id,))
            if ir:
                out["imdb"] = {"rating": ir[0][0], "votes": ir[0][1]}
    im = _q(f, "select poster_path, backdrop_path from film_images where tmdb_id = ?", (fid,))
    if im:
        out["poster"] = img(im[0][0], "w500")
        out["backdrop"] = img(im[0][1], "w780")
    out["directors"] = [n for (n,) in _q(f, "select p.name from credits c join people p on p.person_id = c.person_id "
                                            "where c.tmdb_id = ? and c.job = 'Director'", (fid,))]
    cast = _q(f, "select name, character, profile_path from cast_images where tmdb_id = ? order by ord limit 8", (fid,))
    if not cast:
        cast = [(n, ch, None) for n, ch in _q(
            f, "select p.name, c.character from credits c join people p on p.person_id = c.person_id "
               "where c.tmdb_id = ? and c.kind = 'cast' order by c.ord limit 8", (fid,))]
    out["cast"] = [{"name": n, "character": ch, "photo": img(p, "w185")} for n, ch, p in cast]
    money = {}
    m = _q(f, "select budget, budget_src, gross, gross_src from money where tmdb_id = ?", (fid,))
    if m:
        b, bs, g, gs = m[0]
        if b:
            money["budget"], money["budget_src"] = b, bs
        if g:
            money["gross"], money["gross_src"] = g, gs
    real = _q(f, "select budget_real, gross_real, base_year, year from money_real where tmdb_id = ?", (fid,))
    if real and real[0][2] and real[0][2] != real[0][3]:
        money["budget_real"], money["gross_real"], money["base_year"] = real[0][0], real[0][1], real[0][2]
    if money:
        out["money"] = money
    cr = _q(f, "select rt_pct, rt_n, mc_score, cinemascore from critic_scores where tmdb_id = ?", (fid,))
    if cr:
        out["critics"] = _critics(cr[0])
    if not out.get("critics"):
        oc = _omdb_critics(f, [fid]).get(fid)
        if oc:
            out["critics"] = oc
    ce = _certs(f, [fid]).get(fid)
    if ce:
        out["cert"] = ce
    w = _q(_ro(WIKI), "select title, url from pages where film_id = ? limit 1", (fid,))
    if w and w[0][1]:
        out["wiki"] = {"title": w[0][0], "url": w[0][1]}
    out["tmdb_url"] = f"https://www.themoviedb.org/movie/{fid}"
    rc = _q(f, "select critic_score, critic_score_src, audience_score, audience_score_src, gap, gap_label, polar_label, "
               "trend_label, legacy_label, n_critic_stmts, n_aud_reviews, aud_pos, aud_mixed, aud_neg, bombing, data_json "
               "from reception_card where tmdb_id = ?", (fid,))
    if rc:                                  # v7 E5: the reception panel (reception_cards.py)
        cs, css, au, aus, gap, gl, pl, tl, lg, ncs, nau, ap, am, an, bomb, dj = rc[0]
        try:
            data = json.loads(dj or "{}")
        except ValueError:
            data = {}
        out["reception"] = {"critics": None if cs is None else round(cs), "critics_src": css,
                            "audiences": None if au is None else round(au), "audiences_src": aus,
                            "gap": gap, "gap_label": gl, "polar": pl, "trend": tl, "legacy": lg,
                            "n_critic_statements": ncs, "n_audience_reviews": nau,
                            "audience_split": {"positive": ap, "mixed": am, "negative": an} if nau else None,
                            "bombing": bomb, "praise": [_aspect_line(f, fid, x, 1) for x in (data.get("praise") or [])[:4]],
                            "complaints": [_aspect_line(f, fid, x, -1) for x in (data.get("complaints") or [])[:4]],
                            "named": [{"outlet": o, "critic": c, "tone": t} for o, c, t in (data.get("critics") or {}).get("named", [])[:6]],
                            "videos": data.get("videos")}
    out["mine"] = user_choices(user, [fid]).get(fid, "")
    try:                                    # v6 (20 Oct night): trailer, where to watch, review + box-office links
        import links
        out["links"] = links.for_film(fid, out.get("title"), out.get("year"))
    except Exception as e:  # noqa: BLE001
        print(f"[movie-buff] film links failed: {e!r}", flush=True)
    return out


def choose(user, fid, title, choice, message_id=""):
    """Store one taste-button choice; returns the line the page shows."""
    if choice not in CHOICES + ("",):
        raise ValueError("choice must be liked, not, unseen, want or empty")
    import personal                         # the taste profile (rag folder, on sys.path via server.py)
    sig = {"liked": 1, "not": -1}.get(choice, 0)
    personal.record_film(user, fid, title, sig, "card", message_id)
    with _lock:
        c = _users()
        if choice in ("unseen", "want"):
            c.execute("insert or replace into watch values (?,?,?,?,?,?)",
                      (user, fid, title, choice, message_id, time.strftime("%Y-%m-%dT%H:%M:%S")))
        else:
            c.execute("delete from watch where user_id = ? and tmdb_id = ?", (user, fid))
        c.commit()
    name = title or "that film"
    return {
        "liked": f"Saved to your taste: you liked {name}. It shapes your recommendations.",
        "not": f"Saved to your taste: {name} isn't for you. Similar films get pushed down.",
        "unseen": f"Noted: you haven't seen {name}.",
        "want": f"Added {name} to your watchlist.",
        "": f"Cleared your choice for {name}.",
    }[choice]


def watchlist(user, limit=200):
    if not user:
        return []
    with _lock:
        rows = _users().execute("select tmdb_id, title, created_at from watch where user_id = ? and status = 'want' "
                                "order by created_at desc limit ?", (user, limit)).fetchall()
    return [{"id": i, "title": t, "added": a} for i, t, a in rows]
