"""
Query understanding: let the model read the question before anything is searched.

WHY
  The title matcher decides a question names a film when a database title
  appears inside it as a phrase. That's why a plot description containing
  "one night" or "boarding school" got locked onto the films One Night and
  Boarding School (q42, q43), and why every new false match needed another
  hand-written guard (The One, Police, Giant ...). Regexes also decided what
  KIND of question it was (recommendation? description? follow-up?), and
  every phrasing they didn't anticipate - "the first one" - slipped through.

WHAT CHANGES
  One LLM call at the start of retrieve() returns a small JSON plan:
    intent        named_film | identify | recommend | other
    films         the films the user names or refers back to (from the
                  question and the films the last answer discussed)
    guesses       for "identify" only: up to 3 candidate films
    search_query  the question rewritten to stand alone ("how did the first
                  one end" -> "How does Back to the Future (1985) end?")
  Then plain code checks the plan against reality:
    - a film only becomes the subject if it's in movies.db AND the question's
      own words (or the conversation) point at it - so the model can't invent
      a subject, and a plot description never has one;
    - misspelled titles are matched with fuzzy search as a fallback;
    - "identify" questions go through the existing guess + re-rank path, with
      the plan's guesses replacing the separate guess_titles() call - so plot
      descriptions still make two LLM calls, not three.
  Everything after that (filters, relevance cutoff, re-rank, focus, backfill)
  is unchanged. If the call fails or returns bad JSON, retrieve() falls back
  to the old regex path for that question.

A/B
  QUERY_LLM=0 python quick_check.py      -> old behaviour, exactly
  python quick_check.py                  -> new behaviour
  QUERY_MODEL=<name>                     -> use a smaller/faster model for
                                            the plan (default: movie-buff)

Run from ~/movie-buff/rag AFTER patch_followup.py:   python patch_understand.py
Backups: retrieve.py.bak_understand. Refuses to run twice.
"""

import shutil
import sys
from pathlib import Path

MARKER = "def understand("

NEW_FUNCTIONS = r'''
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

_ARTICLES = {"the", "a", "an", "of", "and"}
_NORM_TITLES = None


def understand(question, recent_titles=None):
    """The model's reading of the question, or None (-> old regex path)."""
    recent = "  ".join(f"{i}. {t}" for i, t in enumerate(recent_titles or [], 1)) or "(none)"
    user = f"Recent films: {recent}\nQuestion: {question}"
    try:
        import ollama
        kw = dict(model=QUERY_MODEL, think=False,
                  options={"num_predict": 220, "temperature": 0},
                  messages=[{"role": "system", "content": PLAN_SYSTEM},
                            {"role": "user", "content": user}])
        try:
            r = ollama.chat(format=PLAN_SCHEMA, **kw)
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
    return {"intent": raw["intent"], "films": films("films"),
            "guesses": films("guesses"), "search_query": sq}


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


'''

PLAN_BLOCK_OLD = '''        question, resolved_from = resolve_followup(question, recent_titles, conn)
        filters = parse_filters(question)
        recommending = is_recommendation(question)
        want = max(k, RECOMMEND_K) if recommending else k
        pool = POOL_WIDE if (filters or recommending) else max(POOL_DEFAULT, k * 5)

        q_vec = embed_query(question)
        hits = hydrate(conn, knn_multi(conn, q_vec, pool))
        notes = {"pool": len(hits), "filters": filters,
                 "recommending": recommending}
        if resolved_from:
            notes["followup_resolved_to"] = resolved_from

        # The film the question is actually about, if it names one.
        named = titles_in(question, conn)
'''

PLAN_BLOCK_NEW = '''        plan = understand(question, recent_titles) if USE_QUERY_LLM else None
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

        q_vec = embed_query(search_text)
        hits = hydrate(conn, knn_multi(conn, q_vec, pool))
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
'''

EDITS = [
    ('def retrieve(question, k=3, db_path="movies.db", recent_titles=None, explain=False):\n',
     NEW_FUNCTIONS.lstrip("\n")
     + 'def retrieve(question, k=3, db_path="movies.db", recent_titles=None, explain=False):\n'),
    (PLAN_BLOCK_OLD, PLAN_BLOCK_NEW),
    ('''        if (subject is None and not recommending and not resolved_from
                and DESCRIBES_A_FILM.search(question)
                and not NAMES_A_FILM.search(question)):
            guesses = guess_titles(question)
''',
     '''        if (subject is None and not recommending and not resolved_from
                and describing):
            # The plan's guesses when it made some; otherwise ask separately.
            guesses = (plan or {}).get("guesses") or guess_titles(question)
'''),
    ('''                and subject is None and not recommending and not resolved_from
                and DESCRIBES_A_FILM.search(question)
                and not NAMES_A_FILM.search(question)):
''',
     '''                and subject is None and not recommending and not resolved_from
                and describing):
'''),
]


def main():
    path = Path(__file__).resolve().parent / "retrieve.py"
    if not path.exists():
        sys.exit(f"retrieve.py not found next to this script. Run it from ~/movie-buff/rag. Nothing changed.")
    text = path.read_text()
    if MARKER in text:
        sys.exit("Already applied (retrieve.py has understand()). Nothing changed.")
    if "REFERENCE_RE" not in text:
        sys.exit("Run patch_followup.py first. Nothing changed.")
    for old, new in EDITS:
        n = text.count(old)
        if n != 1:
            sys.exit(f"expected 1 match for\n{old[:200]!r}...\nfound {n}. Nothing changed.")
        text = text.replace(old, new)
    shutil.copy2(path, path.with_name("retrieve.py.bak_understand"))
    path.write_text(text)
    print("patched retrieve.py  (backup: retrieve.py.bak_understand)")
    print("\nNext:  QUERY_LLM=0 python quick_check.py   (old way)")
    print("       python quick_check.py               (new way)")


if __name__ == "__main__":
    main()
