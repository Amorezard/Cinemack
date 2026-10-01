"""
Retrieval for movie-buff.

Same public interface as before - retrieve(question, k=3, db_path="movies.db")
returns a list of dicts carrying "title", "text" and "distance", so chat.py and
ask.py keep working unchanged. The dicts now also carry id, release_year,
genres, director and top_cast, which the eval harness grades against.

Four changes over the plain vector search, each aimed at a specific failure
observed in results/baseline-q8_0.json:

  1. RELEVANCE CUTOFF      q12 retrieved Nothing (2003) / All or Nothing (2002)
                           at distances of 0.93-0.94 while genuine matches sit
                           well below that. Anything past MAX_DISTANCE is
                           dropped rather than handed to the model as context.

  2. METADATA PRE-FILTER   q09 asked for horror after 2015 and got three films
                           FROM 2015. An embedding cannot represent "after";
                           a WHERE clause can. Years and genres are parsed out
                           of the question and applied as real SQL filters.

  3. RECOMMENDATION POOL   "something like Inception" returned Inception, its
                           own tie-in motion comic, and one 6.2-rated film -
                           the whole candidate pool. Recommendation-shaped
                           questions now search wider and exclude the subject
                           film and its tie-ins from its own suggestion list.

  4. TITLE MATCHING        q12's title is built from common words ("everything",
                           "all", "once") and the embedding drowned in them. A
                           direct title lookup runs alongside the vector search
                           and wins when it hits.

Tunables are at the top. MAX_DISTANCE is the one worth revisiting first: raise
it if the model starts saying "I don't have anything on that" too readily,
lower it if irrelevant films are still getting through.
"""

import re
import sqlite3
import unicodedata

import ollama
import sqlite_vec

# config.yaml (mbconfig.py): fills in any setting the shell did not set. A variable set in the
# shell always wins, so the run_eN.sh scripts behave exactly as before. MB_CONFIG=none skips it.
try:
    import mbconfig as _mbconfig
    _mbconfig.apply()
except ImportError:
    pass

import os as _os_m
EMBED_MODEL = _os_m.environ.get("EMBED_MODEL", "mxbai-embed-large")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Read off the baseline run: real matches came back well under this, while
# q12's three bogus rows all sat at 0.93+.
MAX_DISTANCE = 0.90

# How many rows a recommendation question gets to choose among.
RECOMMEND_K = 10

# Vector-search pool before filtering. Wider when a filter or a recommendation
# will discard most of it.
POOL_DEFAULT = 20
POOL_WIDE = 400

# For recommendations, take this many by relevance and then prefer the
# well-known ones among them. Ranking purely by vector distance surfaces
# obscure films: "a good horror movie after 2015" returned Krampus: The Devil
# Returns because it happened to sit 0.01 closer than Hereditary. "Good" is
# part of the question, so popularity has to count for something.
RERANK_POOL = 60

CURRENT_YEAR = 2026

GENRE_WORDS = {
    "sci-fi": "Science Fiction", "scifi": "Science Fiction",
    "science fiction": "Science Fiction", "sf": "Science Fiction",
    "horror": "Horror", "scary": "Horror", "frightening": "Horror",
    "comedy": "Comedy", "funny": "Comedy", "comedic": "Comedy",
    "romance": "Romance", "romantic": "Romance", "romcom": "Romance",
    "thriller": "Thriller", "suspense": "Thriller",
    "documentary": "Documentary", "docs": "Documentary",
    "animated": "Animation", "animation": "Animation", "cartoon": "Animation",
    "action": "Action", "drama": "Drama", "western": "Western",
    "war": "War", "fantasy": "Fantasy", "mystery": "Mystery",
    "crime": "Crime", "noir": "Crime",
    "family": "Family", "kids": "Family", "children's": "Family",
    "musical": "Music", "adventure": "Adventure",
    "historical": "History", "history": "History",
}

RECOMMEND_PATTERNS = [
    r"\brecommend", r"\bsuggest", r"\bsomething like\b", r"\bsimilar to\b",
    r"\bmovies like\b", r"\bfilms like\b", r"\bmore like\b",
    r"\bwhat should i (watch|see)\b", r"\bwhat to watch\b",
    r"\bin the mood for\b",
    # "what's A GOOD horror movie" is a recommendation request even though it
    # never says "recommend" - and the word "good" means popularity should
    # count, not just vector distance.
    r"\b(a|any|some|another) (really |very |pretty )?(good|great|decent|solid|fun|nice)\b",
    r"\bwhat'?s? a (good|great)\b", r"\bbest\b.*\b(movie|film)s?\b",
]

DECADES = {
    "1920s": (1920, 1929), "20s": (1920, 1929),
    "1930s": (1930, 1939), "30s": (1930, 1939),
    "1940s": (1940, 1949), "40s": (1940, 1949),
    "1950s": (1950, 1959), "50s": (1950, 1959),
    "1960s": (1960, 1969), "60s": (1960, 1969),
    "1970s": (1970, 1979), "70s": (1970, 1979),
    "1980s": (1980, 1989), "80s": (1980, 1989), "eighties": (1980, 1989),
    "1990s": (1990, 1999), "90s": (1990, 1999), "nineties": (1990, 1999),
    "2000s": (2000, 2009), "2010s": (2010, 2019), "2020s": (2020, 2029),
}

# A follow-up question carries its subject in a pronoun: "who directed it?",
# "what year did that come out?". Embedded on its own, "who directed it?"
# retrieves nothing useful - in the baseline it returned Adaptation. and
# Paycheck. The fix is to put the subject back into the query before embedding.
PRONOUN_RE = re.compile(
    r"\b(it|its|it's|that|this|those|these|them|they|he|she|him|her|hers|"
    r"the film|the movie|the one)\b", re.I)

# Follow-ups don't always use a pronoun. "How did the first one end?", "what
# about the sequel?", "is part 2 any good?" point back at the last answer just
# as clearly - and embedded on their own they retrieve whatever shares their
# words ("how the first one ended" -> How It Ends, 2018).
REFERENCE_RE = re.compile(
    r"\b(?:the\s+)?(?:first|second|third|fourth|last|latest|final|original|other)\s+"
    r"(?:one|part|installment|entry)s?\b"
    # "the first movie" - but not "the first movie ever made" / "by Nolan"
    r"|\bthe\s+(?:first|second|third|original|other)\s+(?:film|movie)\b"
    r"(?!\s+(?:ever|of|by|from|in|about|that|where|with|to|made)\b)"
    r"|\bthe\s+(?:original|sequel|prequel|remake|ending|end)\b"
    r"|\bthe\s+(?:first|second|third|last)\b(?=\s*(?:[?.!,]|$))"
    r"|\bpart\s+(?:one|two|three|four|ii|iii|iv|[1-6])\b", re.I)

# Which of the recent titles an ordinal points at. -1 = the last one.
ORDINAL_INDEX = {
    "first": 0, "original": 0, "one": 0, "1": 0,
    "second": 1, "other": 1, "sequel": 1, "two": 1, "ii": 1, "2": 1,
    "third": 2, "three": 2, "iii": 2, "3": 2,
    "fourth": 3, "four": 3, "iv": 3, "4": 3, "5": 4, "6": 5,
    "last": -1, "latest": -1, "final": -1,
}

_TITLE_INDEX = None  # lazily built: [(id, normalized_title, title, year)]

# Single-word titles that are ordinary English and would otherwise hijack any
# question containing that word. The database really does hold films called
# "#Horror", "Nothing" and "Up"; normalizing strips the punctuation that made
# them distinctive, so "a good horror movie" would match the film #Horror and
# inject it as the question's subject. Multi-word titles are safe - nobody
# writes "everything everywhere all at once" by accident.
TITLE_STOPWORDS = set(GENRE_WORDS) | {
    # interrogatives - the database really does contain Polanski's "What?"
    "what", "who", "when", "where", "why", "how", "which", "whom",
    "movie", "movies", "film", "films", "good", "best", "new", "old", "great",
    "nothing", "everything", "something", "anything", "up", "it", "her", "him",
    "them", "us", "we", "she", "he", "they", "one", "two", "time", "life",
    "love", "home", "man", "woman", "boy", "girl", "day", "night", "year",
    "world", "end", "start", "help", "go", "come", "after", "before", "next",
    "watch", "see", "like", "similar", "recommend", "suggest",
    "tell", "about", "know", "think", "want", "need", "please", "thanks",
    "yes", "no", "ok", "okay", "maybe", "sure", "more", "less", "some", "any",
    "this", "that", "these", "those", "here", "there", "now", "then", "again",
}


# ---------------------------------------------------------------- helpers

