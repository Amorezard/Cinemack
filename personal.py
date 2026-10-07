"""
v3 personal taste and explained recommendations (PERSONAL=1).

Two signals are kept apart on purpose (the brief's section 16):
    answer feedback   "this answer was good / bad"            -> feedback.py, evaluation only
    film preference   "I liked this film" / "not for me"      -> this file, the taste profile
A thumbs-up on an answer about a film says nothing about liking the film, so it never
touches the profile.

Profile = weights on interpretable features of the films a person liked or disliked:
    genre, keyword (TMDB tags), director, actor (top-billed), decade
plus stated tastes from the conversation ("I hate jump scares" -> conv.py prefs). Stored in
logs/users.db (anonymous browser id; no account, no personal data).

Recommendation = content-based scoring of a candidate set, optionally mixed with MovieLens
item neighbours of liked films (collaborative signal, not relied on alone because Movie Buff
has few users):
    score = relevance to the question (retrieval rank) + taste match + quality prior
            - similarity to disliked films, excluding films already rated
and every pick carries the features that matched, so the answer can say *why*
("you liked Arrival and Blade Runner 2049; this shares Denis Villeneuve and 'alien contact'").

    python personal.py eval            # offline test on MovieLens users (hit@10, leave-out likes)
    python personal.py sweep [300]     # choose the mixing weights on half the users, report on the rest
    python personal.py show <user_id>
"""
import json
import math
import os
import random
import re
import sqlite3
import sys
import time

import facts_db as F

PERSONAL = os.environ.get("PERSONAL", "0") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_DB = os.environ.get("USERS_DB", os.path.join(HERE, "logs", "users.db"))
W = {"genre": 0.35, "keyword": 0.15, "director": 0.6, "actor": 0.25, "decade": 0.1}
# 6 Oct: mixing weights (content, neighbours, popularity) for rerank(), from `personal.py sweep`
# (chosen on half the MovieLens users, reported on the other half). Unset = the 5 Oct formula.
_PW = os.environ.get("PERSONAL_W", "")
try:
    PW = tuple(float(x) for x in _PW.split(",")) if _PW else None
    if PW is not None and len(PW) != 3:
        PW = None
except ValueError:
    PW = None

_U = {"conn": None}


def users():
    if _U["conn"] is None:
        os.makedirs(os.path.dirname(USERS_DB), exist_ok=True)
        c = sqlite3.connect(USERS_DB, check_same_thread=False, timeout=30)
        c.executescript("""
            create table if not exists film_prefs(user_id text, tmdb_id integer, title text, signal integer,
              source text, message_id text, created_at text, primary key (user_id, tmdb_id));
            create table if not exists stated_prefs(user_id text, text text, polarity integer, created_at text,
              primary key (user_id, text));""")
        _U["conn"] = c
    return _U["conn"]


_FEAT = {}


def features(fid, conn=None):
    """{(type, name)} for one film, from facts.db (cached per process)."""
    if fid in _FEAT:
        return _FEAT[fid]
    c = conn or F.connect(readonly=True)
    if c is None:
        return set()
    out = set()
    _FEAT[fid] = out
    row = c.execute("select genres_json, year from films where tmdb_id = ?", (fid,)).fetchone()
    if not row:
        return out
    for g in json.loads(row[0] or "[]"):
        out.add(("genre", g))
    if row[1]:
        out.add(("decade", f"{row[1] // 10 * 10}s"))
    for (k,) in c.execute("select name from keywords where tmdb_id = ? limit 15", (fid,)):
        out.add(("keyword", k))
    for name, job, ordn in c.execute("select p.name, k.job, k.ord from credits k join people p on p.person_id = k.person_id "
                                     "where k.tmdb_id = ? and (k.job = 'Director' or (k.kind = 'cast' and k.ord < 3))", (fid,)):
        out.add(("director", name) if job == "Director" else ("actor", name))
    return out


def profile(likes, dislikes=(), conn=None):
    """Feature weights from liked / disliked film ids. A feature shared by many liked films
    weighs more; IDF-like damping keeps 'Drama' from dominating."""
    c = conn or F.connect(readonly=True)
    prof = {}
    for fid, sign in [(f, 1) for f in likes] + [(f, -1) for f in dislikes]:
        for ft in features(fid, c):
            prof[ft] = prof.get(ft, 0.0) + sign * W[ft[0]]
    return prof


