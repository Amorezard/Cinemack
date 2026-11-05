#!/usr/bin/env python3
"""
D2 (v7, 27 Oct): director / actor filters for recommendations and browse questions - PEOPLE=1 (off until its rule
passes: the people examples are right and tune / check / analytic lose <= 1).

"Tom Hanks comedies from the 90s", "movies directed by Kathryn Bigelow", "a thriller starring Denzel Washington",
"something with Florence Pugh in it": the search alone returns films LIKE that, not films WITH that person. With
PEOPLE=1 the hits of a recommendation / browse question that names a person are limited to that person's films
(TMDB credits, plus IMDb principal cast and crew for coverage), in search order, then topped up with the person's
best-known films that fit the question's year and genre filters.

    python people.py try "Tom Hanks comedies from the 90s"
    python people.py check sets/people.json     # the examples: every film returned has the person (rule: all right)
"""
import json
import os
import re
import sqlite3
import sys

import facts_db as F

PEOPLE = os.environ.get("PEOPLE", "0") == "1"
_NAME = r"([A-Z][A-Za-zà-ÿ'’.-]+(?:\s(?:[A-Z]\.|[A-Z][A-Za-zà-ÿ'’-]+|de|van|von|del|da|di|le|la)){1,3})"
PATTERNS = [
    (re.compile(rf"\bdirected by {_NAME}"), "director"),
    (re.compile(rf"\b{_NAME}[- ]directed\b"), "director"),
    (re.compile(rf"\b(?:films?|movies?) (?:by|from) (?:director )?{_NAME}"), "director"),
    (re.compile(rf"\bdirector {_NAME}"), "director"),
    (re.compile(rf"\b(?:starring|stars|featuring|with) {_NAME}"), "actor"),
    (re.compile(rf"\b{_NAME} (?:is |was )?in it\b"), "actor"),
    (re.compile(rf"\bactor {_NAME}|\bactress {_NAME}"), "actor"),
    (re.compile(rf"\b{_NAME}['’]s? (?:films?|movies?|comedies|dramas|thrillers|westerns|horror|work|best)\b"), "any"),
    (re.compile(rf"\b{_NAME} (?:films?|movies?|comedies|dramas|thrillers|westerns|horror movies|action movies|"
                rf"rom-?coms|romances)\b"), "any"),
    (re.compile(rf"\ba {_NAME} (?:film|movie|comedy|drama|thriller|western)\b"), "any"),
]
JOBS = {"director": ("Director",), "actor": ("Actor",), "any": None}
R_BROWSE = re.compile(r"\b(recommend\w*|suggest\w*|movies|films|comedies|dramas|thrillers|westerns|something|anything|"
                      r"what should i watch|any good|best|list|"
                      # 29 Oct: singular asks ("a thriller starring Denzel Washington" was left unfiltered, p03)
                      r"(?:a|an|another|one more)\s+(?:good\s+|great\s+|new\s+|old\s+|recent\s+|classic\s+)?"
                      r"(?:movie|film|comedy|drama|thriller|western|horror(?: movie| film)?|romance|musical|"
                      r"documentary|action movie|sci-fi movie|war movie|crime movie|animated movie|mystery)"
                      r"\s+(?:starring|with|by|directed by|featuring|from))\b", re.I)

_DB = {"conn": None, "tried": False}


def db():
    if not _DB["tried"]:
        _DB["tried"] = True
        _DB["conn"] = F.connect(readonly=True)
    return _DB["conn"]


def _q(sql, p=()):
    c = db()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


def person(name, role):
    """(person_id, name) of the best-known person by that name with credits in that role, or None."""
    jobs = JOBS.get(role)
    best = None
    for pid, n, pop in _q("select person_id, name, popularity from people where name = ? collate nocase", (name,)):
        q = "select count(*) from credits where person_id = ?" + (
            f" and job in ({','.join('?' * len(jobs))})" if jobs else "")
        cnt = (_q(q, (pid, *(jobs or ()))) or [[0]])[0][0]
        if cnt and (best is None or (cnt, pop or 0) > (best[2], best[3])):
            best = (pid, n, cnt, pop or 0)
    return (best[0], best[1]) if best else None


