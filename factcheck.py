"""
Change 16: a rule-based fact check of an answer against the films it was given.

    from factcheck import fact_check
    fact_check(answer, hits, question) -> {"ok": bool, "flags": [...], "checked": n}

Rules (no model call, a few milliseconds):
  years    every year in the answer must appear in a given film's year or text,
           or in the question
  ratings  "7.8/10" / "rated 7.8" must match a given film's TMDB rating (+/-0.1)
  people   a person the database knows (any director or top-5 cast member)
           who is named in the answer but isn't in the given films' credits or
           plots, or in the question
  films    a film title from the database (2+ words) named in the answer that
           isn't one of the given films and isn't in the question

It only flags what it can check. A flag means "this isn't backed by the
database", not necessarily "this is false".

    python factcheck.py results/judge-night_all.json   # score saved answers
"""

import json
import re
import sqlite3
import sys
import unicodedata

DB = "movies.db"
_PEOPLE = None
_TITLES = None
YEAR_RE = re.compile(r"\b(19[0-9]{2}|20[0-2][0-9])\b")
RATING_RE = re.compile(r"\b(\d\.\d)\s*(?:/\s*10|out of 10)|\brated\s+(\d\.\d)\b", re.I)
NAME_RE = re.compile(r"\b([A-Z][\w'À-ɏ.-]+(?:\s+(?:de|van|von|del|la|le|da)?\s*"
                     r"[A-Z][\w'À-ɏ.-]+){1,3})")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.casefold()).split()


def key(s):
    return " ".join(norm(s))


def _load(db=DB):
    global _PEOPLE, _TITLES
    if _PEOPLE is None:
        conn = sqlite3.connect(db)
        people, titles = set(), set()
        for title, director, cast in conn.execute("SELECT title, director, top_cast FROM movies"):
            for p in [director or ""] + (cast or "").split(","):
                for name in p.split(","):
                    k = key(name.split(" as ")[0])
                    if len(k.split()) >= 2:
                        people.add(k)
            k = key(title)
            if len(k.split()) >= 2:
                titles.add(k)
        conn.close()
        _PEOPLE, _TITLES = people, titles
    return _PEOPLE, _TITLES


def _context(hits):
    """Everything the model was given, as one normalized string + years + ratings."""
    parts, years, ratings = [], set(), []
    conn = None
    for h in hits:
        row = None
        if h.get("id") is not None:
            try:
                conn = conn or sqlite3.connect(DB)
                row = conn.execute("SELECT title, release_year, director, top_cast, overview, "
                                   "vote_average FROM movies WHERE id = ?", (h["id"],)).fetchone()
            except sqlite3.Error:
                row = None
        title, year, director, cast, overview, va = row or (
            h.get("title"), h.get("release_year"), h.get("director"), h.get("top_cast"),
            h.get("text") or "", h.get("vote_average"))
        parts += [title or "", director or "", cast or "", overview or ""]
        years.update(YEAR_RE.findall(" ".join(filter(None, [str(year or ""), overview or ""]))))
        if va:
            ratings.append(float(va))
    if conn:
        conn.close()
    return " " + key(" ".join(parts)) + " ", years, ratings


def fact_check(answer, hits, question=""):
    if not answer or not answer.strip():
        return {"ok": True, "flags": [], "checked": 0}
    people, titles = _load()
    ctx, years, ratings = _context(hits or [])
    q = " " + key(question) + " "
    flags, checked = [], 0

    for y in sorted(set(YEAR_RE.findall(answer))):
        checked += 1
        if y not in years and f" {y} " not in ctx and f" {y} " not in q:
            flags.append(f"the year {y} isn't in the films it was given")

    for m in RATING_RE.finditer(answer):
        checked += 1
        r = float(m.group(1) or m.group(2))
        if ratings and not any(abs(r - x) <= 0.1 for x in ratings):
            flags.append(f"the rating {r} doesn't match the database")

    seen = set()
    for m in NAME_RE.finditer(answer):
        cand = m.group(1)
        words = cand.split()
        # every 2-3 word window, so "Director Steven Spielberg" still finds the name
        for n in (3, 2):
            for i in range(len(words) - n + 1):
                k = key(" ".join(words[i:i + n]))
                if not k or k in seen:
                    continue
                if k in people:
                    seen.add(k)
                    checked += 1
                    if f" {k} " not in ctx and f" {k} " not in q:
                        flags.append(f"names {' '.join(words[i:i + n])}, who isn't in the films it was given")

    for m in re.finditer(r"\*\*?([^*]+?)\*\*?|\"([^\"]+)\"|“([^”]+)”", answer):
        cand = next(g for g in m.groups() if g)
        k = key(re.sub(r"\(\d{4}\)", "", cand))
        if k in titles and k not in seen:
            seen.add(k)
            checked += 1
            if f" {k} " not in ctx and f" {k} " not in q:
                flags.append(f"mentions the film {cand.strip()}, which it wasn't given")

    return {"ok": not flags, "flags": flags[:5], "checked": checked}


if __name__ == "__main__":
    # Score the answers a judge.py run saved: how many get flagged, and why.
    for path in sys.argv[1:]:
        rows = json.load(open(path))
        conn = sqlite3.connect(DB)
        flagged = 0
        for r in rows:
            titles = re.findall(r"^(.+?) \((?:\d{4}|unknown year)\)$", r.get("context", ""), re.M)
            hits = []
            for t in titles:
                row = conn.execute("SELECT id FROM movies WHERE title = ? ORDER BY vote_count DESC",
                                   (t,)).fetchone()
                if row:
                    hits.append({"id": row[0]})
            res = fact_check(r.get("answer", ""), hits, r.get("question", ""))
            if not res["ok"]:
                flagged += 1
                print(f"{r['id']:5s} {'; '.join(res['flags'])[:150]}")
        print(f"{path}: {flagged}/{len(rows)} answers flagged")