_DF = {}


def _df(conn, ft):
    """How common a feature is (films having it), cached; for damping."""
    if ft in _DF:
        return _DF[ft]
    t, name = ft
    if t == "genre":
        n = conn.execute("select count(*) from films where genres_json like ?", (f'%"{name}"%',)).fetchone()[0]
    elif t == "keyword":
        n = conn.execute("select count(*) from keywords where name = ?", (name,)).fetchone()[0]
    else:
        n = 50
    _DF[ft] = max(n, 1)
    return _DF[ft]


def match(prof, fid, conn):
    """(score, [matched positive features], [matched negative features])."""
    feats = features(fid, conn)
    s, pos, neg = 0.0, [], []
    for ft in feats:
        w = prof.get(ft)
        if not w:
            continue
        damp = 1.0 / math.log(2 + _df(conn, ft) / 50) if ft[0] in ("genre", "keyword") else 1.0
        s += w * damp
        (pos if w > 0 else neg).append((ft, w * damp))
    pos.sort(key=lambda x: -x[1])
    neg.sort(key=lambda x: x[1])
    return s, [f for f, _ in pos[:4]], [f for f, _ in neg[:2]]


def explain(pos, neg, liked_titles):
    bits = []
    for t, name in pos:
        bits.append({"director": f"directed by {name}", "actor": f"stars {name}", "genre": name.lower(),
                     "keyword": f"'{name}'", "decade": f"from the {name}"}[t])
    s = ("shares " + ", ".join(bits)) if bits else ""
    if liked_titles:
        s = f"you liked {', '.join(liked_titles[:2])}" + (f"; {s}" if s else "")
    if neg:
        s += f" (but also {', '.join(n for _t, n in neg)}, which you rated down)"
    return s


def ml_neighbours(fids, movies_db="movies.db", n=30):
    try:
        c = sqlite3.connect(f"file:{movies_db}?mode=ro", uri=True)
        out = {}
        for fid in fids:
            for nb, sc in c.execute("select neighbour_id, score from ml_neighbours where tmdb_id = ? order by rank "
                                    "limit ?", (fid, n)):
                out[nb] = out.get(nb, 0.0) + sc
        return out
    except sqlite3.Error:
        return {}


def rerank(hits, likes, dislikes, stated=(), k=10, movies_db="movies.db"):
    """Re-score recommendation hits for this person. Returns (hits, notes) where each kept hit
    gets h["_why_you"]; films already rated are dropped."""
    conn = F.connect(readonly=True)
    if conn is None or not (likes or dislikes or stated):
        return hits, None
    prof = profile(likes, dislikes, conn)
    for text, pol in stated:                                  # "I hate jump scares" -> keyword weights
        for (kw,) in conn.execute("select distinct name from keywords where ? like '%' || name || '%' "
                                  "and length(name) > 3 limit 5", (text.lower(),)):
            prof[("keyword", kw)] = prof.get(("keyword", kw), 0) + 0.5 * pol
    titles = dict(conn.execute(f"select tmdb_id, title from films where tmdb_id in "
                               f"({','.join(str(int(x)) for x in list(likes) + list(dislikes)) or '0'})"))
    rated = set(likes) | set(dislikes)
    nb = ml_neighbours(likes, movies_db)
    scored = []
    keep = [(rank, h) for rank, h in enumerate(hits) if h["id"] not in rated]
    m = {h["id"]: match(prof, h["id"], conn) for _r, h in keep}
    if PW:
        # Same mix as the sweep: each signal min-max scaled over this candidate list.
        vc = dict(conn.execute(f"select tmdb_id, vote_count from films where tmdb_id in "
                               f"({','.join(str(int(h['id'])) for _r, h in keep) or '0'})"))
        comp = {"c": {i: m[i][0] for i in m}, "n": {i: min(nb.get(i, 0.0), 2.0) for i in m},
                "p": {i: math.log1p(vc.get(i) or 0) for i in m}}
        comp = {k: _minmax(v) for k, v in comp.items()}
    for rank, h in keep:
        s, pos, neg = m[h["id"]]
        rel = 1.0 / (1 + rank * 0.15)                      # keep the question's own ranking mattering
        if PW:
            i = h["id"]
            extra = PW[0] * comp["c"][i] + PW[1] * comp["n"][i] + PW[2] * comp["p"][i]
        else:
            extra = s + min(nb.get(h["id"], 0.0), 1.0) * 0.4
        h = dict(h)
        h["_score_personal"] = round(rel + extra, 3)
        h["_why_you"] = explain(pos, neg, [titles.get(f) for f in likes
                                          if features(f, conn) & set(pos)][:2])
        scored.append(h)
    scored.sort(key=lambda h: -h["_score_personal"])
    return scored[:k], {"likes": len(likes), "dislikes": len(dislikes), "stated": len(stated)}