def normalize(s):
    """Casefold, strip accents and punctuation. Makes 'Schindlers List' match
    'Schindler's List', which is the apostrophe case from the test set."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.casefold()).strip()


def embed_query(text):
    result = ollama.embed(model=EMBED_MODEL, input=[QUERY_PREFIX + text])
    return result["embeddings"][0]


class _Conn(sqlite3.Connection):
    """A plain connection that can also remember whether wiki.db is attached."""


# v2 E9: VEC_MEM=1 (default since 30 Sep) searches in-memory copies of the vector tables
# (vecmem.py; build them with `python mb.py build-cache` or `python vecmem.py build`) instead of
# sqlite-vec. Same exact search - every vector is compared, same distances - in one numpy product
# per list. E9: identical passes and pools on tune and check, search 1.42 -> 0.19 s per question.
# Falls back to sqlite-vec (one warning) if the copies are missing or older than movies.db /
# wiki.db, so a fresh setup works before the cache is built, just slower. 0 = sqlite-vec only.
import os as _os_v
VEC_MEM = _os_v.environ.get("VEC_MEM", "1") == "1"
_VEC = {"store": None, "tried": False, "db": None}


def _vec_store():
    """The vecmem.Store, or None (VEC_MEM off, or the cache is unusable)."""
    if not VEC_MEM:
        return None
    if not _VEC["tried"]:
        _VEC["tried"] = True
        try:
            import vecmem
            _VEC["store"] = vecmem.Store(_VEC["db"] or "movies.db")
        except Exception as e:  # noqa: BLE001
            import sys as _sys_v
            _VEC["error"] = str(e)
            print(f"[retrieve] vector cache not used ({e}); searching with sqlite-vec instead "
                  f"(same results, slower). Build it with: python mb.py build-cache", file=_sys_v.stderr)
    return _VEC["store"]


def vec_status():
    """'numpy' when the in-memory cache is in use, else 'sqlite-vec (<why>)'. Loads it if needed."""
    if not VEC_MEM:
        return "sqlite-vec (VEC_MEM=0)"
    return "numpy" if _vec_store() is not None else f"sqlite-vec ({_VEC.get('error', 'cache unusable')})"


def _as_list(q_vec):
    """q_vec when it is a plain vector (not a serialized blob / JSON text)."""
    return None if isinstance(q_vec, (bytes, str)) else q_vec


def open_db(db_path):
    if _VEC["db"] is None:
        _VEC["db"] = db_path
    conn = sqlite3.connect(db_path, factory=_Conn)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _attach_wiki(conn, db_path)
    return conn


# ------------------------------------------------ v2: Wikipedia passages (28 Sep 2026)
# WIKI=1 (default): the Wikipedia lead + plot passages in wiki.db (built by
# wiki_link.py, wiki_chunks.py, wiki_embed.py) are searched alongside the TMDB
# record and plot-only vectors. A film's distance is its best passage, so a
# description that matches one scene of a long Wikipedia plot can find the film
# even when the 2-3 sentence TMDB overview never mentions that scene.
# vec_chunks stores cosine distance; it is converted to the L2 scale used
# everywhere else here (unit vectors: l2 = sqrt(2 * cosine distance)), so
# MAX_DISTANCE and GUESS_MAX_DISTANCE keep their meaning.
# WIKI_KW=1: keyword search over the Wikipedia text (wiki_fts.py) joins the
# fused lists for descriptions. WIKI=0 restores v1 exactly, for A/B runs.
import os as _os_w
import hashlib as _hashlib_w
import math as _math_w
WIKI = _os_w.environ.get("WIKI", "1") != "0"
WIKI_KW = _os_w.environ.get("WIKI_KW", "1") != "0"
WIKI_DB = _os_w.environ.get("WIKI_DB", "")          # default: wiki.db next to movies.db
WIKI_KINDS = tuple(k for k in _os_w.environ.get("WIKI_KINDS", "plot,lead").split(",") if k)
# Generated descriptions (wiki_enrich.py, kind 'question'). They read like the user's
# own question, so they also match on STYLE ("I saw this years ago... snowy town"),
# not only content. Merged into each film's best distance they flooded the pool with
# look-alikes (28 Sep). WIKI_Q=rrf (default): they form their own ranked list, one
# vote in the RRF fusion for descriptions. merge = best-distance merge (the old way,
# for comparison). off = not used. WIKI_QSTYLES: which styles are searched
# (short, scene, fuzzy, childhood); "fuzzy" is the most formulaic.
WIKI_Q = _os_w.environ.get("WIKI_Q", "rrf")
WIKI_QSTYLES = tuple(x for x in _os_w.environ.get("WIKI_QSTYLES", "short,scene,fuzzy,childhood").split(",") if x)
if WIKI_Q == "merge" and "question" not in WIKI_KINDS:
    WIKI_KINDS = WIKI_KINDS + ("question",)
TEXT_KINDS = ("plot", "lead")   # real Wikipedia text (shown to models); "question" = generated
# v2 E10: plot_s = the same plot text in smaller chunks (~80 words; wiki_small.py). When the
# searched kinds include it, the passage shown to the re-ranker and the answer comes from the
# searched real-text kinds, so a small chunk that matched is the one shown.
_TK = tuple(k for k in WIKI_KINDS if k != "question")
if _TK and set(_TK) != set(TEXT_KINDS):
    TEXT_KINDS = _TK
# RERANK_WIN: which RERANK_WIKI_CHARS of the matched passage the re-ranker reads.
# 0 = the first ones (E4-E9). kw = the run of whole sentences sharing the most content words
# with the question. emb = the run holding the sentence closest to the question vector
# (one batched embedding call per re-rank). A passage that fits is shown whole either way.
RERANK_WIN = _os_w.environ.get("RERANK_WIN", "0")
# RERANK_TEXT: what the re-ranker reads for each candidate. tmdb = the TMDB overview
# (v1); wiki = the Wikipedia passage closest to the question; both = the first 200
# characters of the overview, then that passage. RERANK_WIKI_CHARS caps the passage.
RERANK_TEXT = _os_w.environ.get("RERANK_TEXT", "wiki")   # E4: +6 TREC tune vs tmdb
RERANK_WIKI_CHARS = int(_os_w.environ.get("RERANK_WIKI_CHARS", "400"))
RERANK_MAX_CHARS = int(_os_w.environ.get("RERANK_MAX_CHARS", "26000"))   # ~6.5k of 8k tokens
# RERANK=auto: skip the LLM re-ranker when the fused search's #1 film is also in the top 3
# of at least RERANK_AGREE search channels (and, if RERANK_GAP is set, leads #2 by that
# much distance). Chosen with gate_sim.py against the Wikipedia re-ranker (E4 f_wiki50),
# 28 Sep: agree >= 1 and gap >= 0.05 skipped 29% of tune / 19% of check re-ranks with no
# lost question on either. (agree >= 1 with no gap, calibrated on the weaker TMDB
# re-ranker, lost 5 tune questions in E4.) RERANK_GAP=none turns the gap test off.
# RERANK_ENGINE: which re-ranker runs when one does.
#   llm       movie-buff reads the candidates (default; ~10 s for 50 films)
#   cross     a cross-encoder (sentence-transformers) scores question + passage pairs;
#             no text generation, well under a second on the GPU
#   cross_llm the cross-encoder keeps its top RERANK_CROSS_KEEP, then the LLM picks from those
RERANK_ENGINE = _os_w.environ.get("RERANK_ENGINE", "llm")
RERANK_CROSS_MODEL = _os_w.environ.get("RERANK_CROSS_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_CROSS_KEEP = int(_os_w.environ.get("RERANK_CROSS_KEEP", "10"))
RERANK_CROSS_CHARS = int(_os_w.environ.get("RERANK_CROSS_CHARS", "900"))
RERANK_CROSS_DEVICE = _os_w.environ.get("RERANK_CROSS_DEVICE", "")      # "" = cuda if available
# E9: for Qwen3-Reranker models (e.g. RERANK_CROSS_MODEL=tomaarsen/Qwen3-Reranker-0.6B-seq-cls)
RERANK_CROSS_INSTRUCT = _os_w.environ.get(
    "RERANK_CROSS_INSTRUCT",
    "Given someone's vague, possibly misremembered description of a film, judge whether this "
    "film's plot summary is the film they mean")
# RRF_WEIGHTS: weight of each search list in the fusion, e.g. "q=1,hyde=1,wq=0.5,kw=1,wkw=2"
# (q = question vectors incl. Wikipedia passages, hyde = guessed plot, wq = generated
# descriptions, kw = TMDB keywords, wkw = Wikipedia keywords). Unset = all 1 (plain RRF).
RRF_WEIGHTS = {k: float(v) for k, v in (x.split("=") for x in
               _os_w.environ.get("RRF_WEIGHTS", "").split(",") if "=" in x)}
RRF_K_ENV = int(_os_w.environ.get("RRF_K", "60"))
RERANK_AGREE = int(_os_w.environ.get("RERANK_AGREE", "1"))
# 29 Sep (gate re-sim on the current defaults): gap 0.03 keeps every pass (tune 74/115,
# check 18/33) and skips the re-ranker on 35/97 tune and 10/31 check questions (0.05: 30, 7).
RERANK_GAP = float(_os_w.environ.get("RERANK_GAP", "0.03")) if _os_w.environ.get("RERANK_GAP", "0.03") != "none" else None
WIKI_PER_FILM = int(_os_w.environ.get("WIKI_PER_FILM", "4"))   # passages fetched per film wanted: k = pool * this, per kind
_WIKI_STATE = {}         # path -> {"vec": bool, "fts": bool}
_WIKI_CACHE = {}         # one question's wiki KNN results, cleared per retrieve() call


def _attach_wiki(conn, db_path):
    if not WIKI:
        return
    path = WIKI_DB or _os_w.path.join(_os_w.path.dirname(_os_w.path.abspath(db_path)), "wiki.db")
    if not _os_w.path.exists(path):
        return
    try:
        conn.execute("ATTACH DATABASE ? AS wiki", (path,))
    except sqlite3.OperationalError:
        return
    if path not in _WIKI_STATE:
        names = {r[0] for r in conn.execute("SELECT name FROM wiki.sqlite_master")}
        _WIKI_STATE[path] = {"vec": {"vec_chunks", "chunks"} <= names, "fts": "wiki_fts" in names}
    conn._wiki = _WIKI_STATE[path]


def _wiki_has(conn, what):
    state = getattr(conn, "_wiki", None) if WIKI else None
    return bool(state and state.get(what))


def _vec_arg(q_vec):
    """sqlite-vec accepts a float32 blob or JSON text."""
    if isinstance(q_vec, (bytes, str)):
        return q_vec
    return sqlite_vec.serialize_float32([float(x) for x in q_vec])


def wiki_knn(conn, q_vec, pool):
    """[(film_id, l2 distance)] by best Wikipedia passage, closest first.
    [] when wiki.db is missing or WIKI=0."""
    if not _wiki_has(conn, "vec"):
        return []
    arg = _vec_arg(q_vec)
    key = _hashlib_w.sha1(arg if isinstance(arg, bytes) else arg.encode()).hexdigest()
    k = min(4096, max(50, pool * WIKI_PER_FILM))
    hit = _WIKI_CACHE.get(key)
    if hit and hit[0] >= k:
        best = hit[1]
    else:
        best = {}
        kbest, kmax = {}, {}     # per kind: film -> (l2, chunk_id) of its closest chunk; k-th distance
        for kind in WIKI_KINDS:
            vs = _vec_store()
            if vs is not None and vs.chunks is not None and _as_list(q_vec) is not None:
                rows = vs.knn_cos(kind, q_vec, k)
            else:
                rows = conn.execute("SELECT chunk_id, film_id, distance FROM wiki.vec_chunks "
                                    "WHERE embedding MATCH ? AND k = ? AND kind = ?",
                                    (arg, k, kind)).fetchall()
            kb = kbest[kind] = {}
            last = None
            for cid, fid, d in rows:
                l2 = _math_w.sqrt(2.0 * max(d, 0.0))
                last = l2
                if fid not in best or l2 < best[fid]:
                    best[fid] = l2
                if fid not in kb or l2 < kb[fid][0]:
                    kb[fid] = (l2, cid)
            # fewer than k rows = every chunk of this kind was returned
            kmax[kind] = last if len(rows) >= k else float("inf")
        _WIKI_CACHE[key] = (k, best, kbest, kmax)
    return sorted(best.items(), key=lambda x: x[1])[:pool]


def _film_best_chunk(conn, movie_id, arg, kinds, q_vec=None):
    """(l2 distance, chunk_id) of the film's closest chunk among `kinds`, or None."""
    best = None
    ids = [r[0] for r in conn.execute("SELECT chunk_id FROM wiki.chunks WHERE film_id = ? AND kind IN (%s)"
                                      % ",".join("?" * len(kinds)), (movie_id, *kinds))]
    vs = _vec_store() if q_vec is not None and _as_list(q_vec) is not None else None
    for cid in ids:       # point lookups; vec0 has no fast IN (...)
        if vs is not None and vs.chunks is not None:
            d = vs.dist_cos(cid, q_vec)
            row = (d,)
        else:
            row = conn.execute("SELECT vec_distance_cosine(embedding, ?) FROM wiki.vec_chunks "
                               "WHERE chunk_id = ?", (arg, cid)).fetchone()
        if row and row[0] is not None:
            l2 = _math_w.sqrt(2.0 * max(row[0], 0.0))
            if best is None or l2 < best[0]:
                best = (l2, cid)
    return best


# WIKI_EXACT=0 (default): a film's Wikipedia distance comes from the passage search
# already run for this question (wiki_knn's cache); a film with no passage among the
# top k there is treated as having no close passage (its TMDB distance is used).
# WIKI_EXACT=1 looks up every passage of every film (one query per passage) - about
# 3 s per TREC question with ~200 fused candidates, for a near-identical answer.
WIKI_EXACT = _os_w.environ.get("WIKI_EXACT", "0") == "1"


def wiki_distance(conn, movie_id, q_vec):
    """Best Wikipedia-passage distance (L2 scale) for one film, or None."""
    if not _wiki_has(conn, "vec"):
        return None
    arg = _vec_arg(q_vec)
    if not WIKI_EXACT:
        key = _hashlib_w.sha1(arg if isinstance(arg, bytes) else arg.encode()).hexdigest()
        hit = _WIKI_CACHE.get(key)
        if hit is not None:
            return hit[1].get(movie_id)
    b = _film_best_chunk(conn, movie_id, arg, WIKI_KINDS, q_vec)
    return b[0] if b else None


# v2 E8: WIKI_PASSAGE_FAST=1 (default). Picking each candidate's closest passage took one
# vector lookup per passage (_film_best_chunk): ~200 lookups for the re-ranker's 50 films,
# and the re-rank step still took 2.6-3.4 s per question with every model call cached
# (29 Sep gate runs). The search already fetched each kind's k closest passages
# (wiki_knn). If a film has a passage of a kind among them, its closest passage of that
# kind is among them too (anything closer would also be in the top k); a kind with none
# of its passages there is at least the k-th distance away. So when the closest found
# passage beats those bounds it is exactly the passage the lookups would pick - same
# result, no lookups. Otherwise it falls back to the lookups. 0 = always look up.
WIKI_PASSAGE_FAST = _os_w.environ.get("WIKI_PASSAGE_FAST", "1") != "0"
_PASSAGE_STATS = {"fast": 0, "lookup": 0, "ms": 0.0}


def _best_chunk_from_search(movie_id, arg):
    """(l2, chunk_id) of the film's closest TEXT_KINDS passage, from wiki_knn's results,
    when it is provably the closest; else None."""
    key = _hashlib_w.sha1(arg if isinstance(arg, bytes) else arg.encode()).hexdigest()
    hit = _WIKI_CACHE.get(key)
    if not hit or len(hit) < 4:
        return None
    kbest, kmax = hit[2], hit[3]
    if not all(k in kbest for k in TEXT_KINDS):
        return None
    found = [kbest[k][movie_id] for k in TEXT_KINDS if movie_id in kbest[k]]
    if not found:
        return None
    b = min(found)
    bound = min((kmax[k] for k in TEXT_KINDS if movie_id not in kbest[k]), default=float("inf"))
    return b if b[0] <= bound else None


def wiki_passage(conn, movie_id, q_vec):
    """The film's real Wikipedia passage (intro or plot) closest to the question:
    (l2 distance, text, section, position, n_parts), or None."""
    if not _wiki_has(conn, "vec"):
        return None
    import time as _tp
    t0 = _tp.perf_counter()
    arg = _vec_arg(q_vec)
    b = _best_chunk_from_search(movie_id, arg) if WIKI_PASSAGE_FAST else None
    if b is not None:
        _PASSAGE_STATS["fast"] += 1
    else:
        _PASSAGE_STATS["lookup"] += 1
        b = _film_best_chunk(conn, movie_id, arg, TEXT_KINDS, q_vec)
    _PASSAGE_STATS["ms"] += (_tp.perf_counter() - t0) * 1000
    if not b:
        return None
    row = conn.execute("SELECT text, section, position, n_parts FROM wiki.chunks WHERE chunk_id = ?",
                       (b[1],)).fetchone()
    return (b[0],) + tuple(row) if row else None


def wiki_intro(conn, movie_id):
    """First part of the film's Wikipedia intro, or ""."""
    if not _wiki_has(conn, "vec"):
        return ""
    row = conn.execute("SELECT text FROM wiki.chunks WHERE film_id = ? AND kind = 'lead' "
                       "ORDER BY position LIMIT 1", (movie_id,)).fetchone()
    return row[0] if row else ""


def _clip(text, n):
    """At most n characters, cut at a word boundary."""
    text = text or ""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + " ..."


_SENT_W = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9\"'(\[])")   # = wiki_common.split_sentences


