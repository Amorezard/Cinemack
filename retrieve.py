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

EMBED_MODEL = "mxbai-embed-large"
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


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


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
GUESS_MODEL = "movie-buff"
GUESS_MAX_DISTANCE = 0.87   # 0.90 let a wrong guess (The Parent Trap, 0.893) through


def guess_titles(question, n=3):
    """Up to n candidate films for a description, most likely first."""
    try:
        import ollama
        r = ollama.chat(
            model=GUESS_MODEL, think=False,
            options={"num_predict": 60, "temperature": 0},
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
    v = q_vec if isinstance(q_vec, (bytes, str)) else _json.dumps([float(x) for x in q_vec])
    row = conn.execute(
        "SELECT vec_distance_l2(embedding, ?) FROM movie_vectors WHERE movie_id = ?",
        (v, movie_id)).fetchone()
    return row[0] if row else None


EXTRA_VECTOR_TABLES = ("movie_plot_vectors",)


def knn_multi(conn, q_vec, pool):
    """Full-record KNN plus plot-only KNN, best distance per film. Cast lists
    and keywords dilute the full record (Jaws: 0.785 full, 0.734 plot-only)."""
    best = dict(knn(conn, q_vec, pool))
    v = q_vec if isinstance(q_vec, (bytes, str)) else _json.dumps([float(x) for x in q_vec])
    for table in EXTRA_VECTOR_TABLES:
        try:
            rows = conn.execute(f"SELECT movie_id, distance FROM {table} "
                                "WHERE embedding MATCH ? AND k = ?", (v, pool)).fetchall()
        except Exception:
            continue   # index not built yet
        for mid, dist in rows:
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
DESCRIBE_POOL = 100          # vector pool per list for descriptions when HYDE/HYBRID
RRF_K = 60
SWITCHES = {"RERANK_CAP": RERANK_CAP, "SOFT_FILTERS": SOFT_FILTERS,
            "SOFT_FIT_SLOTS": SOFT_FIT_SLOTS, "UNKNOWN_FILM": UNKNOWN_FILM,
            "MULTI_FILM": MULTI_FILM, "HYDE": HYDE, "HYBRID": HYBRID, "RERANK_N": RERANK_N,
            "RERANK_CHARS": RERANK_CHARS, "GUESS_WIDE": GUESS_WIDE}

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


def keyword_ranking(conn, text, n=100):
    """Film ids by SQLite FTS5 keyword match (bm25), best first. [] if the
    movies_fts index hasn't been built (python build_fts.py)."""
    global _FTS_OK
    if _FTS_OK is None:
        _FTS_OK = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'movies_fts'").fetchone())
    if not _FTS_OK:
        return []
    words = []
    for w in _re.findall(r"[a-z0-9]+", normalize(text)):
        if len(w) >= 3 and w not in _STOP and w not in words:
            words.append(w)
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words[:40])
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


def rrf(*rankings):
    """Reciprocal rank fusion: each list adds 1/(RRF_K + rank) per film."""
    score = {}
    for ranking in rankings:
        for rank, mid in enumerate(ranking, 1):
            score[mid] = score.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(score, key=lambda m: -score[m])


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
    listing = "\n".join(f"{i}. {t} ({(y or '')[:4]}, {v or 0:,} votes): {o[:RERANK_CHARS if RERANK_CAP else RERANK_PLOT_CHARS]}"
                        for i, (_, _, (t, y, o, v)) in enumerate(cands, 1))
    try:
        import ollama
        r = ollama.chat(
            model=GUESS_MODEL, think=False,
            options={"num_predict": 16, "temperature": 0},
            messages=[
                {"role": "system", "content":
                    "You match film descriptions to plot summaries. Read the numbered films "
                    "and reply with the numbers of the ones that fit the description, best "
                    "match first, at most 3, comma-separated, e.g. 7, 2. If none fit, reply "
                    "NONE. No other text. When several fit about equally, prefer the better-known film (more votes) - people describing a film usually mean the famous one."},
                {"role": "user", "content": f"Description: {question}\n\nFilms:\n{listing}"},
            ])
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

_ARTICLES = {"the", "a", "an", "of", "and"}
_NORM_TITLES = None


def understand(question, recent_titles=None):
    """The model's reading of the question, or None (-> old regex path)."""
    recent = "  ".join(f"{i}. {t}" for i, t in enumerate(recent_titles or [], 1)) or "(none)"
    user = f"Recent films: {recent}\nQuestion: {question}"
    try:
        import ollama
        hyde = globals().get("HYDE", False)
        kw = dict(model=QUERY_MODEL, think=False,
                  options={"num_predict": 400 if hyde else 220, "temperature": 0},
                  messages=[{"role": "system", "content": PLAN_SYSTEM_HYDE if hyde else PLAN_SYSTEM},
                            {"role": "user", "content": user}])
        try:
            r = ollama.chat(format=PLAN_SCHEMA_HYDE if hyde else PLAN_SCHEMA, **kw)
        except TypeError:          # older ollama client: no schema support
            r = ollama.chat(format="json", **kw)
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
    conn = open_db(db_path)
    try:
        plan = understand(question, recent_titles) if USE_QUERY_LLM else None
        if trace is not None:
            trace["plan_ms"] = round((_tt.perf_counter() - _t_start) * 1000)
            trace["path"] = f"plan:{plan['intent']}" if plan else "regex"
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
        if fused:
            # Changes 10 + 11: the question's vector list, the guessed plot's
            # vector list and the keyword list, merged by reciprocal rank fusion.
            # Each hit keeps its distance to the QUESTION (the cutoff's scale).
            pool = max(pool, DESCRIBE_POOL)
            q_list = knn_multi(conn, q_vec, pool)
            dist = dict(q_list)
            lists = [[m for m, _ in q_list]]
            gp = (plan or {}).get("guessed_plot") if HYDE else ""
            if gp:
                h_list = knn_multi(conn, embed_document(gp), pool)
                lists.append([m for m, _ in h_list])
                if trace is not None:
                    trace["guessed_plot"] = gp
                    trace["pool_hyde"] = [m for m, _ in h_list[:50]]
            if HYBRID:
                k_list = keyword_ranking(conn, question, pool)
                lists.append(k_list)
                if trace is not None:
                    trace["pool_keyword"] = k_list[:50]
            order = rrf(*lists)[:pool * 2]
            for m in order:
                if m not in dist:
                    dist[m] = distance_to(conn, m, q_vec)
            hits = hydrate(conn, [(m, dist[m]) for m in order])
            if trace is not None:
                trace["pool_q"] = [m for m, _ in q_list[:50]]
        else:
            hits = hydrate(conn, knn_multi(conn, q_vec, pool))
        if trace is not None:   # stage 1: the vector pool, closest first
            trace["search_ms"] = round((_tt.perf_counter() - _t_search) * 1000)
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
        if (_os.environ.get("RERANK", "1") != "0"
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
            if not picked and HYDE and fused:
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
        if trace is not None:   # stage 6: what the answer model receives
            trace["final"] = _snap(hits)
            trace["final_distance"] = [h["distance"] for h in hits]
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