def _minmax(d):
    if not d:
        return d
    lo, hi = min(d.values()), max(d.values())
    return {k: ((v - lo) / (hi - lo) if hi > lo else 0.0) for k, v in d.items()}


# --------------------------------------------------------------- per-user storage
def record_film(user_id, tmdb_id, title, signal, source="card", message_id=""):
    if not user_id or signal not in (-1, 1, 0):
        return
    c = users()
    if signal == 0:
        c.execute("delete from film_prefs where user_id = ? and tmdb_id = ?", (user_id, tmdb_id))
    else:
        c.execute("insert or replace into film_prefs values (?,?,?,?,?,?,?)",
                  (user_id, tmdb_id, title, signal, source, message_id, F.now()))
    c.commit()


def record_stated(user_id, prefs):
    if not user_id:
        return
    c = users()
    for p in prefs or []:
        c.execute("insert or ignore into stated_prefs values (?,?,?,?)",
                  (user_id, str(p.get("text"))[:120], 1 if p.get("polarity", 1) >= 0 else -1, F.now()))
    c.commit()


def load(user_id):
    if not user_id:
        return [], [], []
    c = users()
    likes = [r[0] for r in c.execute("select tmdb_id from film_prefs where user_id = ? and signal > 0", (user_id,))]
    dis = [r[0] for r in c.execute("select tmdb_id from film_prefs where user_id = ? and signal < 0", (user_id,))]
    st = [(t, p) for t, p in c.execute("select text, polarity from stated_prefs where user_id = ?", (user_id,))]
    return likes, dis, st


def summary(user_id, top=8):
    likes, dis, st = load(user_id)
    prof = profile(likes, dis)
    pos = sorted(((f, w) for f, w in prof.items() if w > 0), key=lambda x: -x[1])[:top]
    neg = sorted(((f, w) for f, w in prof.items() if w < 0), key=lambda x: x[1])[:4]
    return {"likes": len(likes), "dislikes": len(dis),
            "tastes": [f"{t}: {n} (+{w:.2f})" for (t, n), w in pos],
            "avoid": [f"{t}: {n} ({w:.2f})" for (t, n), w in neg],
            "stated": [("likes " if p > 0 else "dislikes ") + t for t, p in st]}


# --------------------------------------------------------------- disliked films named in a question
NEG_RE = re.compile(r"\b(?:but|and|though|although)?\s*(?:i\s+)?(?:disliked|hated|didn'?t like|did not like|"
                    r"couldn'?t stand|can'?t stand|not a fan of|wasn'?t into)\s+([A-Z0-9][\w'’:&.\- ]{1,60}?)"
                    r"(?=[,.!?;]|\s+(?:and|but|so|or)\b|$)", re.I)


def disliked_in(question):
    return [m.group(1).strip() for m in NEG_RE.finditer(question or "")]