def _stem_terms(text):
    out = set()
    for w in re.findall(r"[a-z0-9']+", (text or "").lower()):
        w = w.strip("'")
        if len(w) < 3 or w in _STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def _windows(sents, n):
    """[(start, end)] runs of whole sentences of at most n characters (one sentence alone if longer)."""
    spans = []
    for i in range(len(sents)):
        j, size = i, 0
        while j < len(sents) and (j == i or size + 1 + len(sents[j]) <= n):
            size += len(sents[j]) + (1 if j > i else 0)
            j += 1
        spans.append((i, j))
    return spans


def best_windows(passages, question, q_vec, n, mode="kw"):
    """For each passage (or None): the n-character excerpt the re-ranker should read, or None
    to keep the default (first n characters). Ties keep the earliest run, so a passage that
    shares nothing with the question reads exactly as before."""
    out = [None] * len(passages)
    split = []
    for i, p in enumerate(passages):
        if p and len(p) > n:
            sents = [x.strip() for x in _SENT_W.split(p) if x.strip()]
            if len(sents) > 1:
                split.append((i, sents))
    if not split:
        return out
    if mode == "emb" and _as_list(q_vec) is not None:
        uniq = list(dict.fromkeys(s for _, sents in split for s in sents))
        try:
            vecs = ollama.embed(model=EMBED_MODEL, input=uniq)["embeddings"]
            import numpy as _np_w
            q = _np_w.asarray(q_vec, dtype=_np_w.float32)
            q = q / (float(_np_w.linalg.norm(q)) or 1.0)
            m = _np_w.asarray(vecs, dtype=_np_w.float32)
            sims = m @ q / _np_w.maximum(_np_w.linalg.norm(m, axis=1), 1e-9)
            sim = dict(zip(uniq, (float(x) for x in sims)))
            score_of = lambda sents, a, b: (max(sim[s] for s in sents[a:b]),
                                            sum(sim[s] for s in sents[a:b]) / (b - a))
        except Exception:  # noqa: BLE001 - fall back to keywords
            mode = "kw"
    if mode != "emb" or _as_list(q_vec) is None:
        qt = _stem_terms(question)
        score_of = lambda sents, a, b: (len(qt & _stem_terms(" ".join(sents[a:b]))), 0)
    for i, sents in split:
        best, best_span = None, None
        for a, b in _windows(sents, n):
            sc = score_of(sents, a, b)
            if best is None or sc > best:
                best, best_span = sc, (a, b)
        a, b = best_span
        if a == 0:
            continue                      # the default already starts here
        text = " ".join(sents[a:b])
        out[i] = "... " + _clip(text, n) + ("" if b == len(sents) else " ...")
    return out


def wiki_question_ranking(conn, q_vec, n=100):
    """Film ids by their closest generated description (WIKI_Q=rrf), best first,
    only the styles in WIKI_QSTYLES. [] when there are none or WIKI_Q is not rrf."""
    if WIKI_Q != "rrf" or not _wiki_has(conn, "vec"):
        return []
    k = min(4096, max(50, n * 4))
    vs = _vec_store()
    if vs is not None and vs.chunks is not None and _as_list(q_vec) is not None:
        rows = vs.knn_cos("question", q_vec, k)
    else:
        rows = conn.execute("SELECT chunk_id, film_id, distance FROM wiki.vec_chunks "
                            "WHERE embedding MATCH ? AND k = ? AND kind = 'question'",
                            (_vec_arg(q_vec), k)).fetchall()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    style = dict(conn.execute("SELECT chunk_id, section FROM wiki.chunks WHERE chunk_id IN (%s)"
                              % ",".join("?" * len(ids)), ids).fetchall())
    out, seen = [], set()
    for cid, fid, _d in rows:                       # already closest first
        if fid in seen or style.get(cid) not in WIKI_QSTYLES:
            continue
        seen.add(fid)
        out.append(fid)
        if len(out) >= n:
            break
    return out


def wiki_keyword_ranking(conn, text, n=100):
    """Film ids by FTS5 keyword match over Wikipedia lead + plot, best first."""
    if not (WIKI_KW and _wiki_has(conn, "fts")):
        return []
    match = _fts_query(text)
    if not match:
        return []
    try:
        return [r[0] for r in conn.execute(
            "SELECT rowid FROM wiki.wiki_fts WHERE wiki_fts MATCH ? "
            "ORDER BY bm25(wiki_fts, 3.0, 1.0) LIMIT ?", (match, n))]
    except sqlite3.OperationalError:
        return []


def is_recommendation(question):
    q = question.casefold()
    return any(re.search(p, q) for p in RECOMMEND_PATTERNS)


def parse_filters(question):
    """Pull year ranges and genres out of the question text.

    This is the piece q09 needed: 'horror after 2015' has to become a real
    year comparison, because no embedding encodes 'after'."""
    q = question.casefold()
    f = {}

    # Explicit comparisons first - they are unambiguous and beat a bare year.
    m = re.search(r"\b(?:after|since|newer than|past)\s+(\d{4})\b", q)
    if m:
        f["year_min"], f["year_max"] = int(m.group(1)) + 1, CURRENT_YEAR
    if not f.get("year_min"):
        m = re.search(r"\b(?:before|older than|earlier than|pre-?)\s*(\d{4})\b", q)
        if m:
            f["year_min"], f["year_max"] = 1870, int(m.group(1)) - 1
    if not f.get("year_min"):
        m = re.search(r"\bbetween\s+(\d{4})\s+and\s+(\d{4})\b", q)
        if m:
            f["year_min"], f["year_max"] = int(m.group(1)), int(m.group(2))

    # Decades: "from the 80s", "1990s sci-fi".
    if not f.get("year_min"):
        for word, (lo, hi) in DECADES.items():
            if re.search(rf"\b{re.escape(word)}\b", q):
                f["year_min"], f["year_max"] = lo, hi
                break

    # A bare four-digit year: "the 2021 version of Dune", "movies from 1994".
    if not f.get("year_min"):
        m = re.search(r"\b(19[2-9]\d|20[0-2]\d)\b", q)
        if m:
            y = int(m.group(1))
            f["year_min"], f["year_max"] = y, y

    genres = []
    for word, genre in GENRE_WORDS.items():
        if genre not in genres and genre_is_a_filter(q, word):
            genres.append(genre)
    if genres:
        f["genres"] = genres

    return f


def genre_is_a_filter(q, word):
    """True only when a genre word is being used to describe the KIND of film
    wanted, rather than appearing incidentally in a plot description.

    "a good horror movie"                 -> Horror filter
    "recommend some sci-fi"               -> Science Fiction filter
    "a poor FAMILY hired by a household"  -> no filter; that's the plot

    Genre names are ordinary English nouns - family, war, music, action,
    crime, romance, history - so matching them anywhere in the question turns
    half of all plot descriptions into spurious filters."""
    tokens = re.findall(r"[a-z0-9'+-]+", q)
    wtoks = word.split()
    n = len(wtoks)
    nouns = {"movie", "movies", "film", "films", "flick", "flicks",
             "something", "anything", "one", "ones", "pick", "picks"}
    qualifiers = {"good", "great", "best", "decent", "solid", "fun", "nice",
                  "some", "any", "another", "classic", "favourite", "favorite"}

    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] != wtoks:
            continue
        after = tokens[i + n:i + n + 2]
        before = tokens[max(0, i - 2):i]
        # "horror movie", "sci-fi films", "horror or thriller movie"
        if any(t in nouns for t in after):
            return True
        # "movies that are horror", "something horror-ish"
        if any(t in nouns for t in before):
            return True
        # "a good horror", "recommend some sci-fi"
        if any(t in qualifiers for t in before):
            return True
    return False


def title_index(conn):
    """All titles, normalized, cached for the process. ~30k rows, built once."""
    global _TITLE_INDEX
    if _TITLE_INDEX is None:
        _TITLE_INDEX = [
            (mid, normalize(t), t, y)
            for mid, t, y in conn.execute(
                "SELECT id, title, release_year FROM movies WHERE title IS NOT NULL"
            )
        ]
    return _TITLE_INDEX


def titles_in(question, conn, min_len=4):
    """Database titles that appear in the question, longest first.

    Longest-first matters: 'Everything Everywhere All at Once' must win over
    'Everything', and 'Inception: The Cobol Job' over 'Inception'."""
    nq = f" {normalize(question)} "
    found = []
    for mid, ntitle, title, year in title_index(conn):
        if len(ntitle) < min_len:
            continue
        # A one-word title that is also an ordinary word is not evidence that
        # the question is about that film. Multi-word titles are kept.
        if " " not in ntitle and ntitle in TITLE_STOPWORDS:
            continue
        # One-word titles, with or without an article ("Police", "The One",
        # "Whiplash"), only count when typed like a title. "the Boston police"
        # and "the one where..." are phrases, not film names. People who type
        # everything in lowercase still match after a cue: "about whiplash".
        words = ntitle.split(" ")
        core = words[1:] if len(words) > 1 and words[0] in ("the", "a", "an") else words
        if len(core) == 1 and title not in question:
            import re
            lazy = question == question.lower()
            cue = re.search(r"\b(about|watch|watched|seen|saw|called|named)\s+(the\s+)?"
                            + re.escape(core[0]) + r"\W*$", normalize(question))
            if not (lazy and cue):
                continue
        if f" {ntitle} " in nq:
            found.append({"id": mid, "title": title, "release_year": year,
                          "n": len(ntitle)})
    found.sort(key=lambda r: -r["n"])
    return found


def knn(conn, q_vec, pool):
    """Nearest neighbours by vector distance. Split out so it can be stubbed
    in tests without a live sqlite-vec extension."""
    vs = _vec_store()
    if vs is not None and vs.has("movie_vectors") and _as_list(q_vec) is not None:
        return vs.knn_l2("movie_vectors", q_vec, pool)
    return conn.execute("""
        SELECT movie_id, distance
        FROM movie_vectors
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
    """, (sqlite_vec.serialize_float32(q_vec), pool)).fetchall()


def hydrate(conn, ids_with_distance):
    out = []
    for mid, distance in ids_with_distance:
        row = conn.execute(
            "SELECT id, title, release_year, genres, director, top_cast, "
            "embed_text, vote_count FROM movies WHERE id = ?", (mid,)
        ).fetchone()
        if row:
            out.append({
                "id": row[0], "title": row[1], "release_year": row[2],
                "genres": row[3], "director": row[4], "top_cast": row[5],
                "text": row[6], "vote_count": row[7] or 0,
                "distance": distance,
            })
    return out


def passes(hit, filters):
    lo, hi = filters.get("year_min"), filters.get("year_max")
    if lo is not None:
        try:
            year = int(hit["release_year"])
        except (TypeError, ValueError):
            return False
        if not (lo <= year <= hi):
            return False
    wanted = filters.get("genres")
    if wanted:
        have = (hit["genres"] or "").casefold()
        if not any(g.casefold() in have for g in wanted):
            return False
    return True


def backfill(conn, filters, need, exclude_ids):
    """When the filter throws out nearly everything the vector search found,
    fall back to the most-voted-on films that actually match the filter.

    This is what turns 'horror after 2015' from three 2015 films into three
    real, popular, post-2015 horror films."""
    lo = filters.get("year_min", 0)
    hi = filters.get("year_max", 9999)
    sql = ("SELECT id, title, release_year, genres, director, top_cast, "
           "embed_text, vote_count FROM movies "
           "WHERE CAST(release_year AS INTEGER) BETWEEN ? AND ?")
    params = [lo, hi]
    for g in filters.get("genres", []):
        sql += " AND genres LIKE ?"
        params.append(f"%{g}%")
    sql += " ORDER BY vote_count DESC LIMIT ?"
    params.append(need + len(exclude_ids) + 5)

    out = []
    for row in conn.execute(sql, params):
        if row[0] in exclude_ids:
            continue
        out.append({
            "id": row[0], "title": row[1], "release_year": row[2],
            "genres": row[3], "director": row[4], "top_cast": row[5],
            "text": row[6], "vote_count": row[7] or 0,
            "distance": None,  # ranked by popularity, not vector distance
        })
        if len(out) >= need:
            break
    return out


# ---------------------------------------------------------------- main entry

def resolve_followup(question, recent_titles, conn):
    """Put the conversation's subject back into a pronoun-only question.

    "Who directed it?" embeds to nothing useful on its own. Rewritten as
    "Who directed it? (The Departed)" it retrieves the right film, and the
    title matcher then pins it exactly.

    Only fires when the question actually looks like a follow-up: it carries a
    pronoun, and it does not already name a film of its own."""
    if not recent_titles:
        return question, None
    if not (PRONOUN_RE.search(question) or REFERENCE_RE.search(question)):
        return question, None
    # Strip the reference phrase before looking for a named film, so a film
    # literally called "The Sequel" or "The End" can't claim the question.
    if titles_in(REFERENCE_RE.sub(" ", question), conn):
        return question, None  # names its own subject; leave it alone
    subject = pick_subject(question, recent_titles, conn)
    return f"{question.strip()} ({subject})", subject


def pick_subject(question, recent_titles, conn):
    """Which recent title a follow-up means. No ordinal -> the first one, as
    before. "the first one" / "the sequel" / "part 3" -> by position."""
    m = REFERENCE_RE.search(question)
    idx = None
    if m:
        for tok in re.findall(r"[a-z0-9]+", m.group(0).casefold()):
            if tok in ORDINAL_INDEX:
                idx = ORDINAL_INDEX[tok]
                break
    if idx is None:
        return recent_titles[0]
    ordered = franchise_order(recent_titles, conn) or list(recent_titles)
    try:
        return ordered[idx]
    except IndexError:
        return recent_titles[0]


