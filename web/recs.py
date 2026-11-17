"""v8.1 slice 5.2 (15 Nov 2026): one ranking interface for film recommendations.

    rank(conn, plan, likes=(), dislikes=(), k=10)  ->  [{media_id, rank, score, reason, signals}, ...]

Order of work (kit package 5), each step a small function so it can be tested alone:
  1. candidates   MovieLens neighbours of the liked films (movies.db, read-only) + the most-voted films that meet
                  the plan (so a person with no history still gets an answer) - or the caller's own list
  2. hard limits  web/plan_sql.matching_ids on the validated plan: year, length, genres, US rating, services, the
                  "never show" ids (watched films arrive in the plan from the SERVER). A film that fails a limit can
                  not come back at any later step. Films the person already rated are removed too.
  3. score        weights x (content match, neighbour strength, popularity), each min-max scaled over the
                  candidates - the same three signals as personal.rerank, measured in the 14 Nov benchmark
  4. variety      genre MMR (lambda 0.85, as benchmarked): a film very like one already picked moves down
  5. reason       exactly ONE reason code per film, taken from the numbers above (never from model text):
                  similar_to_liked | shares_director | shares_cast | shares_genre | shares_theme | popular_fits_limits
                  The page turns the code + its parameters into words (Pick Tonight "Why I picked this", slice 6.2).
The score is a ranking number. It is not a probability and is never shown as one.

Switch RECS_V2 (off by default). Nothing calls rank() yet; while the switch is off `enabled()` is False and the
site keeps using personal.rerank. Films only: a TV-only plan raises Unsupported (shows have no MovieLens signal).
Weights: (content, neighbours, popularity). The 14 Nov benchmark picked different weights under its two test
protocols, so ONE set is fixed by tools/v81_recs_single.py (results/v81_recs_single.json); until that file
exists the fallback below is used, and RECS_V2 must stay off.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_WEB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_WEB)
sys.path.insert(0, _WEB)
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
import plan_sql as PS  # noqa: E402
import query_plan as QP  # noqa: E402

RECS_V2 = os.environ.get("RECS_V2", "0") == "1"
VERSION = "recs-v2.0"
FALLBACK_W = (2.0, 2.0, 0.0)
WEIGHTS_FILE = os.environ.get("RECS_V2_WEIGHTS", os.path.join(_ROOT, "results", "v81_recs_single.json"))
# round 48: the third candidate (tools/v81_recs_v3.py, tested on 2021) is read first; the 15 Nov file failed its rule
WEIGHTS_V3 = os.path.join(_ROOT, "results", "v81_recs_v3.json")
MOVIES_DB = os.environ.get("MOVIES_DB", os.path.expanduser("~/movie-buff/rag/movies.db"))
MMR_LAMBDA = 0.85
NB_PER_LIKE, NB_CAP, POPULAR_POOL, MMR_POOL = 30, 2.0, 300, 60
REASONS = ("similar_to_liked", "shares_director", "shares_cast", "shares_genre", "shares_theme", "popular_fits_limits")
_FEATURE_REASON = {"director": "shares_director", "actor": "shares_cast", "genre": "shares_genre",
                   "keyword": "shares_theme", "decade": None}


class Unsupported(ValueError):
    pass


def weights() -> Tuple[Tuple[float, float, float], str]:
    """((content, neighbours, popularity), where they came from)."""
    files = [(WEIGHTS_FILE, "v81_recs_single")]
    if "RECS_V2_WEIGHTS" not in os.environ:
        files.insert(0, (WEIGHTS_V3, "v81_recs_v3"))
    for path, name in files:
        try:
            with open(path) as fh:
                d = json.load(fh)
            w = tuple(float(x) for x in d["weights"])
            if len(w) == 3 and all(x >= 0 and math.isfinite(x) for x in w) and any(w) and d.get("rule") == "PASS":
                return w, name
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return FALLBACK_W, "fallback"


def enabled() -> bool:
    """True only when the switch is on AND one benchmarked weight set passed its rule."""
    return RECS_V2 and weights()[1] != "fallback"


def minmax(d: Dict[int, float]) -> Dict[int, float]:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    return {k: ((v - lo) / (hi - lo) if hi > lo else 0.0) for k, v in d.items()}


def neighbour_scores(likes: Sequence[int], movies_db: Optional[str] = None) -> Tuple[Dict[int, float], Dict[int, int]]:
    """({film: summed neighbour score}, {film: the liked film that contributed most}). Empty when movies.db is absent."""
    path = movies_db or MOVIES_DB
    total: Dict[int, float] = {}
    best: Dict[int, Tuple[float, int]] = {}
    if not likes or not path or not os.path.exists(path):
        return {}, {}
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for f in likes:
                for nb, sc in c.execute("select neighbour_id, score from ml_neighbours where tmdb_id = ? order by rank limit ?",
                                        (int(f), NB_PER_LIKE)):
                    total[nb] = total.get(nb, 0.0) + (sc or 0.0)
                    if nb not in best or (sc or 0.0) > best[nb][0]:
                        best[nb] = (sc or 0.0, int(f))
        finally:
            c.close()
    except sqlite3.Error:
        return {}, {}
    return total, {k: v[1] for k, v in best.items()}


def candidates(conn, plan: QP.QueryPlan, nb: Dict[int, float]) -> List[int]:
    """Neighbours of the liked films plus the most-voted films meeting the plan (no limits applied to the first part yet)."""
    out = list(nb)
    seen = set(out)
    for i in PS.matching_ids(conn, plan, limit=POPULAR_POOL):
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def apply_limits(conn, plan: QP.QueryPlan, cands: Iterable[int], rated: Iterable[int] = ()) -> List[int]:
    """The candidates that meet every hard limit of the plan and were not rated by the person. Step 2: nothing
    removed here may return."""
    rated = {int(x) for x in rated}
    ids = [int(i) for i in dict.fromkeys(cands) if int(i) not in rated]
    if not ids:
        return []
    keep = set(PS.matching_ids(conn, plan, candidate_ids=ids))
    return [i for i in ids if i in keep]


def _genres(conn, ids: Sequence[int]) -> Dict[int, frozenset]:
    out: Dict[int, frozenset] = {}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        for fid, g in conn.execute("select tmdb_id, genres_json from films where tmdb_id in (%s)" % ",".join("?" * len(chunk)), chunk):
            try:
                v = json.loads(g or "[]")
                out[fid] = frozenset(x for x in v if isinstance(x, str)) if isinstance(v, list) else frozenset()
            except ValueError:
                out[fid] = frozenset()
    return out


def _votes(conn, ids: Sequence[int]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        out.update(conn.execute("select tmdb_id, coalesce(vote_count, 0) from films where tmdb_id in (%s)" % ",".join("?" * len(chunk)), chunk))
    return out


def mmr(scores: Dict[int, float], genres: Dict[int, frozenset], k: int, lam: float = MMR_LAMBDA) -> List[int]:
    """Greedy genre-variety re-rank of the best MMR_POOL films (the benchmarked rule)."""
    left = sorted(scores, key=lambda f: (-scores[f], f))[:max(MMR_POOL, k)]
    out: List[int] = []
    while left and len(out) < k:
        def val(f):
            if not out:
                return scores[f]
            g = genres.get(f, frozenset())
            sim = max((len(g & genres.get(o, frozenset())) / len(g | genres.get(o, frozenset())))
                      if (g | genres.get(o, frozenset())) else 0.0 for o in out)
            return lam * scores[f] - (1 - lam) * sim
        best = max(left, key=lambda f: (val(f), -f))
        out.append(best)
        left.remove(best)
    return out


def reason_for(fid: int, parts: Dict[str, float], pos_features, nb_source: Dict[int, int], has_history: bool) -> Dict[str, object]:
    """Exactly one reason, from the weighted parts of this film's score. parts = {"c","n","p"} already weighted."""
    if has_history:
        if parts.get("n", 0.0) > 0 and parts["n"] >= parts.get("c", 0.0) and fid in nb_source:
            return {"code": "similar_to_liked", "params": {"liked_media_id": "movie:%d" % nb_source[fid]}}
        if parts.get("c", 0.0) > 0:
            for ftype, name in pos_features:
                code = _FEATURE_REASON.get(ftype)
                if code:
                    return {"code": code, "params": {"name": str(name)[:80]}}
        if parts.get("n", 0.0) > 0 and fid in nb_source:
            return {"code": "similar_to_liked", "params": {"liked_media_id": "movie:%d" % nb_source[fid]}}
    return {"code": "popular_fits_limits", "params": {}}