# --------------------------------------------------------------- offline evaluation (MovieLens users)
def evaluate(movielens_zip="data/movielens/ml-latest-small.zip", users_n=150, seed=3):
    """Leave-out test on real people's ratings: for each MovieLens user with >= 20 films rated
    4+ that facts.db knows, hide 20% of their liked films, build the profile from the rest
    (likes = 4+, dislikes = <= 2), score a candidate set (the hidden films + 200 random other
    films), and count how many hidden likes reach the top 10.
        popularity    vote_count only (no personalisation)
        content       taste-profile match (this file)
        neighbours    MovieLens item neighbours of the likes (collaborative)
        hybrid        content + neighbours + a small popularity prior
    The neighbours table was built from the same ratings, so 'neighbours' is optimistic here."""
    import csv
    import io
    import zipfile
    conn = F.connect(readonly=True)
    z = zipfile.ZipFile(movielens_zip)

    def read(name):
        member = next(n for n in z.namelist() if n.endswith("/" + name))
        return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"))
    to_tmdb = {r["movieId"]: int(r["tmdbId"]) for r in read("links.csv") if r.get("tmdbId", "").isdigit()}
    known = {r[0]: r[1] or 0 for r in conn.execute("select tmdb_id, vote_count from films")}
    ratings = {}
    for r in read("ratings.csv"):
        t = to_tmdb.get(r["movieId"])
        if t in known:
            ratings.setdefault(r["userId"], []).append((t, float(r["rating"])))
    rnd = random.Random(seed)
    pool = list(known)
    us = [u for u, rs in ratings.items() if sum(1 for _, x in rs if x >= 4) >= 20]
    rnd.shuffle(us)
    res = {k: [0, 0] for k in ("popularity", "content", "neighbours", "hybrid")}
    t0 = time.time()
    for u in us[:users_n]:
        likes = [t for t, x in ratings[u] if x >= 4]
        dis = [t for t, x in ratings[u] if x <= 2]
        rnd.shuffle(likes)
        cut = max(1, len(likes) // 5)
        hidden, train = set(likes[:cut]), likes[cut:]
        seen = {t for t, _ in ratings[u]}
        cand = list(hidden) + [f for f in rnd.sample(pool, 400) if f not in seen][:200]
        prof = profile(train, dis, conn)
        nb = ml_neighbours(train)
        maxv = max(known[f] for f in cand) or 1
        scores = {"popularity": {}, "content": {}, "neighbours": {}, "hybrid": {}}
        for f in cand:
            pop = math.log1p(known[f]) / math.log1p(maxv)
            s, _, _ = match(prof, f, conn)
            scores["popularity"][f] = pop
            scores["content"][f] = s
            scores["neighbours"][f] = nb.get(f, 0.0)
            scores["hybrid"][f] = s + 0.6 * min(nb.get(f, 0.0), 2.0) + 0.3 * pop
        for k, sc in scores.items():
            top = sorted(cand, key=lambda f: -sc[f])[:10]
            res[k][0] += len(hidden & set(top))
            res[k][1] += len(hidden)
    n = min(users_n, len(us))
    lines = [f"Recommendation leave-out test: {n} MovieLens users, hidden liked films ranked among ~200 random others",
             "method        hidden likes in top 10 (recall@10)"]
    for k, (hit, tot) in res.items():
        lines.append(f"{k:12s}  {hit}/{tot} = {hit / max(tot, 1):.3f}")
    lines.append(f"({time.time() - t0:.0f}s)")
    print("\n".join(lines))
    os.makedirs("results", exist_ok=True)
    open("results/personal_eval.txt", "w").write("\n".join(lines) + "\n")


def sweep(movielens_zip="data/movielens/ml-latest-small.zip", users_n=300, seed=3):
    """5 Oct the hybrid (0.246) lost to neighbours alone (0.370): content scores grow with the
    number of liked films, so they swamped the other signals. Here each signal is min-max scaled
    per user over that user's candidates, the weights (content, neighbours, popularity) are
    chosen on the first half of the users and reported on the second half (never seen while
    choosing). Same leave-out setup as evaluate(); MovieLens neighbours were built from the same
    ratings, so anything leaning on them is optimistic (stated in the report).
    Decision rule (6 Oct, before running): PERSONAL=1 with the chosen PERSONAL_W if, on the
    held-out half, it beats popularity AND content-only, and is within 0.02 of neighbours-only
    or better (neighbours-only is not usable alone on the site: it needs films MovieLens knows)."""
    import csv
    import io
    import zipfile
    conn = F.connect(readonly=True)
    z = zipfile.ZipFile(movielens_zip)

    def read(name):
        member = next(n for n in z.namelist() if n.endswith("/" + name))
        return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"))
    to_tmdb = {r["movieId"]: int(r["tmdbId"]) for r in read("links.csv") if r.get("tmdbId", "").isdigit()}
    known = {r[0]: r[1] or 0 for r in conn.execute("select tmdb_id, vote_count from films")}
    ratings = {}
    for r in read("ratings.csv"):
        t = to_tmdb.get(r["movieId"])
        if t in known:
            ratings.setdefault(r["userId"], []).append((t, float(r["rating"])))
    rnd = random.Random(seed)
    pool = list(known)
    us = [u for u, rs in ratings.items() if sum(1 for _, x in rs if x >= 4) >= 20]
    rnd.shuffle(us)
    us = us[:users_n]
    t0 = time.time()
    data = []                         # per user: (hidden, cand, {c,n,p} scaled)
    for u in us:
        likes = [t for t, x in ratings[u] if x >= 4]
        dis = [t for t, x in ratings[u] if x <= 2]
        rnd.shuffle(likes)
        cut = max(1, len(likes) // 5)
        hidden, train = set(likes[:cut]), likes[cut:]
        seen = {t for t, _ in ratings[u]}
        cand = list(hidden) + [f for f in rnd.sample(pool, 400) if f not in seen][:200]
        prof = profile(train, dis, conn)
        nb = ml_neighbours(train)
        comp = {"c": {f: match(prof, f, conn)[0] for f in cand},
                "n": {f: min(nb.get(f, 0.0), 2.0) for f in cand},
                "p": {f: math.log1p(known[f]) for f in cand}}
        data.append((hidden, cand, {k: _minmax(v) for k, v in comp.items()}))
    half = len(data) // 2
    sel, test = data[:half], data[half:]

    def recall(rows, w):
        hit = tot = 0
        for hidden, cand, comp in rows:
            sc = {f: w[0] * comp["c"][f] + w[1] * comp["n"][f] + w[2] * comp["p"][f] for f in cand}
            top = sorted(cand, key=lambda f: (-sc[f], f))[:10]
            hit += len(hidden & set(top))
            tot += len(hidden)
        return hit / max(tot, 1)
    grid = [(c, n, p) for c in (0, 0.25, 0.5, 1, 2) for n in (0, 0.5, 1, 2, 4) for p in (0, 0.1, 0.25, 0.5, 1)
            if (c, n, p) != (0, 0, 0)]
    res = sorted(((recall(sel, w), w) for w in grid), key=lambda x: -x[0])
    best = res[0][1]
    fixed = {"popularity": (0, 0, 1), "content": (1, 0, 0), "neighbours": (0, 1, 0)}
    lines = [f"Hybrid weight sweep: {len(data)} MovieLens users; weights chosen on {len(sel)}, "
             f"reported on the other {len(test)}",
             "Signals min-max scaled per user. (content, neighbours, popularity) weights.",
             "Top 5 on the choosing half:"]
    lines += [f"  {w}  recall@10 {r:.3f}" for r, w in res[:5]]
    lines.append("Held-out half, recall@10:")
    out = {k: recall(test, w) for k, w in fixed.items()}
    out[f"hybrid {best}"] = recall(test, best)
    for k, v in out.items():
        lines.append(f"  {k:28s} {v:.3f}")
    hb = out[f"hybrid {best}"]
    ok = hb > out["popularity"] and hb > out["content"] and hb >= out["neighbours"] - 0.02
    lines.append(f"Decision rule: beats popularity and content, within 0.02 of neighbours -> "
                 f"{'PASS: PERSONAL=1 PERSONAL_W=' + ','.join(str(x) for x in best) if ok else 'FAIL: PERSONAL stays 0'}")
    lines.append("Note: MovieLens neighbours come from the same ratings, so 'neighbours' and any mix "
                 "leaning on it are optimistic.")
    lines.append(f"({time.time() - t0:.0f}s)")
    print("\n".join(lines))
    os.makedirs("results", exist_ok=True)
    open("results/personal_sweep.txt", "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        sweep(users_n=int(sys.argv[2]) if len(sys.argv) > 2 else 300)
    elif len(sys.argv) > 1 and sys.argv[1] == "eval":
        evaluate(users_n=int(sys.argv[2]) if len(sys.argv) > 2 else 150)
    elif len(sys.argv) > 2 and sys.argv[1] == "show":
        print(json.dumps(summary(sys.argv[2]), indent=1))
    else:
        print(__doc__)