def franchise_order(titles, conn):
    """If the recent titles are one series (Back to the Future, Part II,
    Part III), return that series oldest first; otherwise None."""
    norms = [normalize(t) for t in titles]
    root = min(norms, key=len)
    series = [t for t, n in zip(titles, norms) if n.startswith(root)]
    if len(series) < 2:
        return None
    years = {}
    for _mid, _nt, title, year in title_index(conn):
        if title in series:
            try:
                years[title] = min(years.get(title, 9999), int(year))
            except (TypeError, ValueError):
                pass
    return sorted(series, key=lambda t: years.get(t, 9999))


def recent_titles_from(hits, answer=None):
    """Titles for the next turn's follow-up resolution, in the order the answer
    mentioned them, then any it didn't mention. After "Recommend some 80s
    sci-fi", "the second one" means the second film the answer talked about -
    not the second retrieval hit, which is ordered by popularity."""
    titles = []
    for h in hits:
        if h.get("title") and h["title"] not in titles:
            titles.append(h["title"])
    if not answer:
        return titles
    text = f" {normalize(answer)} "
    pos = {}
    for t in titles:
        i = text.find(f" {normalize(t)} ")
        if i >= 0:
            pos[t] = i
    mentioned = sorted(pos, key=pos.get)
    return mentioned + [t for t in titles if t not in pos]


# ---------------------------------------------------------- title guessing
import json as _json
import re as _re

# "A movie about...", "There's a film where...", "What's that movie..."
DESCRIBES_A_FILM = _re.compile(
    r"\b(movie|film)\b.{0,40}?\b(where|about|in which|that|with|who)\b"
    r"|\bthere'?s a (movie|film)\b|\bwhat'?s (that|the) (movie|film)\b", _re.I)
# "the 1987 film The Cobalt Hour": the user already named a film. If it is not
# in the database, that is the honesty test's job, not something to guess past.
NAMES_A_FILM = _re.compile(r"\b(?:film|movie)\s+(?:The |A |An )?[A-Z][\w'’:-]*")
GUESS_MODEL = _os_m.environ.get("ANSWER_MODEL", "movie-buff")   # guesses + the LLM re-ranker
GUESS_MAX_DISTANCE = 0.87   # 0.90 let a wrong guess (The Parent Trap, 0.893) through


def guess_titles(question, n=3):
    """Up to n candidate films for a description, most likely first."""
    try:
        import ollama
        r = ollama.chat(
            model=GUESS_MODEL, think=False,
            options=llm_options({"num_predict": 60, "temperature": 0}),
            messages=[
                {"role": "system", "content":
                    f"You identify films from descriptions. List the {n} most likely "
                    "films, one per line, as: Title (Year). No other text. "
                    "If you have no idea, reply exactly: UNKNOWN"},
                {"role": "user", "content": question},
            ])
        text = (r["message"]["content"] or "").strip()
    except Exception:
        return []
    out = []
    for m in _re.finditer(r"([^\n()]+?)\s*\((\d{4})\)", text):
        title = _re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", m.group(1)).strip(' "*')
        if title:
            out.append({"title": title, "year": m.group(2)})
    return out[:n]


def lookup_guess(conn, guess):
    """The guessed film's row, only if that exact title is in the database."""
    nt = normalize(guess["title"])
    rows = [(mid, t, y) for mid, n, t, y in title_index(conn) if n == nt]
    if guess["year"]:
        rows = [r for r in rows if str(r[2] or "").startswith(guess["year"])] or rows
    if not rows:
        return None
    def votes(r):
        v = conn.execute("SELECT vote_count FROM movies WHERE id = ?", (r[0],)).fetchone()
        return (v[0] or 0) if v else 0
    mid, t, y = max(rows, key=votes)
    return {"id": mid, "title": t, "release_year": y, "n": len(nt)}


def distance_to(conn, movie_id, q_vec):
    """The same L2 distance the KNN search reports, for one specific film."""
    vs = _vec_store()
    if vs is not None and vs.has("movie_vectors") and _as_list(q_vec) is not None:
        d = vs.dist_l2("movie_vectors", movie_id, q_vec)
    else:
        v = q_vec if isinstance(q_vec, (bytes, str)) else _json.dumps([float(x) for x in q_vec])
        row = conn.execute(
            "SELECT vec_distance_l2(embedding, ?) FROM movie_vectors WHERE movie_id = ?",
            (v, movie_id)).fetchone()
        d = row[0] if row else None
    w = wiki_distance(conn, movie_id, q_vec)      # v2: best Wikipedia passage
    if w is not None and (d is None or w < d):
        d = w
    return d


EXTRA_VECTOR_TABLES = ("movie_plot_vectors",)


def knn_multi(conn, q_vec, pool):
    """Full-record KNN plus plot-only KNN, best distance per film. Cast lists
    and keywords dilute the full record (Jaws: 0.785 full, 0.734 plot-only)."""
    best = dict(knn(conn, q_vec, pool))
    v = q_vec if isinstance(q_vec, (bytes, str)) else _json.dumps([float(x) for x in q_vec])
    vs = _vec_store()
    for table in EXTRA_VECTOR_TABLES:
        try:
            if vs is not None and vs.has(table) and _as_list(q_vec) is not None:
                rows = vs.knn_l2(table, q_vec, pool)
            else:
                rows = conn.execute(f"SELECT movie_id, distance FROM {table} "
                                    "WHERE embedding MATCH ? AND k = ?", (v, pool)).fetchall()
        except Exception:
            continue   # index not built yet
        for mid, dist in rows:
            if mid not in best or dist < best[mid]:
                best[mid] = dist
    for mid, dist in wiki_knn(conn, q_vec, pool):    # v2: Wikipedia passages
        if mid not in best or dist < best[mid]:
            best[mid] = dist
    return sorted(best.items(), key=lambda x: x[1])[:pool]


RERANK_POOL = 30          # Parasite sat at rank 28 for q07
RERANK_PLOT_CHARS = 220


# The re-ranker once got the whole 400-film pool whenever a description also
# named a genre or year ("an ANIMATED movie where..."): ~100k characters, with
# the closest films listed last. It picked nothing on all 4 such questions
# (q28, q29, q38, q40; results/stage_scores_2026-09-23.json). Now it sees at
# most RERANK_POOL films: accepted guesses first, then the closest by distance,
# and only films that pass the question's year/genre filter.
# RERANK_CAP=0 restores the old behaviour for A/B runs.
import os as _os_r
RERANK_CAP = _os_r.environ.get("RERANK_CAP", "1") != "0"

# Descriptions: a year or genre is a hint, not a rule. On the TREC dev set the
# hard filter threw away the right film on 21 of 84 questions (often ranked #1-5
# by the search): people misremember release years ("early 2000s I believe" for
# a 1995 film) or give the year the story is set in ("1940" for a 2007 film).
# Now, for description questions, films that fit the year (+/- SOFT_YEAR_SLACK)
# and genre just move up the re-ranker's list, and nothing is filtered out; the
# re-ranker sees each film's year next to the question and can weigh it.
# SOFT_FILTERS=0 restores the hard filter for A/B runs.
SOFT_FILTERS = _os_r.environ.get("SOFT_FILTERS", "1") != "0"
SOFT_YEAR_SLACK = 3
SOFT_BONUS = 0.03        # distance credit for fitting the hint (distances are ~0.6-0.9)
# Of the re-ranker's RERANK_POOL slots, this many go to the closest films that
# fit the hint; the rest to the closest films of any year. With the bonus alone
# (SOFT_FIT_SLOTS=0), non-fitting films crowded out right-era ones that the old
# hard filter had made room for (tune set: +7 questions, -3; 23 Sep).
SOFT_FIT_SLOTS = int(_os_r.environ.get("SOFT_FIT_SLOTS", "20"))


def fits_hint(year, genres, filters):
    """passes(), with the year range widened by SOFT_YEAR_SLACK."""
    lo, hi = filters.get("year_min"), filters.get("year_max")
    wide = dict(filters)
    if lo is not None:
        wide["year_min"], wide["year_max"] = lo - SOFT_YEAR_SLACK, hi + SOFT_YEAR_SLACK
    return passes({"release_year": year, "genres": genres}, wide)


# ------------------------------------------------ search batch, 23 Sep 2026
# Each change has its own switch so one overnight run can measure them together
# and one at a time (run_overnight.sh). 1 = on (default), 0 = off.
#   UNKNOWN_FILM  change 9:  a film the question names that isn't in movies.db
#                            -> return nothing (not three nearby films)
#   MULTI_FILM    change 13: a question naming 2-3 films gets all of them
#   HYDE          change 10: for descriptions the plan also writes a guessed
#                            TMDB-style plot; it is searched too, and the
#                            re-ranker gets a second look if it picks nothing
#   HYBRID        change 11: for descriptions, keyword search (SQLite FTS5,
#                            build_fts.py) is merged with vector search by
#                            reciprocal rank fusion
#   RERANK_N      how many candidates the re-ranker reads (was 30)
# With HYDE or HYBRID on, a film the re-ranker picked is kept even if its
# question-to-plot distance is over the 0.90 cutoff (it was found another way).
UNKNOWN_FILM = _os_r.environ.get("UNKNOWN_FILM", "1") != "0"
MULTI_FILM = _os_r.environ.get("MULTI_FILM", "1") != "0"
HYDE = _os_r.environ.get("HYDE", "1") != "0"
HYBRID = _os_r.environ.get("HYBRID", "1") != "0"
RERANK_N = int(_os_r.environ.get("RERANK_N", "50"))
# RERANK_CHARS: how much of each plot the re-ranker reads (was 220; TMDB plots
# are usually 300-500 characters, so the remembered detail was often cut off).
# GUESS_WIDE: every title the planner guessed that exists in movies.db is shown
# to the re-ranker first, even when its distance to the (long, rambling)
# question is over GUESS_MAX_DISTANCE; the re-ranker reads its plot and decides.
RERANK_CHARS = int(_os_r.environ.get("RERANK_CHARS", "400"))
GUESS_WIDE = _os_r.environ.get("GUESS_WIDE", "1") != "0"
# v2 E7: RERANK_SECOND=0 stops the second re-rank call (the next 50 films down the
# list) when the first call picks nothing. Each call reads ~6k tokens, so on hard
# descriptions the second look can double the re-rank time.
# E8 (29 Sep): 0 = default. Tune and check passes identical with and without it; it only
# ever doubled the re-rank time (~10 s) on the rare question where the first call picked nothing.
RERANK_SECOND = _os_r.environ.get("RERANK_SECOND", "0") != "0"
# v2 E8: NUM_BATCH = how many prompt tokens Ollama reads per step (Ollama's default is 512).
# A bigger batch reads long prompts (the re-ranker's ~6k tokens, the planner's ~1.5k)
# faster on the GPU; what the model writes should not change. Every movie-buff call must
# use the SAME value - Ollama reloads the model whenever num_batch changes between calls -
# so chat.py and web/pipeline.py take their options from llm_options() too.
# 0 (default) = Ollama's default.
NUM_BATCH = int(_os_r.environ.get("NUM_BATCH", "0"))


def llm_options(opts=None):
    """Ollama options for a chat call, plus num_batch when NUM_BATCH is set."""
    out = dict(opts or {})
    if NUM_BATCH:
        out["num_batch"] = NUM_BATCH
    return out


# v2 E8: where each model call's time goes, from Ollama's own counters (prompt reading
# vs. writing, in ms and tokens), plus the re-ranker's listing build (Wikipedia passage
# lookups for ~50 films). Summed per retrieve() call and copied into trace["llm"].
# Replayed (cached) calls carry no counters, so only --no-cache runs fill it.
_LLM_STATS = {}


def _note_llm(name, r=None, build_ms=None):
    try:
        st = _LLM_STATS.setdefault(name, {"calls": 0})
        if build_ms is not None:
            st["build_ms"] = st.get("build_ms", 0) + round(build_ms)
            return
        get = r.get if hasattr(r, "get") else (lambda k, d=None: getattr(r, k, d))
        vals = {"prompt_tokens": get("prompt_eval_count"), "prompt_ms": get("prompt_eval_duration"),
                "out_tokens": get("eval_count"), "out_ms": get("eval_duration"),
                "load_ms": get("load_duration")}
        if all(v is None for v in vals.values()):
            return
        st["calls"] += 1
        for k, v in vals.items():
            if v is not None:
                v = v / 1e6 if k.endswith("_ms") else v      # Ollama reports nanoseconds
                st[k] = round(st.get(k, 0) + v)
    except Exception:       # timing must never break a search
        pass


