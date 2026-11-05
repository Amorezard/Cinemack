#!/usr/bin/env python3
"""
v7 (1 Nov): short films, behind the SHORTS switch (off until its rule passes; rule written 1 Nov before any result).

The catalogue already holds short films (IMDb title type short / tvShort, or a TMDB runtime of 40 minutes or less:
1,093 IMDb shorts among our films), but a question about one competes with every feature film: "the Pixar short
about a dumpling" puts features about food and families ahead of it. With SHORTS=1, a question that says it is about
a short film searches only the shorts; the answer's context says the film is a short and gives its length. Anything
else - and a short-film question that finds nothing - takes the normal film path unchanged. SHORTS=0 = exactly the
earlier code (no change to prompts, so the model-call cache is untouched).

How a question reaches the shorts search (rules, no planner change):
    it says short film / animated short / Pixar (Disney, Oscar-winning ...) short / "the short that played before" /
    "a short about ..."; not "short story", "short time", "short miniseries" and the like.
    Then: a short named in the question -> that short; recommendation wording -> the best-known matching shorts;
    otherwise a description -> vector + keyword search over the shorts only, with the film path's distance cut.

    python shorts.py coverage                       # shorts in facts.db / the catalogue; well-known shorts missing
    python shorts.py try "the Pixar short about a dumpling that comes to life"
    python shorts.py routecheck sets/tune.json sets/check.json sets/analytic.json sets/shorts.json

Rule (1 Nov, before any result): SHORTS=1 if the 15-question shorts set (sets/shorts.json) passes >= off + 4 judge
passes, AND tune / check each lose <= 1 found film, AND the analytic set loses <= 1 judge pass with grounding not
lower - all against an off arm run the same night.
"""
import json
import os
import re
import sqlite3
import sys

SHORTS = os.environ.get("SHORTS", "0") == "1"
SHORT_MAX_RUNTIME = int(os.environ.get("SHORT_MAX_RUNTIME", "40"))     # minutes (the Academy's limit for a short)
SHORT_POOL = int(os.environ.get("SHORT_POOL", "4000"))                 # nearest vectors searched, then shorts kept

R_SHORT = re.compile(
    r"\b(short films?|short movies?|animated shorts?|live[- ]action shorts?|cartoon shorts?|theatrical shorts?|"
    r"short subjects?|"
    r"(pixar|disney|dreamworks|aardman|ghibli|oscar[- ]winning|oscar[- ]nominated|academy award[- ]winning|"
    r"award[- ]winning|silent|student|stop[- ]motion|documentary|youtube|vimeo) shorts?|"
    r"shorts? (that|which) (played|plays|was shown|ran|came) (before|with|in front of)|"
    r"(the|a|an|that|this) short (about|where|with|in which|that|from|by|called|named|before|of)|"
    r"(oscar|academy award) for (best )?(animated |live[- ]action |documentary )?short)\b", re.I)
R_NOT_SHORT = re.compile(r"\bshort (stor(y|ies)|time|while|list|answer|version|run|term|notice|hair|guy|man|woman|"
                         r"king|circuit|miniseries|series|season)\b|\b(in|too|so|cut it|life is|long story) short\b",
                         re.I)
R_REC = re.compile(r"\b(recommend\w*|suggest\w*|similar to|something like|shorts? like|what should i watch|"
                   r"(good|best|great|favou?rite|underrated) ([\w'-]+ ){0,2}shorts|any (good |great )?([\w'-]+ ){0,2}shorts|"
                   r"(some|a few|list( of)?|top \d+) .{0,25}shorts)\b", re.I)

# Well-known shorts for `coverage` (are they in the catalogue at all?)
KNOWN = ["Bao", "Piper", "Paperman", "Feast", "Geri's Game", "Hair Love", "Luxo Jr.", "La Luna", "For the Birds",
         "Lou", "Kitbull", "Purl", "Sanjay's Super Team", "Partly Cloudy", "Presto", "Day & Night", "The Blue Umbrella",
         "Lava", "Inner Workings", "Get a Horse!", "Tin Toy", "Knick Knack", "Boundin'", "Lifted", "One Man Band",
         "Kitbull", "Float", "Loop", "Wind", "Out", "The Present", "World of Tomorrow", "The Man Who Planted Trees",
         "A Grand Day Out", "The Wrong Trousers", "A Close Shave", "La Jetée", "Un Chien Andalou",
         "Le Voyage dans la Lune", "The Lost Thing", "Borrowed Time", "Hedgehog in the Fog", "Logorama",
         "The Red Balloon", "Neighbours", "Bambi Meets Godzilla", "Duck Amuck", "What's Opera, Doc?", "Steamboat Willie",
         "Skeleton Dance", "Flowers and Trees", "Validation", "Signs", "Lights Out", "The Silent Child", "Skin"]

_DB = {"facts": None, "tried": False}
_IDX = {"ids": None, "info": None}


def facts():
    if not _DB["tried"]:
        _DB["tried"] = True
        try:
            import facts_db as F
            _DB["facts"] = F.connect(readonly=True)
        except Exception:  # noqa: BLE001
            _DB["facts"] = None
    return _DB["facts"]


def _q(sql, p=()):
    c = facts()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.casefold()).split())


def short_info():
    """{tmdb_id: (title, year, runtime, imdb title type)} for every short film in facts.db."""
    if _IDX["info"] is None:
        rows = _q(f"""select f.tmdb_id, f.title, f.year, f.runtime, t.title_type from films f
            left join imdb_titles t on t.tconst = f.imdb_id
            where t.title_type in ('short', 'tvShort')
               or (coalesce(t.title_type, 'movie') not in ('tvSeries', 'tvMiniSeries', 'tvEpisode')
                   and f.runtime between 1 and {SHORT_MAX_RUNTIME})""")
        _IDX["info"] = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}
        _IDX["ids"] = set(_IDX["info"])
    return _IDX["info"]


