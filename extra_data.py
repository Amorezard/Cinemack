"""
v6 free data in answers (EXTRA_DATA=1 in tools.py; the site's links use it whatever the switch).

Reads facts.db read-only; every table comes from fetch_free_data.py or fetch_v6_data.py and is
optional (a missing table gives no line, never an error).

    critic_line(fid)            Rotten Tomatoes / Metacritic from OMDb, for films whose Wikipedia
                                reception text gave none (critic_scores has no row)
    critic_review_lines(fid)    up to 3 professional reviews: Guardian (stars + standfirst) and NYT
                                (summary), each with critic, date and source tag
    watch_block(fid, title)     where to stream / rent / buy in the US (JustWatch via TMDB), the age
                                rating and the main trailer
    audience_block(fid, title)  IMDb users, MovieLens 32M, Netflix views, trailer reach (today's
                                numbers, labelled as after release), and how early the first trailer
                                came out (a pre-release signal)
    trailer(fid)                {"key", "name", "published_at", "views"} of the main trailer, or None
    certification(fid)          US age rating ("PG-13"), or None

    python extra_data.py "Oppenheimer"      # print every block for one film
"""
import json
import os
import sqlite3
import sys

import facts_db as F

_C = {"conn": None, "tried": False}
KIND_LABEL = {"flatrate": "streaming on", "free": "free on", "ads": "free with ads on", "rent": "to rent on",
              "buy": "to buy on"}


def db():
    if not _C["tried"]:
        _C["tried"] = True
        _C["conn"] = F.connect(readonly=True)
    return _C["conn"]


def _q(sql, p=()):
    c = db()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def _month(ts):
    return str(ts or "")[:7]


def _n(v):
    if v is None:
        return "?"
    if v >= 1e9:
        return f"{v / 1e9:.1f} billion"
    if v >= 1e6:
        return f"{v / 1e6:.1f} million"
    return f"{int(v):,}"


# ------------------------------------------------------------------ critics
def critic_line(fid):
    r = _q("select rt_pct, mc_score, fetched_at from omdb where tmdb_id = ? and status = 'ok' "
           "and (rt_pct is not null or mc_score is not null)", (fid,))
    if not r:
        return None
    rt, mc, ts = r[0]
    bits = ([f"Rotten Tomatoes {rt:.0f}% positive"] if rt is not None else []) + \
           ([f"Metacritic {mc:.0f}/100"] if mc is not None else [])
    return "Critics' scores: " + "; ".join(bits) + f" [OMDb, fetched {_month(ts)}]"


def critic_reviews(fid, n=3):
    """Guardian first (it has stars), then NYT; newest first within each."""
    rows = _q("select source, critic, stars, published, url, summary, headline from critic_reviews "
              "where tmdb_id = ? and match >= 1 order by source = 'guardian' desc, published desc", (fid,))
    firsts, rest, seen = [], [], set()
    for src, critic, stars, pub, url, summary, head in rows:
        d = {"source": src, "critic": critic, "stars": stars, "published": pub, "url": url,
             "summary": summary, "headline": head}
        (rest if src in seen else firsts).append(d)      # one per outlet before a second from the same one
        seen.add(src)
    return (firsts + rest)[:n]


def critic_review_lines(fid, n=3):
    lines = []
    for r in critic_reviews(fid, n):
        outlet = "The Guardian" if r["source"] == "guardian" else "The New York Times"
        who = ", ".join(x for x in (r["critic"], r["published"]) if x)
        stars = f", {r['stars']:g}/5 stars" if r["stars"] is not None else ""
        text = " ".join((r["summary"] or r["headline"] or "").split())[:300]
        tag = "Guardian review" if r["source"] == "guardian" else "NYT review"
        lines.append(f"{outlet} ({who}{stars}): \"{text}\" [{tag}]")
    return lines


# ------------------------------------------------------------------ watch
def certification(fid, region="US"):
    rows = _q("select certification, release_type from certifications where tmdb_id = ? and region = ? "
              "order by release_type = 3 desc, release_type", (fid, region))
    return rows[0][0] if rows else None


def trailer(fid):
    rows = _q("select video_key, type, official, published_at, name from film_videos where tmdb_id = ? "
              "and site = 'YouTube' and type in ('Trailer','Teaser')", (fid,))
    if not rows:
        return None
    key, typ, off, pub, name = min(rows, key=lambda r: (r[1] != "Trailer", not r[2], r[3] or "9999"))
    v = _q("select views, fetched_at from yt_videos where video_key = ?", (key,))
    return {"key": key, "type": typ, "name": name, "published_at": (pub or "")[:10],
            "views": v[0][0] if v else None, "views_at": _month(v[0][1]) if v else None}


def providers(fid, region="US"):
    rows = _q("select kind, provider_name, priority, fetched_at from watch_providers where tmdb_id = ? and region = ? "
              "order by priority", (fid, region))
    out = {}
    for kind, name, _p, ts in rows:
        out.setdefault(kind, [])
        if name not in out[kind]:
            out[kind].append(name)
    return out, (_month(rows[0][3]) if rows else None)