DESCRIBE_POOL = 100         # vector pool per list for descriptions when HYDE/HYBRID
RRF_K = 60
SWITCHES = {"RERANK_CAP": RERANK_CAP, "SOFT_FILTERS": SOFT_FILTERS,
            "SOFT_FIT_SLOTS": SOFT_FIT_SLOTS, "UNKNOWN_FILM": UNKNOWN_FILM,
            "MULTI_FILM": MULTI_FILM, "HYDE": HYDE, "HYBRID": HYBRID, "RERANK_N": RERANK_N,
            "RERANK_CHARS": RERANK_CHARS, "GUESS_WIDE": GUESS_WIDE,
            "WIKI": WIKI, "WIKI_KW": WIKI_KW, "WIKI_KINDS": ",".join(WIKI_KINDS),
            "WIKI_Q": WIKI_Q, "WIKI_QSTYLES": ",".join(WIKI_QSTYLES), "WIKI_EXACT": WIKI_EXACT, "RERANK": _os_w.environ.get("RERANK", "auto"),
            "RERANK_AGREE": RERANK_AGREE, "RERANK_GAP": RERANK_GAP, "RERANK_ENGINE": RERANK_ENGINE,
            "RRF_WEIGHTS": RRF_WEIGHTS, "RRF_K": RRF_K_ENV,
            "RERANK_TEXT": RERANK_TEXT, "RERANK_WIKI_CHARS": RERANK_WIKI_CHARS,
            "RERANK_SECOND": RERANK_SECOND, "NUM_BATCH": NUM_BATCH,
            "WIKI_PASSAGE_FAST": WIKI_PASSAGE_FAST, "VEC_MEM": VEC_MEM,
            "RERANK_WIN": RERANK_WIN, "WIKI_PER_FILM": WIKI_PER_FILM}

_STOP = set("""a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing down during each few for
from further had has have having he her here hers herself him himself his how i if in into is
it its itself just me more most my myself no nor not now of off on once only or other our ours
out over own same she should so some such than that the their theirs them then there these they
this those through to too under until up very was we were what when where which while who whom
why will with would you your yours yourself movie movies film films think remember remembered
watched watch saw seen scene scenes really like kind sort something anything thing things one
also maybe probably pretty get got know dont didnt cant think believe years year ago old
""".split())
_FTS_OK = None


def _fts_query(text):
    """The question's content words as an FTS5 OR query ("" if none)."""
    words = []
    for w in _re.findall(r"[a-z0-9]+", normalize(text)):
        if len(w) >= 3 and w not in _STOP and w not in words:
            words.append(w)
    return " OR ".join(f'"{w}"' for w in words[:40])


def keyword_ranking(conn, text, n=100):
    """Film ids by SQLite FTS5 keyword match (bm25), best first. [] if the
    movies_fts index hasn't been built (python build_fts.py)."""
    global _FTS_OK
    if _FTS_OK is None:
        _FTS_OK = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'movies_fts'").fetchone())
    if not _FTS_OK:
        return []
    match = _fts_query(text)
    if not match:
        return []
    try:
        return [r[0] for r in conn.execute(
            "SELECT rowid FROM movies_fts WHERE movies_fts MATCH ? "
            "ORDER BY bm25(movies_fts, 3.0, 1.0) LIMIT ?", (match, n))]
    except sqlite3.OperationalError:
        return []


def embed_document(text):
    """A guessed plot is embedded like a film record (no query prefix)."""
    import ollama
    return ollama.embed(model=EMBED_MODEL, input=[text])["embeddings"][0]


_CROSS = None


def cross_scores(question, docs):
    """Cross-encoder relevance scores for (question, doc) pairs; higher = better.
    Loaded once per process. Falls back to CPU if the GPU is out of memory."""
    global _CROSS
    import torch
    if _CROSS is None:
        from sentence_transformers import CrossEncoder
        dev = RERANK_CROSS_DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
        try:
            _CROSS = CrossEncoder(RERANK_CROSS_MODEL, max_length=1024, device=dev)
            if dev == "cuda":
                _CROSS.model.half()
        except RuntimeError as e:      # GPU full (movie-buff is ~19 GB of 23): use the CPU
            if "out of memory" not in str(e).lower():
                raise
            torch.cuda.empty_cache()
            _CROSS = CrossEncoder(RERANK_CROSS_MODEL, max_length=1024, device="cpu")
    pairs = [(question, d) for d in docs]
    if "qwen3-reranker" in RERANK_CROSS_MODEL.lower():
        # Qwen3-Reranker is an instruction-following yes/no judge; it expects its chat
        # template around the query and the document (as on the model card).
        pre = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the "
               "Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\"."
               "<|im_end|>\n<|im_start|>user\n")
        suf = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        pairs = [(f"{pre}<Instruct>: {RERANK_CROSS_INSTRUCT}\n<Query>: {question}\n",
                  f"<Document>: {d}{suf}") for d in docs]
    try:
        out = _CROSS.predict(pairs, batch_size=8, show_progress_bar=False)
    except RuntimeError as e:          # CUDA out of memory with movie-buff loaded: use the CPU
        if "out of memory" not in str(e).lower():
            raise
        from sentence_transformers import CrossEncoder
        del _CROSS
        torch.cuda.empty_cache()
        _CROSS = CrossEncoder(RERANK_CROSS_MODEL, max_length=1024, device="cpu")
        out = _CROSS.predict(pairs, batch_size=8, show_progress_bar=False)
    return [float(x) for x in out]


def rrf_named(named):
    """Weighted reciprocal rank fusion over [(name, ranking)] (RRF_WEIGHTS, RRF_K)."""
    score = {}
    for name, ranking in named:
        w = RRF_WEIGHTS.get(name, 1.0)
        if not w:
            continue
        for rank, mid in enumerate(ranking, 1):
            score[mid] = score.get(mid, 0.0) + w / (RRF_K_ENV + rank)
    return sorted(score, key=lambda m: -score[m])


def rrf(*rankings):
    """Reciprocal rank fusion: each list adds 1/(RRF_K + rank) per film."""
    score = {}
    for ranking in rankings:
        for rank, mid in enumerate(ranking, 1):
            score[mid] = score.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(score, key=lambda m: -score[m])



# ------------------------------------------------ v2 E11: search each remembered detail
# A tip-of-the-tongue description is several separate memories ("a lighthouse", "the dog dies",
# "a storm"). As one vector, the strongest detail decides; the right film often sits at #4-#20
# (E10 without the re-ranker). DETAILS splits the description into its details, searches each
# on its own against the Wikipedia passages, and fuses the per-detail rankings with RRF, so a
# film that several details point at rises. The result joins the main fusion as one list:
#   dv  = per-detail vector search over the plot + intro passages
#   dkw = per-detail keyword search over single passages (wiki_pfts.db, wiki_pfts.py), so the
#         words of one detail must occur in the same passage, not anywhere in the article
# DETAILS: 0 = off (default), split = sentences/clauses of the question (no model call),
# llm = DETAILS_MODEL lists the details (one short call; falls back to split).
# Only description ("identify") questions use the fused search, so named-film and
# recommendation questions are unaffected.
DETAILS = _os_w.environ.get("DETAILS", "0")
DETAIL_LISTS = tuple(x for x in _os_w.environ.get("DETAIL_LISTS", "dv,dkw").split(",") if x)
DETAILS_MODEL = _os_w.environ.get("DETAILS_MODEL", "llama3.2")
DETAIL_K = int(_os_w.environ.get("DETAIL_K", "200"))     # passages per kind per detail
DETAIL_MAX = 8
PFTS_DB = _os_w.environ.get("PFTS_DB", "")               # default: wiki_pfts.db next to movies.db
SWITCHES.update({"DETAILS": DETAILS, "DETAIL_LISTS": ",".join(DETAIL_LISTS)})
_PFTS = {"conn": None, "tried": False}
_CLAUSE = re.compile(r",?\s+(?:and then|but then|and also|and later|and at the end|but|and|then|where|when|while)\s+",
                     re.I)


_FILLER = set("""help helps appreciated appreciate thanks thank please anyone anybody ideas idea lot
name title trying try find finding looking recall forgot forget forgotten crying cried sure
guess kinda sort mind brain head""".split())


def _content_words(text):
    return [w for w in re.findall(r"[a-z0-9]+", normalize(text))
            if len(w) >= 3 and w not in _STOP and w not in _FILLER]


def split_details(question):
    """The description's separate details: sentences, long ones cut at clause words.
    [] when there are fewer than two (the whole-question search already covers it)."""
    pieces = []
    for sent in re.split(r"(?<=[.!?;])\s+|\n+", question.strip()):
        if len(sent.split()) > 22:
            pieces += [x for x in _CLAUSE.split(sent) if x and x.strip()]
        else:
            pieces.append(sent)
    out, carry = [], ""
    for p in pieces:
        p = (carry + " " + p).strip() if carry else p.strip()
        if len(_content_words(p)) < 3:
            carry = p
            continue
        carry = ""
        out.append(p)
    if carry and out and len(_content_words(carry)) >= 2:
        out[-1] = out[-1] + " " + carry
    while len(out) > DETAIL_MAX:                 # merge the two shortest neighbours
        i = min(range(len(out) - 1), key=lambda j: len(out[j]) + len(out[j + 1]))
        out[i:i + 2] = [out[i] + " " + out[i + 1]]
    return out if len(out) >= 2 else []


_DETAILS_SYSTEM = (
    "You list the separate things a person remembers about a film they are trying to identify. "
    "Give one short phrase per remembered detail: a scene, a character, the setting, the time or "
    "place, an object, a plot event, how it ends. Keep their own words. Do not guess the film and "
    "do not add facts they did not give. Skip filler such as 'I saw this years ago' or 'please help'. "
    'Reply with JSON only: {"details": ["...", "..."]} with 2 to 8 items.')
_DETAILS_SCHEMA = {"type": "object", "properties": {"details": {"type": "array", "items": {"type": "string"}}},
                   "required": ["details"]}


def llm_details(question):
    try:
        r = ollama.chat(model=DETAILS_MODEL, think=False, format=_DETAILS_SCHEMA,
                        options=llm_options({"num_predict": 220, "temperature": 0}),
                        messages=[{"role": "system", "content": _DETAILS_SYSTEM},
                                  {"role": "user", "content": question}])
        _note_llm("details", r)
        items = _json.loads(r["message"]["content"]).get("details") or []
        items = [str(x).strip() for x in items if str(x).strip() and _content_words(str(x))]
        return items[:DETAIL_MAX] if len(items) >= 2 else []
    except Exception:  # noqa: BLE001
        return []


def _pfts():
    if not _PFTS["tried"]:
        _PFTS["tried"] = True
        root = _os_w.path.dirname(_os_w.path.abspath(_VEC["db"] or "movies.db"))
        path = PFTS_DB or _os_w.path.join(root, "wiki_pfts.db")
        if _os_w.path.exists(path):
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'pfts'").fetchone():
                _PFTS["conn"] = c
        if _PFTS["conn"] is None and "dkw" in DETAIL_LISTS:
            import sys as _sys_d
            print("[retrieve] DETAILS: wiki_pfts.db not found, so no dkw list (python wiki_pfts.py)",
                  file=_sys_d.stderr)
    return _PFTS["conn"]


def _detail_vec_ranking(conn, d_vec, n):
    best = {}
    vs = _vec_store()
    arg = None
    for kind in TEXT_KINDS:
        if vs is not None and vs.chunks is not None:
            rows = vs.knn_cos(kind, d_vec, DETAIL_K)
        else:
            arg = arg or _vec_arg(d_vec)
            rows = conn.execute("SELECT chunk_id, film_id, distance FROM wiki.vec_chunks "
                                "WHERE embedding MATCH ? AND k = ? AND kind = ?",
                                (arg, DETAIL_K, kind)).fetchall()
        for _cid, fid, d in rows:
            if fid not in best or d < best[fid]:
                best[fid] = d
    return [m for m, _ in sorted(best.items(), key=lambda x: x[1])[:n]]


def _detail_kw_ranking(text, n):
    c = _pfts()
    match = _fts_query(text)
    if c is None or not match:
        return []
    out, seen = [], set()
    try:
        for (fid,) in c.execute("SELECT film_id FROM pfts WHERE pfts MATCH ? ORDER BY bm25(pfts) LIMIT ?",
                                (match, n * 4)):
            if fid not in seen:
                seen.add(fid)
                out.append(fid)
                if len(out) >= n:
                    break
    except sqlite3.OperationalError:
        return []
    return out


def detail_lists(conn, question, pool, trace=None):
    """[(name, ranking)] for the main fusion: one fused list per DETAIL_LISTS entry, or []."""
    import time as _td
    t0 = _td.perf_counter()
    details = llm_details(question) if DETAILS == "llm" else []
    how = "llm" if details else "split"
    details = details or split_details(question)
    out = []
    if len(details) >= 2 and _wiki_has(conn, "vec"):
        if "dv" in DETAIL_LISTS:
            vecs = ollama.embed(model=EMBED_MODEL, input=[QUERY_PREFIX + d for d in details])["embeddings"]
            out.append(("dv", rrf(*[_detail_vec_ranking(conn, v, pool) for v in vecs])[:pool]))
        if "dkw" in DETAIL_LISTS:
            kw = [r for r in (_detail_kw_ranking(d, pool) for d in details) if r]
            if kw:
                out.append(("dkw", rrf(*kw)[:pool]))
    if trace is not None:
        trace["details"] = {"how": how, "items": details, "ms": round((_td.perf_counter() - t0) * 1000)}
        for name, lst in out:
            trace[f"pool_{name}"] = lst[:50]
    return out