def rank(conn, plan: QP.QueryPlan, likes: Sequence[int] = (), dislikes: Sequence[int] = (), k: int = 10,
         candidate_ids: Optional[Sequence[int]] = None, movies_db: Optional[str] = None,
         w: Optional[Tuple[float, float, float]] = None) -> List[Dict[str, object]]:
    """The k best films for this plan and this history. See the module text for the steps."""
    if "movie" not in plan.media_types:
        raise Unsupported("recommendation ranking covers films only")
    if not 1 <= int(k) <= 50:
        raise ValueError("k must be between 1 and 50")
    if list(plan.media_types) != ["movie"]:
        d = plan.model_dump()
        d["media_types"] = ["movie"]
        plan = QP.parse(d)
    import personal                                   # the site's own feature / match code (facts.db, read-only)
    likes, dislikes = [int(x) for x in likes], [int(x) for x in dislikes]
    w = tuple(w) if w is not None else weights()[0]
    nb, nb_source = neighbour_scores(likes, movies_db)
    cands = list(candidate_ids) if candidate_ids is not None else candidates(conn, plan, nb)
    ok = apply_limits(conn, plan, cands, rated=likes + dislikes)
    if not ok:
        return []
    has_history = bool(likes or dislikes)
    prof = personal.profile(likes, dislikes, conn) if has_history else {}
    match = {f: (personal.match(prof, f, conn) if prof else (0.0, [], [])) for f in ok}
    votes = _votes(conn, ok)
    raw = {"c": {f: match[f][0] for f in ok}, "n": {f: min(nb.get(f, 0.0), NB_CAP) for f in ok},
           "p": {f: math.log1p(votes.get(f, 0)) for f in ok}}
    s = {key: minmax(v) for key, v in raw.items()}
    parts = {f: {"c": w[0] * s["c"][f], "n": w[1] * s["n"][f], "p": w[2] * s["p"][f]} for f in ok}
    score = {f: sum(parts[f].values()) for f in ok}
    if not any(score.values()):                       # no signal at all (new person, weights without popularity)
        score = dict(s["p"])
    genres = _genres(conn, ok)
    top = mmr(score, genres, int(k))
    out = []
    for i, f in enumerate(top):
        out.append({"media_id": "movie:%d" % f, "rank": i + 1, "score": round(float(score[f]), 4),
                    "reason": reason_for(f, parts[f], match[f][1], nb_source, has_history),
                    "signals": {"content": round(s["c"][f], 3), "neighbours": round(s["n"][f], 3), "popularity": round(s["p"][f], 3)},
                    "ranking_version": VERSION})
    return out