def watch_block(fid, title):
    lines = []
    pv, ts = providers(fid)
    if pv:
        parts = [f"{KIND_LABEL[k]} {', '.join(pv[k][:5])}" for k in ("flatrate", "free", "ads", "rent", "buy") if pv.get(k)]
        lines.append("Where to watch in the US: " + "; ".join(parts) + f" [JustWatch via TMDB, {ts}]")
    elif _q("select 1 from tmdb_extra_done where tmdb_id = ?", (fid,)):
        lines.append("Where to watch in the US: no streaming, rental or purchase listing [JustWatch via TMDB]")
    c = certification(fid)
    if c:
        lines.append(f"US age rating: {c} [TMDB release dates]")
    t = trailer(fid)
    if t:
        lines.append(f"{t['type']} on YouTube: \"{t['name']}\", published {t['published_at']}"
                     + (f", {_n(t['views'])} views as of {t['views_at']}" if t.get("views") else "")
                     + " (link shown under the answer) [TMDB videos]")
    return (f"{title} - where to watch\n" + "\n".join(lines)) if lines else None


# ------------------------------------------------------------------ audience / reach
def audience_block(fid, title):
    lines = []
    r = _q("select r.rating, r.votes, r.fetched_at from films f join imdb_ratings r on r.imdb_id = f.imdb_id "
           "where f.tmdb_id = ?", (fid,))
    if r and r[0][0]:
        lines.append(f"IMDb users: {r[0][0]:.1f}/10 from {r[0][1]:,} votes [IMDb datasets, {_month(r[0][2])}]")
    m = _q("select n_ratings, mean, top_tags_json from ml32_stats where tmdb_id = ?", (fid,))
    if m and m[0][0]:
        tags = [t for t, _c in json.loads(m[0][2] or "[]")[:6]]
        lines.append(f"MovieLens users: {m[0][1]:.2f}/5 from {m[0][0]:,} ratings"
                     + (f"; most used tags: {', '.join(tags)}" if tags else "") + " [MovieLens 32M, 2023]")
    nf = _q("select period, views, hours from netflix_views where tmdb_id = ? order by period", (fid,))
    if nf:
        lines.append("Netflix: " + "; ".join(f"{p}: {_n(v)} views" if v else f"{p}: {_n(h)} hours viewed"
                                             for p, v, h in nf) + " [Netflix engagement report]")
    first = _q("select min(substr(v.published_at, 1, 10)), count(*), coalesce(f.us_release_date, f.release_date) "
               "from film_videos v join films f using(tmdb_id) where v.tmdb_id = ? and v.type in ('Trailer','Teaser') "
               "and v.published_at < coalesce(f.us_release_date, f.release_date)", (fid,))
    if first and first[0][0] and first[0][2]:
        import datetime
        try:
            lead = (datetime.date.fromisoformat(first[0][2][:10]) - datetime.date.fromisoformat(first[0][0])).days
            lines.append(f"Marketing lead time: first trailer or teaser on YouTube {lead} days before release, "
                         f"{first[0][1]} before release in all [TMDB videos; YouTube dates start in 2005]")
        except ValueError:
            pass
    t = trailer(fid)
    if t and t.get("views"):
        lines.append(f"Main trailer reach today: {_n(t['views'])} YouTube views as of {t['views_at']} "
                     "(counted long after release, so not a measure of pre-release interest) [YouTube Data API]")
    nrev = _q("select count(*), sum(views) from yt_videos where tmdb_id = ? and kind = 'review'", (fid,))
    if nrev and nrev[0][0]:
        lines.append(f"YouTube review videos found: {nrev[0][0]} with {_n(nrev[0][1] or 0)} views in all [YouTube Data API]")
    return (f"{title} - audience and reach\n" + "\n".join(lines)) if lines else None


# ------------------------------------------------------------------ box office (IMDb API, fetch_imdb_api.py)
def boxoffice_block(fid, title):
    r = _q("select budget, budget_cur, gross_ww, gross_dom, gross_intl, opening_dom, opening_date, fetched_at "
           "from imdb_boxoffice where tmdb_id = ? and status = 'ok'", (fid,))
    if not r:
        return None
    b, bcur, ww, dom, intl, op, opd, ts = r[0]
    usd = lambda v: f"${v / 1e6:,.1f} million" if v and v >= 1e6 else (f"${v:,.0f}" if v else None)  # noqa: E731
    bits = []
    if dom:
        bits.append(f"US and Canada {usd(dom)}")
    if intl:
        bits.append(f"international {usd(intl)}")
    if ww:
        bits.append(f"worldwide {usd(ww)}")
    if op:
        bits.append(f"opening weekend (US and Canada) {usd(op)}" + (f", weekend ending {opd}" if opd else ""))
        if dom:
            bits.append(f"total / opening = {dom / op:.1f} (a measure of word of mouth: about 2.5-3 is typical "
                        "for a wide release, higher means it kept selling)")
    if b and (bcur in (None, "USD")):
        bits.append(f"production budget {usd(b)}")
    if not bits:
        return None
    return f"{title} - box office from Box Office Mojo (via the IMDb API, fetched {_month(ts)})\n" + "; ".join(bits) + \
        " [IMDb / Box Office Mojo]"


if __name__ == "__main__":
    name = " ".join(sys.argv[1:]) or "Oppenheimer"
    rows = _q("select tmdb_id, title, year from films where title = ? order by vote_count desc limit 1", (name,))
    if not rows:
        sys.exit(f"no film called {name!r} in facts.db")
    fid, t, y = rows[0]
    print(f"{t} ({y}) tmdb {fid}")
    print("critic_line:", critic_line(fid))
    print("reviews:", *critic_review_lines(fid), sep="\n  ")
    print(watch_block(fid, t))
    print(audience_block(fid, t))
    print(boxoffice_block(fid, t))