def rerank(question, conn, q_vec, current_hits, trace=None, keep_ids=(), filters=None,
           prefer=None, ranked=False, exclude_ids=()):
    """Let the chat model read the candidates' plots and pick the ones that fit.
    The embedding measures wording overlap; the model can see that "Ki-taek's
    unemployed family takes interest in the wealthy Parks for their livelihood"
    is "a poor family hired by a wealthy household".
    keep_ids: accepted title guesses, always shown first.
    filters: the question's year/genre filters; candidates must pass them.
    prefer: the same filters as a hint - fitting films move up, none are dropped.
    ranked: current_hits are already in the order to use (fused ranking), not
    to be re-sorted by distance. exclude_ids: films shown in a first look.
    Returns (hits, picked titles). Hits are unchanged if the model picks nothing."""
    import time as _tb
    _t_build = _tb.perf_counter()
    n_show = RERANK_N if RERANK_CAP else RERANK_POOL
    near = [(m, d) for m, d in knn_multi(conn, q_vec, n_show) if d <= MAX_DISTANCE]
    exclude = set(exclude_ids)
    pos = {}
    if RERANK_CAP:
        best = {}
        for mid, d in [(h["id"], h["distance"]) for h in current_hits] + near:
            if d is not None and mid not in exclude and (mid not in best or d < best[mid]):
                best[mid] = d
        if ranked:
            seq = [h["id"] for h in current_hits if h["id"] in best]
            seen_ids = set(seq)
            seq += sorted((m for m in best if m not in seen_ids), key=best.get)
        else:
            seq = sorted(best, key=best.get)
        pos = {m: i for i, m in enumerate(seq)}
        kept = [m for m in keep_ids if m in best]
        rest = [m for m in seq if m not in kept]
        fit = {}
        if prefer:
            for mid in rest:
                row = conn.execute("SELECT release_year, genres FROM movies WHERE id = ?",
                                   (mid,)).fetchone()
                fit[mid] = bool(row and fits_hint(row[0], row[1], prefer))
        if prefer and SOFT_FIT_SLOTS > 0:
            # Reserved slots: the best-ranked fitting films first, then the
            # best-ranked of the remainder (any year). Shown in rank order below.
            first = [m for m in rest if fit[m]][:SOFT_FIT_SLOTS]
            chosen = set(first)
            rest_order = first + [m for m in rest if m not in chosen]
        elif prefer and not ranked:
            rest_order = sorted(rest, key=lambda m: best[m] - (SOFT_BONUS if fit[m] else 0))
        else:
            rest_order = rest
        pool = [(m, best[m]) for m in kept] + [(m, best[m]) for m in rest_order]
    else:
        seen = {m for m, _ in near}
        pool = [(h["id"], h["distance"] or 0.0) for h in current_hits if h["id"] not in seen] + near
    cands = []
    for mid, d in pool:
        row = conn.execute("SELECT title, release_year, overview, vote_count, genres "
                           "FROM movies WHERE id = ?", (mid,)).fetchone()
        if not (row and row[2]):
            continue
        if RERANK_CAP and filters and not passes({"release_year": row[1], "genres": row[4]},
                                                 filters):
            continue
        cands.append((mid, d, row[:4]))
        if RERANK_CAP and len(cands) >= n_show:
            break
    if RERANK_CAP and prefer and SOFT_FIT_SLOTS > 0:
        # Same films, listed in rank order (guesses stay on top) so the reserved
        # slots don't also decide the order the model reads them in.
        nk = sum(1 for c in cands if c[0] in set(keep_ids))
        cands = cands[:nk] + sorted(cands[nk:], key=lambda c: pos.get(c[0], 1e9))
    if trace is not None:   # stage 3: exactly what the re-ranker is shown
        trace["rerank_in"] = [(mid, row[0], row[1], d) for mid, d, row in cands]
    if len(cands) < 2:
        return current_hits, []
    lim = RERANK_CHARS if RERANK_CAP else RERANK_PLOT_CHARS

    def shown(mid, overview):
        # v2 RERANK_TEXT: the Wikipedia passage that matched, so the re-ranker can
        # see the scene the description remembers (TMDB overviews rarely have it).
        if RERANK_TEXT == "tmdb" or not _wiki_has(conn, "vec"):
            return overview[:lim]
        wp = wiki_passage(conn, mid, q_vec)
        if not wp:
            return overview[:lim]
        raw[mid] = wp[1]
        passage = _clip(wp[1], RERANK_WIKI_CHARS)
        return passage if RERANK_TEXT == "wiki" else f"{_clip(overview, 200)} | Wikipedia: {passage}"

    raw = {}
    texts = [shown(mid, o) for mid, _, (_t, _y, o, _v) in cands]
    if RERANK_WIN in ("kw", "emb") and raw:
        wins = best_windows([raw.get(mid) for mid, _, _r in cands], question, q_vec,
                            RERANK_WIKI_CHARS, RERANK_WIN)
        changed = 0
        for i, (mid, _, (_t, _y, o, _v)) in enumerate(cands):
            if mid in raw and wins[i] is not None:
                new = wins[i] if RERANK_TEXT == "wiki" else f"{_clip(o, 200)} | Wikipedia: {wins[i]}"
                changed += new != texts[i]
                texts[i] = new
        if trace is not None:
            trace["rerank_win"] = changed

    if RERANK_ENGINE in ("cross", "cross_llm"):
        # v2: cross-encoder over title + TMDB overview + the Wikipedia passage closest
        # to the question. "cross": its top 3 are the picks. "cross_llm": its top
        # RERANK_CROSS_KEEP go on to the LLM re-ranker below (a much shorter prompt).
        import time as _tc
        t0 = _tc.perf_counter()
        docs = []
        for mid, _d, (t, y, o, _v) in cands:
            wp = wiki_passage(conn, mid, q_vec) if _wiki_has(conn, "vec") else None
            body = f"{_clip(o, 300)} {wp[1] if wp else ''}"
            docs.append(_clip(f"{t} ({(y or '')[:4]}). {body}", RERANK_CROSS_CHARS))
        try:
            sc = cross_scores(question, docs)
        except Exception as e:  # noqa: BLE001  (package missing, download failed...)
            sc = None
            if trace is not None:
                trace["cross_error"] = str(e)[:200]
        if sc is not None:
            order_i = sorted(range(len(cands)), key=lambda i: -sc[i])
            if trace is not None:
                trace["cross_ms"] = round((_tc.perf_counter() - t0) * 1000)
            if RERANK_ENGINE == "cross":
                picked = [cands[i] for i in order_i[:3]]
                ids = {c[0] for c in picked}
                if trace is not None:
                    trace["rerank_out"] = [(mid, row[0], row[1], d) for mid, d, row in picked]
                front = hydrate(conn, [(mid, d) for mid, d, _ in picked])
                for h in front:
                    h["_picked"] = True
                return front + [h for h in current_hits if h["id"] not in ids], [c[2][0] for c in picked]
            keep = order_i[:RERANK_CROSS_KEEP]
            cands = [cands[i] for i in keep]
            texts = [texts[i] for i in keep]

    def make_listing(n):
        return "\n".join(f"{i}. {t} ({(y or '')[:4]}, {v or 0:,} votes): {texts[i - 1]}"
                          for i, (_mid, _, (t, y, o, v)) in enumerate(cands[:n], 1))
    # v2: stay inside the model's context (movie-buff num_ctx 8192 tokens, ~4 chars
    # each). Past RERANK_MAX_CHARS, Ollama would silently cut the START of the
    # prompt - the instructions and the description - so drop the lowest-ranked
    # candidates instead.
    n_keep = len(cands)
    listing = make_listing(n_keep)
    while n_keep > 2 and len(listing) + len(question) > RERANK_MAX_CHARS:
        n_keep -= 1
        listing = make_listing(n_keep)
    if n_keep < len(cands):
        cands = cands[:n_keep]
        if trace is not None:
            trace["rerank_in"] = trace.get("rerank_in", [])[:n_keep]
            trace["rerank_trimmed"] = True
    if trace is not None:
        trace["rerank_chars"] = len(listing)
    _note_llm("rerank", build_ms=(_tb.perf_counter() - _t_build) * 1000)
    try:
        import ollama
        r = ollama.chat(
            model=GUESS_MODEL, think=False,
            options=llm_options({"num_predict": 16, "temperature": 0}),
            messages=[
                {"role": "system", "content":
                    "You match film descriptions to plot summaries. Read the numbered films "
                    "and reply with the numbers of the ones that fit the description, best "
                    "match first, at most 3, comma-separated, e.g. 7, 2. If none fit, reply "
                    "NONE. No other text. When several fit about equally, prefer the better-known film (more votes) - people describing a film usually mean the famous one."},
                {"role": "user", "content": f"Description: {question}\n\nFilms:\n{listing}"},
            ])
        _note_llm("rerank", r)
        text = (r["message"]["content"] or "").strip()
    except Exception:
        return current_hits, []
    if "NONE" in text.upper():
        return current_hits, []
    picked, ids = [], set()
    for n in _re.findall(r"\d+", text):
        i = int(n) - 1
        if 0 <= i < len(cands) and cands[i][0] not in ids:
            picked.append(cands[i]); ids.add(cands[i][0])
        if len(picked) == 3:
            break
    if not picked:
        return current_hits, []
    if trace is not None:   # stage 4: what the re-ranker chose, best first
        trace["rerank_out"] = [(mid, row[0], row[1], d) for mid, d, row in picked]
    front = hydrate(conn, [(mid, d) for mid, d, _ in picked])
    for h in front:
        h["_picked"] = True
    return front + [h for h in current_hits if h["id"] not in ids], [c[2][0] for c in picked]


# ------------------------------------------------ query understanding (LLM)
# Before searching, the chat model reads the question and says what it asks.
# The model decides WHICH words are a title; the database and the question's
# own words decide whether to believe it. See patch_understand.py.
import difflib as _difflib
import os as _os_q

USE_QUERY_LLM = _os_q.environ.get("QUERY_LLM", "1") != "0"
QUERY_MODEL = _os_q.environ.get("QUERY_MODEL", GUESS_MODEL)
INTENTS = ("named_film", "identify", "recommend", "other")

_FILM = {"type": "object",
         "properties": {"title": {"type": "string"},
                        "year": {"type": ["integer", "null"]}},
         "required": ["title", "year"]}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "films": {"type": "array", "items": _FILM},
        "guesses": {"type": "array", "items": _FILM},
        "search_query": {"type": "string"},
    },
    "required": ["intent", "films", "guesses", "search_query"],
}

PLAN_SYSTEM = """You read a question for a movie database and say what it is asking. Reply with JSON only.

intent:
- "named_film": about one or more specific films that the user names, or refers back to from the recent films ("it", "the first one", "the sequel").
- "identify": describes a film's plot or scenes without naming it and wants to know which film it is.
- "recommend": wants suggestions ("something like X", "a good horror movie", "what should I watch").
- "other": about people, film in general, or not about movies at all.

films: the films the question is about, as exact titles with release year (null if unsure). Include a film ONLY if the user names it, or points back to one of the recent films. Ordinary words in a description are not titles. For "something like X", X goes here. For "identify", leave films empty.

guesses: only for "identify" - up to 3 films that best fit the description, most likely first. Otherwise [].

search_query: the question rewritten so it makes sense on its own, with any reference replaced by the film's title and year.

Examples
Recent films: (none)
Question: Who directed Pulp Fiction?
{"intent":"named_film","films":[{"title":"Pulp Fiction","year":1994}],"guesses":[],"search_query":"Who directed Pulp Fiction (1994)?"}

Recent films: (none)
Question: a kid at a boarding school spends one night trapped in the library with a ghost
{"intent":"identify","films":[],"guesses":[],"search_query":"a kid at a boarding school spends one night trapped in the library with a ghost"}

Recent films: 1. Back to the Future  2. Back to the Future Part III  3. Back to the Future Part II
Question: how did the first one end?
{"intent":"named_film","films":[{"title":"Back to the Future","year":1985}],"guesses":[],"search_query":"How does Back to the Future (1985) end?"}

Recent films: (none)
Question: something like Inception but lighter
{"intent":"recommend","films":[{"title":"Inception","year":2010}],"guesses":[],"search_query":"lighter films similar to Inception (2010)"}"""

# Change 10 (HYDE): the same plan, plus a guessed TMDB-style plot for descriptions.
PLAN_SCHEMA_HYDE = {**PLAN_SCHEMA, "properties": {**PLAN_SCHEMA["properties"],
                                                  "guessed_plot": {"type": "string"}},
                    "required": PLAN_SCHEMA["required"] + ["guessed_plot"]}
PLAN_SYSTEM_HYDE = PLAN_SYSTEM.split("\n\nExamples\n")[0] + """

guessed_plot: only for "identify" - 2-3 sentences written the way a movie database summarises the film's plot: who the main character is, the setup, the central conflict. Plain and factual, no title, no "the user remembers". Otherwise "".

Examples
Recent films: (none)
Question: Who directed Pulp Fiction?
{"intent":"named_film","films":[{"title":"Pulp Fiction","year":1994}],"guesses":[],"search_query":"Who directed Pulp Fiction (1994)?","guessed_plot":""}

Recent films: (none)
Question: a kid at a boarding school spends one night trapped in the library with a ghost
{"intent":"identify","films":[],"guesses":[],"search_query":"a kid at a boarding school spends one night trapped in the library with a ghost","guessed_plot":"A lonely boy at a strict boarding school is locked in the old library overnight. There he meets the ghost of a former student who needs his help to uncover what happened to her decades ago."}

Recent films: 1. Back to the Future  2. Back to the Future Part III  3. Back to the Future Part II
Question: how did the first one end?
{"intent":"named_film","films":[{"title":"Back to the Future","year":1985}],"guesses":[],"search_query":"How does Back to the Future (1985) end?","guessed_plot":""}

Recent films: (none)
Question: something like Inception but lighter
{"intent":"recommend","films":[{"title":"Inception","year":2010}],"guesses":[],"search_query":"lighter films similar to Inception (2010)","guessed_plot":""}"""