def is_short(fid):
    try:
        return int(fid) in short_info()
    except (TypeError, ValueError):
        return False


def wants_short(question):
    """True when the question says it is about a short film."""
    if not SHORTS or not question:
        return False
    t = R_NOT_SHORT.sub(" ", question)
    return bool(R_SHORT.search(t))


def label(fid):
    """The line the answer's context gets for a short (SHORTS=1 only), or None."""
    if not SHORTS:
        return None
    i = short_info().get(int(fid)) if str(fid).lstrip("-").isdigit() else None
    if not i:
        return None
    _t, _y, rt, typ = i
    return ("Format: short film" + (f", {rt} min" if rt else "")
            + (" (IMDb title type: short)" if typ in ("short", "tvShort") else "") + " [TMDB / IMDb]")


def retrieve_short(question, k=3, conn=None):
    """Hits for a short-film question (only shorts), shaped like retrieve()'s (with _plan), or []."""
    import retrieve as R
    ids = short_info()
    if not ids or conn is None:
        return []
    rec = bool(R_REC.search(question)) or R.is_recommendation(question)
    named = [f for f in R.titles_in(question, conn) if f["id"] in ids]
    if named and not rec:
        hits = R.hydrate(conn, [(named[0]["id"], 0.0)])
        intent, films = "named_film", [named[0]["title"]]
    else:
        q_vec = R.embed_query(question)
        dist = {}
        for mid, d in R.knn(conn, q_vec, SHORT_POOL):
            if mid in ids and (mid not in dist or d < dist[mid]):
                dist[mid] = d
        vec = sorted(dist, key=dist.get)[:200]
        kw = [m for m in R.keyword_ranking(conn, question, 3000) if m in ids][:200]
        fused = R.rrf(vec, kw)
        if rec:
            cand = R.hydrate(conn, [(m, dist.get(m)) for m in fused[:60]])
            cand.sort(key=lambda h: -(h["vote_count"] or 0) * (1.0 if h["distance"] is None
                                                               else max(0.05, 1.2 - h["distance"])))
            hits = cand[:max(k, 6)]
            intent = "recommend"
        else:
            cand = R.hydrate(conn, [(m, dist.get(m)) for m in fused[:k * 4]])
            hits = [h for h in cand if h["distance"] is not None and h["distance"] <= R.MAX_DISTANCE][:k]
            intent = "identify"
        films = []
    for h in hits:
        h["_short"] = True
    if hits:
        hits[0]["_plan"] = {"intent": intent, "films": films, "media": "short", "search_query": question}
    return hits


# ------------------------------------------------------------------ commands
def cmd_coverage():
    info = short_info()
    print(f"short films in facts.db: {len(info):,} (IMDb short / tvShort: "
          f"{sum(1 for v in info.values() if v[3] in ('short', 'tvShort')):,}; runtime <= {SHORT_MAX_RUNTIME} min only: "
          f"{sum(1 for v in info.values() if v[3] not in ('short', 'tvShort')):,})")
    cat, vec = set(), set()
    try:                                     # 1 Nov fix: movie_vectors is a sqlite-vec table - open as retrieve does
        import retrieve as R
        c = R.open_db("movies.db")
        cat = {r[0] for r in c.execute("select id from movies")}
        try:
            vec = {r[0] for r in c.execute("select distinct movie_id from movie_vectors")}
        except sqlite3.Error as e:
            print(f"  (vectors not listed: {e})")
    except Exception as e:  # noqa: BLE001
        print(f"  (movies.db: {e})")
    print(f"  in the search catalogue (movies.db): {len(set(info) & cat):,}; with vectors: {len(set(info) & vec):,}")
    by = {}
    for fid, (t, _y, _r, _ty) in info.items():
        by.setdefault(norm(t), []).append(fid)
    have, miss = [], []
    for t in dict.fromkeys(KNOWN):
        fids = by.get(norm(t), [])
        (have if any(f in cat for f in fids) else miss).append(t if fids else t + " (not in facts.db)")
    print(f"  well-known shorts searchable: {len(have)}/{len(have) + len(miss)}")
    print(f"  missing: {', '.join(miss)}")
    top = sorted(((v[0], v[1], v[2], fid) for fid, v in info.items() if fid in cat), key=lambda x: str(x[1]))[-5:]
    print(f"  newest in the catalogue: {[f'{t} ({y})' for t, y, _r, _f in top]}")


def cmd_routecheck(paths):
    global SHORTS
    SHORTS = True
    for p in paths:
        d = json.load(open(p))
        qs = d if isinstance(d, list) else d["questions"]
        hit = []
        for q in qs:
            t = q.get("question") or (q.get("turns") or [""])[-1]
            t = t if isinstance(t, str) else t.get("user", "")
            if wants_short(t):
                hit.append(f"  {q.get('id', '?'):12s} {t[:100]!r}")
        print(f"{p}: {len(qs)} questions; to the shorts search: {len(hit)}")
        print("\n".join(hit[:40]))


def cmd_try(text):
    global SHORTS
    SHORTS = True
    import retrieve as R
    conn = R.open_db("movies.db")
    print("short-film question:", wants_short(text))
    for h in retrieve_short(text, 5, conn):
        print(f"  {h['title']} ({h['release_year']}) d={h['distance']}  {label(h['id'])}  plan={h.get('_plan')}")


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "coverage":
        return cmd_coverage()
    if a[0] == "routecheck":
        return cmd_routecheck(a[1:])
    if a[0] == "try":
        return cmd_try(" ".join(a[1:]))
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