def parse(question):
    """[(person_id, name, role)] the question names, in order."""
    out, seen = [], set()
    for rx, role in PATTERNS:
        for m in rx.finditer(question or ""):
            name = m.group(1).strip()
            name = re.sub(r"\s+(movies?|films?|comedies|dramas|thrillers)$", "", name)
            if name.lower() in seen:
                continue
            p = person(name, role)
            if not p and len(name.split()) > 2:          # "Tom Hanks Comedies" -> "Tom Hanks"
                p = person(" ".join(name.split()[:2]), role)
            if p:
                seen.add(name.lower())
                out.append((p[0], p[1], role))
    return out


def films_of(pid, role, name=None):
    """Set of TMDB film ids the person worked on in that role: TMDB credits + IMDb principals / crew."""
    jobs = JOBS.get(role)
    q = "select distinct tmdb_id from credits where person_id = ?" + (
        f" and job in ({','.join('?' * len(jobs))})" if jobs else "")
    ids = {r[0] for r in _q(q, (pid, *(jobs or ())))}
    if name:
        cats = {"director": ("director",), "actor": ("actor", "actress", "self"), "any": None}[role]
        for (nc,) in _q("select nconst from imdb_names where name = ? collate nocase", (name,)):
            qq = "select f.tmdb_id from imdb_principals p join films f on f.imdb_id = p.tconst where p.nconst = ?" + (
                f" and p.category in ({','.join('?' * len(cats))})" if cats else "")
            ids |= {r[0] for r in _q(qq, (nc, *(cats or ())))}
            if role in ("director", "any"):
                ids |= {r[0] for r in _q("select f.tmdb_id from imdb_crew c join films f on f.imdb_id = c.tconst "
                                         "where (',' || c.directors || ',') like ?", (f"%,{nc},%",))}
    return ids


def apply(question, hits, want, conn_movies=None, filters=None, intent=None):
    """The hits limited to the named people's films, topped up from their filmography. Unchanged when the question
    names nobody, or is not a recommendation / browse question."""
    if intent in ("named_film", "identify") or not R_BROWSE.search(question or ""):
        return hits, None
    ps = parse(question)
    if not ps:
        return hits, None
    ids = None
    for pid, name, role in ps:
        f = films_of(pid, role, name)
        ids = f if ids is None else ids & f
    ids = ids or set()
    kept = [h for h in hits if h.get("id") in ids]
    if len(kept) < want and conn_movies is not None:
        import retrieve as R
        have = {h["id"] for h in kept}
        rest = [r for r in _q(f"select tmdb_id, vote_count from films where tmdb_id in "
                              f"({','.join(str(int(i)) for i in ids) or '0'}) order by vote_count desc")
                if r[0] not in have]
        extra = R.hydrate(conn_movies, [(fid, None) for fid, _vc in rest[:200]])
        if filters:
            extra = [h for h in extra if R.passes(h, filters)]
        try:
            import rec_clean
            if rec_clean.REC_CLEAN:
                extra, _d = rec_clean.clean(extra, filters, question)
        except Exception:  # noqa: BLE001
            pass
        kept += extra[: want - len(kept)]
    return kept[:want], [f"{n} ({r})" for _p, n, r in ps]


def cmd_check(path):
    import retrieve as R
    qs = json.load(open(path))["questions"]
    ok_all = True
    for q in qs:
        hits = R.retrieve(q["question"], k=10)
        ps = parse(q["question"])
        need = set.intersection(*[films_of(p, r, n) for p, n, r in ps]) if ps else set()
        wrong = [h["title"] for h in hits if h["id"] not in need]
        ok = bool(ps) and not wrong and len(hits) > 0
        ok_all &= ok
        print(f"{q['id']} {'ok ' if ok else 'BAD'} people {[n for _p, n, _r in ps]}  {len(hits)} films"
              + (f"; not theirs: {wrong[:5]}" if wrong else "") + f"  {q['question'][:60]}")
    print(f"RULE people examples: {'PASS' if ok_all else 'FAIL'}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "try":
        q = " ".join(a[1:])
        print(parse(q))
    elif a and a[0] == "check":
        cmd_check(a[1] if len(a) > 1 else "sets/people.json")
    else:
        print(__doc__)