# v2 PLAN_FAST=1: for descriptions ("identify") the planner no longer copies the whole
# question into search_query (it is never used - the search uses the question itself),
# and the guessed plot is 1-2 short sentences. Long TREC questions made the plan run
# past num_predict (400 tokens), so its JSON was cut off and the regex path took over.
# E6 (28 Sep): planner 8.2 -> 5.3 s on TREC questions; tune TREC 23 -> 21/60 (within the
# 1-2 question noise band), check and own questions unchanged. Default on; PLAN_FAST=0 = old prompt.
PLAN_FAST = _os_q.environ.get("PLAN_FAST", "1") == "1"
SWITCHES["PLAN_FAST"] = PLAN_FAST
SWITCHES["QUERY_MODEL"] = QUERY_MODEL
if PLAN_FAST:
    _ID_Q = "a kid at a boarding school spends one night trapped in the library with a ghost"
    _SQ_OLD = ("search_query: the question rewritten so it makes sense on its own, with any "
               "reference replaced by the film's title and year.")
    _SQ_NEW = _SQ_OLD + (' For "identify", reply "" - the search uses the question itself.')
    for _name in ("PLAN_SYSTEM", "PLAN_SYSTEM_HYDE"):
        _p = globals()[_name].replace(_SQ_OLD, _SQ_NEW)
        _p = _p.replace(f'"guesses":[],"search_query":"{_ID_Q}"', '"guesses":[],"search_query":""')
        _p = _p.replace("only for \"identify\" - 2-3 sentences", "only for \"identify\" - 1-2 short sentences (under 40 words)")
        _p = _p.replace("A lonely boy at a strict boarding school is locked in the old library overnight. "
                        "There he meets the ghost of a former student who needs his help to uncover "
                        "what happened to her decades ago.",
                        "A lonely boy locked in his boarding school's library overnight meets the ghost "
                        "of a former student who needs his help.")
        globals()[_name] = _p

_ARTICLES = {"the", "a", "an", "of", "and"}
_NORM_TITLES = None


# v2 E9: PLAN_ROUTE=1 - a two-step planner. ROUTE_MODEL (llama3.2, ~1.3 s) reads every question
# first; its plan is used as-is unless it says "identify" (a plot description), and only then
# does QUERY_MODEL (movie-buff, ~5 s) write the plan with the guessed plot. E8: llama3.2 as the
# only planner cut the planner 5.0 -> 1.3 s but lost 4 tune / 4 check questions, all
# descriptions; named-film and recommendation questions lost nothing. Both calls use the same
# prompt as before, so an evaluation replays them from the call cache. 0 = one planner.
PLAN_ROUTE = _os_q.environ.get("PLAN_ROUTE", "0") == "1"
ROUTE_MODEL = _os_q.environ.get("ROUTE_MODEL", "llama3.2")
SWITCHES["PLAN_ROUTE"] = PLAN_ROUTE


def understand(question, recent_titles=None):
    """The model's reading of the question, or None (-> old regex path)."""
    if PLAN_ROUTE and ROUTE_MODEL != QUERY_MODEL:
        first = _understand_with(question, recent_titles, ROUTE_MODEL, "route")
        if first is not None and first["intent"] != "identify":
            first["_planner"] = ROUTE_MODEL
            return first
    plan = _understand_with(question, recent_titles, QUERY_MODEL, "plan")
    if plan is not None:
        plan["_planner"] = QUERY_MODEL
    return plan


def _understand_with(question, recent_titles, model, stat):
    recent = "  ".join(f"{i}. {t}" for i, t in enumerate(recent_titles or [], 1)) or "(none)"
    user = f"Recent films: {recent}\nQuestion: {question}"
    try:
        import ollama
        hyde = globals().get("HYDE", False)
        kw = dict(model=model, think=False,
                  options=llm_options({"num_predict": 400 if hyde else 220, "temperature": 0}),
                  messages=[{"role": "system", "content": PLAN_SYSTEM_HYDE if hyde else PLAN_SYSTEM},
                            {"role": "user", "content": user}])
        try:
            r = ollama.chat(format=PLAN_SCHEMA_HYDE if hyde else PLAN_SCHEMA, **kw)
        except TypeError:          # older ollama client: no schema support
            r = ollama.chat(format="json", **kw)
        _note_llm(stat, r)
        raw = _json.loads(r["message"]["content"])
    except Exception:
        return None
    return _clean_plan(raw, question)


def _clean_plan(raw, question):
    if not isinstance(raw, dict) or raw.get("intent") not in INTENTS:
        return None

    def films(key):
        out = []
        for f in raw.get(key) or []:
            if not isinstance(f, dict) or not str(f.get("title") or "").strip():
                continue
            y = f.get("year")
            try:   # text, like guess_titles() - lookup_guess() compares strings
                y = str(int(y)) if y not in (None, "") else None
            except (TypeError, ValueError):
                y = None
            out.append({"title": str(f["title"]).strip(), "year": y})
        return out[:3]

    sq = str(raw.get("search_query") or "").strip() or question
    gp = str(raw.get("guessed_plot") or "").strip()
    return {"intent": raw["intent"], "films": films("films"),
            "guesses": films("guesses"), "search_query": sq,
            "guessed_plot": gp if raw["intent"] == "identify" else ""}


def _mentioned(ntitle, nquestion):
    """Does the question itself point at this (normalized) title? Most of the
    title's words must appear, allowing typos ("Shawshank Redemtion")."""
    words = [w for w in ntitle.split() if w not in _ARTICLES] or ntitle.split()
    qwords = set(nquestion.split())
    if not words:
        return False
    hit = sum(1 for w in words
              if w in qwords or _difflib.get_close_matches(w, qwords, n=1, cutoff=0.8))
    return hit / len(words) >= 0.6


def _norm_titles(conn):
    global _NORM_TITLES
    if _NORM_TITLES is None:
        _NORM_TITLES = sorted({n for _mid, n, _t, _y in title_index(conn)})
    return _NORM_TITLES


def lookup_named(conn, film, question, recent_titles):
    """The plan says the question is about `film`. Believe it only if the film
    is in movies.db and the question's words (or the conversation) point at
    it. Returns a titles_in()-style dict or None."""
    nt = normalize(film["title"])
    from_recent = any(normalize(t) == nt for t in (recent_titles or []))
    if not from_recent and not _mentioned(nt, normalize(question)):
        return None                      # the model named something the user didn't
    year = film.get("year") or None
    g = lookup_guess(conn, {"title": nt, "year": year})
    if g is None:                        # misspelled / slightly different title
        close = _difflib.get_close_matches(nt, _norm_titles(conn), n=1, cutoff=0.88)
        if close:
            g = lookup_guess(conn, {"title": close[0], "year": year})
    return g


def _snap(hits):
    """(id, title, year, distance) for each hit, in order - what a stage handed on."""
    return [(h["id"], h["title"], h.get("release_year"), h.get("distance")) for h in hits]


def retrieve(question, k=3, db_path="movies.db", recent_titles=None, explain=False,
             trace=None):
    """trace: pass a dict to have it filled with the films each stage handed on
    (pool, subject, guesses, rerank_in, rerank_out, after_cutoff, final) plus
    timings. Used by stage_scores.py; None (the default) changes nothing."""
    import time as _tt
    _t_start = _tt.perf_counter()
    _WIKI_CACHE.clear()
    _LLM_STATS.clear()
    _PASSAGE_STATS.update(fast=0, lookup=0, ms=0.0)
    conn = open_db(db_path)
    try:
        plan = understand(question, recent_titles) if USE_QUERY_LLM else None
        if trace is not None:
            trace["plan_ms"] = round((_tt.perf_counter() - _t_start) * 1000)
            trace["path"] = f"plan:{plan['intent']}" if plan else "regex"
            if plan:
                trace["planner"] = plan.get("_planner")
        if plan:
            # The model read the question (and what the last answer was about).
            # A plot description never gets a subject from its words - that is
            # what locked q42/q43 onto "One Night" and "Boarding School".
            named = []
            if plan["intent"] != "identify":
                for f in plan["films"]:
                    n = lookup_named(conn, f, question, recent_titles)
                    if n and all(n["id"] != m["id"] for m in named):
                        named.append(n)
            resolved_from = None
            if (named and recent_titles
                    and any(normalize(t) == normalize(named[0]["title"]) for t in recent_titles)
                    and not _mentioned(normalize(named[0]["title"]), normalize(question))):
                resolved_from = named[0]["title"]
            search_text = plan["search_query"] if named else question
            recommending = plan["intent"] == "recommend"
            describing = plan["intent"] == "identify"
            # Change 9: the question names a film (its words point at the title)
            # and it isn't in movies.db -> nothing, not three look-alikes.
            if UNKNOWN_FILM and plan["intent"] == "named_film" and not named:
                nq = normalize(question)
                unknown = [f["title"] for f in plan["films"]
                           if _mentioned(normalize(f["title"]), nq)]
                if unknown:
                    if trace is not None:
                        trace["unknown_film"] = unknown
                        for key in ("pool", "after_cutoff", "final"):
                            trace[key] = []
                        trace["final_distance"] = []
                        trace["total_ms"] = round((_tt.perf_counter() - _t_start) * 1000)
                    if explain:
                        print(f"  [retrieve] not in movies.db: {unknown}")
                    return []
        else:
            question, resolved_from = resolve_followup(question, recent_titles, conn)
            search_text = question
            named = None
            recommending = is_recommendation(question)
            describing = bool(DESCRIBES_A_FILM.search(question)
                              and not NAMES_A_FILM.search(question))
        filters = parse_filters(question)
        want = max(k, RECOMMEND_K) if recommending else k
        pool = POOL_WIDE if (filters or recommending) else max(POOL_DEFAULT, k * 5)
        soft = bool(SOFT_FILTERS and filters and describing and not recommending)
        if trace is not None:
            trace["soft_filters"] = soft

        _t_search = _tt.perf_counter()
        q_vec = embed_query(search_text)
        fused = bool(describing and not recommending and (HYDE or HYBRID))
        gate_confident = False       # set by the fused search below (RERANK=auto)
        if fused:
            # Changes 10 + 11: the question's vector list, the guessed plot's
            # vector list and the keyword list, merged by reciprocal rank fusion.
            # Each hit keeps its distance to the QUESTION (the cutoff's scale).
            pool = max(pool, DESCRIBE_POOL)
            q_list = knn_multi(conn, q_vec, pool)
            dist = dict(q_list)
            lists = [[m for m, _ in q_list]]
            names = ["q"]
            gp = (plan or {}).get("guessed_plot") if HYDE else ""
            if gp:
                h_list = knn_multi(conn, embed_document(gp), pool)
                lists.append([m for m, _ in h_list]); names.append("hyde")
                if trace is not None:
                    trace["guessed_plot"] = gp
                    trace["pool_hyde"] = [m for m, _ in h_list[:50]]
            wq_list = wiki_question_ranking(conn, q_vec, pool)    # v2: generated descriptions
            if wq_list:
                lists.append(wq_list); names.append("wq")
                if trace is not None:
                    trace["pool_wiki_q"] = wq_list[:50]
            if HYBRID:
                k_list = keyword_ranking(conn, question, pool)
                lists.append(k_list); names.append("kw")
                if trace is not None:
                    trace["pool_keyword"] = k_list[:50]
                wk_list = wiki_keyword_ranking(conn, question, pool)   # v2
                if wk_list:
                    lists.append(wk_list); names.append("wkw")
                    if trace is not None:
                        trace["pool_wiki_keyword"] = wk_list[:50]
            if DETAILS != "0":                                   # v2 E11
                for name, lst in detail_lists(conn, question, pool, trace):
                    lists.append(lst); names.append(name)
            order = (rrf_named(list(zip(names, lists))) if (RRF_WEIGHTS or RRF_K_ENV != RRF_K)
                     else rrf(*lists))[:pool * 2]
            for m in order:
                if m not in dist:
                    dist[m] = distance_to(conn, m, q_vec)
            hits = hydrate(conn, [(m, dist[m]) for m in order])
            if trace is not None:
                trace["pool_q"] = [m for m, _ in q_list[:50]]
            # v2 confidence gate (RERANK=auto): is the fused #1 film also near the top of
            # the individual search channels? Same features gate_sim.py simulated.
            if hits:
                chans = [[m for m, _ in q_list]]
                if gp:
                    chans.append([m for m, _ in h_list])
                chans += [wq_list, (k_list if HYBRID else []), (wk_list if HYBRID else []),
                          [m for m, _ in wiki_knn(conn, q_vec, pool)]]
                top = hits[0]["id"]
                agree = sum(1 for c in chans if top in c[:3])
                d1 = hits[0]["distance"]
                d2 = hits[1]["distance"] if len(hits) > 1 else None
                gap = (d2 - d1) if (d1 is not None and d2 is not None) else 0.0
                gate_confident = agree >= RERANK_AGREE and (RERANK_GAP is None or gap >= RERANK_GAP)
                if trace is not None:
                    trace["gate"] = {"agree": agree, "gap": round(gap, 4), "confident": gate_confident}
        else:
            hits = hydrate(conn, knn_multi(conn, q_vec, pool))
        if trace is not None:   # stage 1: the vector pool, closest first
            trace["search_ms"] = round((_tt.perf_counter() - _t_search) * 1000)
            if _wiki_has(conn, "vec"):   # v2: which films the Wikipedia passages found (cached)
                trace["pool_wiki"] = [m for m, _ in wiki_knn(conn, q_vec, pool)[:50]]
            trace["pool_size"] = pool
            trace["pool"] = _snap(hits)
        notes = {"pool": len(hits), "filters": filters,
                 "recommending": recommending}
        if plan:
            notes["plan"] = {"intent": plan["intent"], "search_query": plan["search_query"],
                             "films": [n["title"] for n in named],
                             "model_films": [f["title"] for f in plan["films"]]}
        if resolved_from:
            notes["followup_resolved_to"] = resolved_from

        # The film the question is actually about, if it names one.
        if named is None:
            named = titles_in(question, conn)
        # If the question pins a year ("the 1987 film ..."), a title match from
        # a different year is a coincidence, not the subject.
        lo, hi = filters.get("year_min"), filters.get("year_max")
        if lo is not None:
            kept = []
            for n in named:
                try:
                    y = int(n["release_year"])
                except (TypeError, ValueError):
                    continue
                if lo <= y <= hi:
                    kept.append(n)
            named = kept
        subject = named[0] if named else None
        notes["subject"] = subject["title"] if subject else None
        if trace is not None:   # stage 2a: the film the question names, if any
            trace["subject"] = [(subject["id"], subject["title"], subject["release_year"], None)] \
                if subject else []

        # Tip-of-the-tongue questions name no film. Ask the chat model for up
        # to three candidates, keep those that exist in the database and fit
        # any year in the question, and use the one the embedding agrees with
        # most - only if it is close enough. The answer still comes from the
        # database, never from the guess.
        accepted, wide = [], []
        if (subject is None and not recommending and not resolved_from
                and describing):
            # The plan's guesses when it made some; otherwise ask separately.
            guesses = (plan or {}).get("guesses") or guess_titles(question)
            notes["title_guesses"] = [f'{g["title"]} ({g["year"]})' for g in guesses]
            accepted = []
            wide = []            # GUESS_WIDE: in the database, but too far to auto-accept
            for guess in guesses:
                g = lookup_guess(conn, guess)
                if not g or any(a[1]["id"] == g["id"] for a in accepted):
                    continue
                if lo is not None and not soft:
                    try:
                        if not (lo <= int(g["release_year"]) <= hi):
                            continue
                    except (TypeError, ValueError):
                        continue
                d = distance_to(conn, g["id"], q_vec)
                if d is not None and d <= GUESS_MAX_DISTANCE:
                    accepted.append((d, g))
                elif GUESS_WIDE and d is not None and all(w[1]["id"] != g["id"] for w in wide):
                    wide.append((d, g))
            if accepted:
                # All plausible guesses go in, closest first. Picking one let
                # eXistenZ (0.77) beat The Matrix (0.79); the answering model can
                # tell them apart from their plots, the embedding could not.
                accepted.sort(key=lambda a: a[0])
                ids = {g["id"] for _, g in accepted}
                hits = hydrate(conn, [(g["id"], d) for d, g in accepted]) + \
                       [h for h in hits if h["id"] not in ids]
                notes["title_guesses_used"] = [f'{g["title"]} ({d:.3f})' for d, g in accepted]
            if wide:
                # Not placed ahead of other films; only guaranteed a place in
                # the re-ranker's list (keep_ids below).
                have = {h["id"] for h in hits}
                hits = hits + hydrate(conn, [(g["id"], d) for d, g in wide if g["id"] not in have])
                notes["title_guesses_shown"] = [f'{g["title"]} ({d:.3f})' for d, g in wide]
            if trace is not None:   # stage 2b: model guesses that passed the checks
                trace["guesses"] = [(g["id"], g["title"], g["release_year"], d)
                                    for d, g in sorted(accepted + wide, key=lambda a: a[0])]

        # Re-rank tip-of-the-tongue questions: the chat model reads ~30 candidate
        # plots and chooses. RERANK=0 switches it off for A/B evals.
        import os as _os
        rr_mode = _os.environ.get("RERANK", "auto")     # 1 = always, 0 = never, auto = gate (default)
        if rr_mode == "auto" and gate_confident and describing:
            notes["rerank_skipped"] = "search confident"
            if trace is not None:
                trace["rerank_skipped"] = True
        if (rr_mode != "0" and not (rr_mode == "auto" and gate_confident)
                and subject is None and not recommending and not resolved_from
                and describing):
            import time as _time
            _t0 = _time.perf_counter()
            rr_kw = dict(keep_ids=[g["id"] for _d, g in accepted] +
                                  [g["id"] for _d, g in (wide if describing else [])],
                         filters=None if soft else filters,
                         prefer=filters if soft else None, ranked=fused)
            first_look = {}
            hits, picked = rerank(question, conn, q_vec, hits, trace=first_look, **rr_kw)
            if not picked and HYDE and fused and RERANK_SECOND:
                # Change 10: a second look at the next candidates down the list.
                shown = [c[0] for c in first_look.get("rerank_in", [])]
                second = {}
                hits, picked = rerank(question, conn, q_vec, hits, trace=second,
                                      exclude_ids=shown, **rr_kw)
                notes["second_look"] = bool(picked)
                first_look["rerank_in"] = first_look.get("rerank_in", []) + second.get("rerank_in", [])
                if "rerank_out" in second:
                    first_look["rerank_out"] = second["rerank_out"]
                if trace is not None:
                    trace["second_look"] = True
            if trace is not None:
                trace.update(first_look)
                trace.setdefault("rerank_out", [])   # ran, picked nothing
            notes["reranked_to"] = picked
            # Two independent signals: the model's own guess (what it remembers)
            # and the re-rank (what it reads). If the re-rank leaves out an
            # accepted guess, keep the guess in 2nd place rather than drop it
            # (q25: the re-rank chose Cruel Jaws and Dinoshark over Jaws).
            try:
                top_guess = accepted[0][1] if accepted else None
            except NameError:
                top_guess = None
            if picked and top_guess and top_guess["id"] not in {h["id"] for h in hits[:len(picked)]}:
                g_hit = [h for h in hits if h["id"] == top_guess["id"]]
                if g_hit:
                    rest = [h for h in hits if h["id"] != top_guess["id"]]
                    hits = rest[:1] + g_hit + rest[1:]
                    notes["kept_guess"] = top_guess["title"]
            notes["rerank_ms"] = round((_time.perf_counter() - _t0) * 1000)
            if trace is not None:
                trace["rerank_ms"] = notes["rerank_ms"]

        if filters and not soft:
            hits = [h for h in hits if passes(h, filters)]
            notes["after_filter"] = len(hits)
        elif soft:
            notes["soft_filters"] = filters     # a hint only; nothing removed

        trust = HYDE or HYBRID
        hits = [h for h in hits if h["distance"] is None or h["distance"] <= MAX_DISTANCE
                or (trust and h.get("_picked"))]
        notes["after_cutoff"] = len(hits)
        if trace is not None:   # stage 5: after year/genre filters and the 0.90 cutoff
            trace["after_cutoff"] = _snap(hits)

        if recommending and subject:
            # Don't recommend the film someone just named, or its tie-ins -
            # "Inception: The Cobol Job" is not a recommendation for someone
            # who liked Inception.
            nsub = normalize(subject["title"])
            hits = [h for h in hits if nsub not in normalize(h["title"])]
            notes["after_subject_exclusion"] = len(hits)
        elif (MULTI_FILM and plan and subject and not recommending
              and len(named) > 1):
            # Change 13: "Is Aliens better than Alien?" - every named film (up
            # to 3) goes in, in the order named, instead of only the first.
            have = {h["id"]: h for h in hits}
            front = []
            for n in named[:3]:
                h = have.get(n["id"]) or next(iter(hydrate(conn, [(n["id"], 0.0)])), None)
                if h:
                    front.append(h)
            hits = front
            want = max(want, len(front))
            notes["multi_film"] = [h["title"] for h in front]
        elif subject and not recommending:
            # Factual question naming a film: make sure that film is present
            # even if the embedding buried it (q12's failure mode).
            if not any(h["id"] == subject["id"] for h in hits):
                found = hydrate(conn, [(subject["id"], 0.0)])
                if found:
                    hits = found + hits
                    notes["title_match_injected"] = subject["title"]
            else:
                # Already retrieved, but maybe not first ("tell me about
                # whiplash" ranked Whiplash 2 above Whiplash). The film the
                # question names leads the context.
                hits = ([h for h in hits if h["id"] == subject["id"]] +
                        [h for h in hits if h["id"] != subject["id"]])
                notes["title_match_promoted"] = subject["title"]
            # The question names one film: send that film and its other versions
            # only. Unrelated neighbours ("Fighting" next to Fight Club) just give
            # the model something to wander off to, and make the prompt longer.
            nsub = normalize(subject["title"])
            focused = [h for h in hits if h["id"] == subject["id"]
                       or normalize(h["title"]).startswith(nsub)]
            if focused:
                hits = focused
                notes["focused_on_subject"] = len(focused)

        # Collapse duplicate titles. Unless the question pins a year, three
        # slots spent on Cheaper by the Dozen (2003, 2022, 1950) is three
        # slots wasted - the remakes tell the model nothing the original
        # didn't. Keep the best-known version of each title and let the other
        # slots carry different films.
        #
        # When a year IS specified, remakes are exactly what distinguishes the
        # answer ("the 2021 version of Dune"), so title+year stays the key.
        collapse_versions = filters.get("year_min") is None
        best, order = {}, []
        for h in hits:
            key = normalize(h["title"]) if collapse_versions else (
                normalize(h["title"]), h["release_year"])
            if key not in best:
                best[key] = h
                order.append(key)
            elif (h["vote_count"] or 0) > (best[key]["vote_count"] or 0):
                # Same title, better-known version: swap it in but keep the
                # position earned by the closest match.
                best[key] = h
        hits = [best[k] for k in order]

        if recommending:
            # Relevant first, then well-known. Without this the ranking is pure
            # vector distance, which treats a 60-vote direct-to-video title the
            # same as a film everyone has heard of.
            pool_for_rank = hits[:RERANK_POOL]
            rest = hits[RERANK_POOL:]
            pool_for_rank.sort(key=lambda h: -(h["vote_count"] or 0))
            hits = pool_for_rank + rest
            notes["reranked_by_popularity"] = min(len(hits), RERANK_POOL)

        # Backfill only for browse/recommendation questions. A question about
        # one specific film must be allowed to come back empty - that is the
        # whole point of the relevance cutoff. Backfilling here is what turned
        # "the 1987 film The Cobalt Hour" into Full Metal Jacket and Predator.
        if filters and recommending and len(hits) < want:
            have = {h["id"] for h in hits}
            if subject:
                have.add(subject["id"])
            extra = backfill(conn, filters, want - len(hits), have)
            hits.extend(extra)
            notes["backfilled"] = len(extra)

        hits = hits[:want]
        notes["returned"] = len(hits)
        if _wiki_has(conn, "vec"):
            # v2: Wikipedia intro + the passage closest to the question, for the
            # answer (chat.py ANSWER_WIKI). Top 3 only; point lookups, a few ms.
            for h in hits[:3]:
                h["wiki_intro"] = wiki_intro(conn, h["id"])
                wp = wiki_passage(conn, h["id"], q_vec)
                if wp:
                    h["wiki_passage"], h["wiki_section"] = wp[1], wp[2]
                row = conn.execute("SELECT url FROM wiki.chunks WHERE film_id = ? LIMIT 1",
                                   (h["id"],)).fetchone()
                if row:
                    h["wiki_url"] = row[0]
        if trace is not None:   # stage 6: what the answer model receives
            trace["final"] = _snap(hits)
            trace["final_distance"] = [h["distance"] for h in hits]
            trace["llm"] = {k: dict(v) for k, v in _LLM_STATS.items()}
            trace["passages"] = {"fast": _PASSAGE_STATS["fast"], "lookup": _PASSAGE_STATS["lookup"],
                                 "ms": round(_PASSAGE_STATS["ms"])}
            trace["total_ms"] = round((_tt.perf_counter() - _t_start) * 1000)
        if explain:
            for h in hits:
                h["_notes"] = notes
            print(f"  [retrieve] {notes}")
        return hits
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "a heartwarming animated movie about family"
    print(f"Q: {q}\n")
    for r in retrieve(q, explain=True):
        d = "popularity" if r["distance"] is None else f"{r['distance']:.4f}"
        credits = "credits" if (r["director"] or r["top_cast"]) else "no credits"
        print(f"  {r['title']} ({r['release_year']})  [{d}]  {credits}")
