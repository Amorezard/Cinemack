#!/usr/bin/env python3
"""
D7 (v7, 27 Oct): the fact fast path - "who directed Heat?", "how long is The Irishman?", "when did Dune come out?"
answered straight from facts.db in milliseconds, without the planner (~5 s) or the answer model (~8 s).
FAST=1 on the website (off until its rule passes: on the analytic set's factual questions every fast answer holds
the facts the full answer must hold, and no non-factual question takes the fast path).

It fires only when ALL of these hold, otherwise the normal pipeline answers:
    - the question is one short fact question of a known shape (director, year / release date, runtime, writer,
      composer, cinematographer, main cast, age rating, budget, worldwide gross) and nothing else ("and is it good?"
      or "why ..." -> normal path);
    - it names exactly one film by its title (or "it" / "that film" after an answer about one film), and that
      title is unambiguous: no other film of the same title has even a third of its votes (or the question gives
      the year);
    - facts.db has the fact.
The answer names its source like the full answer does ("per TMDB"), and the site shows the film's card as usual.

    python fastpath.py try "who directed Heat"
    python fastpath.py check sets/analytic.json      # the rule: facts kept on every question it answers
"""
import json
import os
import re
import sqlite3
import sys
import time

import facts_db as F

FAST = os.environ.get("FAST", "0") == "1"

_T = r"(?P<t>.+?)"
_END = r"\s*\??\s*$"
SHAPES = [
    ("director", re.compile(rf"^(?:who|which director)\s+(?:directed|made|helmed)\s+{_T}{_END}", re.I)),
    ("director", re.compile(rf"^who\s+(?:is|was)\s+the\s+director\s+of\s+{_T}{_END}", re.I)),
    ("director", re.compile(rf"^(?:the\s+)?director\s+of\s+{_T}{_END}", re.I)),
    ("year", re.compile(rf"^(?:what|which)\s+year\s+(?:did|was)\s+{_T}\s+(?:come\s+out|released|made|release)"
                        rf"{_END}", re.I)),
    ("year", re.compile(rf"^when\s+(?:did|was)\s+{_T}\s+(?:come\s+out|released|first\s+released|release|premiere|"
                        rf"premiered|in\s+theaters)\b(?:\s+in\s+theaters)?{_END}", re.I)),
    ("year", re.compile(rf"^(?:what\s+is\s+the\s+)?release\s+date\s+(?:of|for)\s+{_T}{_END}", re.I)),
    ("runtime", re.compile(rf"^how\s+long\s+is\s+{_T}(?:\s+in\s+minutes)?{_END}", re.I)),
    ("runtime", re.compile(rf"^(?:what\s+is\s+)?(?:the\s+)?(?:runtime|run\s+time|length)\s+of\s+{_T}{_END}", re.I)),
    ("writer", re.compile(rf"^who\s+wrote\s+(?:the\s+(?:script|screenplay)\s+(?:for|of)\s+)?{_T}{_END}", re.I)),
    ("composer", re.compile(rf"^who\s+(?:composed|wrote|did)\s+the\s+(?:score|music|soundtrack)\s+(?:for|of|to|in)\s+"
                            rf"{_T}{_END}", re.I)),
    ("dop", re.compile(rf"^who\s+(?:was\s+the\s+cinematographer|shot)\s+(?:on|of|for)?\s*{_T}{_END}", re.I)),
    ("cast", re.compile(rf"^who\s+(?:stars|starred|is|was|acts|acted)\s+in\s+{_T}{_END}", re.I)),
    ("cast", re.compile(rf"^who\s+(?:are|were)\s+the\s+(?:main\s+)?(?:actors|cast|stars)\s+(?:in|of)\s+{_T}{_END}", re.I)),
    ("rating", re.compile(rf"^what\s+is\s+{_T}\s+rated{_END}", re.I)),
    ("rating", re.compile(rf"^(?:what\s+is\s+the\s+)?(?:age|mpaa)\s+rating\s+(?:of|for)\s+{_T}{_END}", re.I)),
    ("budget", re.compile(rf"^(?:what\s+was|what's)\s+the\s+budget\s+(?:of|for)\s+{_T}{_END}", re.I)),
    ("budget", re.compile(rf"^how\s+much\s+did\s+{_T}\s+cost(?:\s+to\s+make)?{_END}", re.I)),
    ("gross", re.compile(rf"^how\s+much\s+(?:money\s+)?did\s+{_T}\s+(?:make|gross|earn)(?:\s+(?:worldwide|at\s+the\s+box\s+"
                         rf"office))?{_END}", re.I)),
]
R_PRONOUN = re.compile(r"^(it|that|this|that (film|movie)|this (film|movie)|the (film|movie))$", re.I)

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


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold().replace("&", "and"))).strip()


def find_film(title, recent_titles=None):
    """tmdb_id of the one film this title means, or None (unknown or ambiguous)."""
    t = title.strip().strip("\"'“”‘’").strip()
    t = re.sub(r"^(the movie|the film|movie|film)\s+", "", t, flags=re.I)
    year = None
    m = re.search(r"\s*\((\d{4})\)\s*$|\s+(\d{4})$", t)
    if m:
        year = int(m.group(1) or m.group(2))
        t = t[:m.start()].strip()
    if R_PRONOUN.match(t):
        if not recent_titles:
            return None
        t = recent_titles[0]
    nt = _norm(t)
    if not nt:
        return None
    rows = _q("select tmdb_id, title, year, coalesce(vote_count, 0) from films where title = ? collate nocase", (t,))
    if not rows:
        try:                                         # nicknames / alternate titles (aliases.py)
            rows = _q("select a.tmdb_id, f.title, f.year, coalesce(f.vote_count, 0) from aliases a join films f "
                      "on f.tmdb_id = a.tmdb_id where a.norm = ?", (nt,))
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        return None
    if year:
        rows = [r for r in rows if r[2] and abs(int(r[2]) - year) <= 1]
        if len(rows) != 1:
            return None
        return rows[0][0]
    rows.sort(key=lambda r: -r[3])
    if len(rows) > 1 and rows[1][3] >= rows[0][3] / 3:
        return None                                  # two well-known films share the title: let the planner ask
    return rows[0][0] if rows[0][3] >= 20 else None


def _crew(fid, jobs):
    return [n for (n,) in _q("select distinct p.name from credits c join people p on p.person_id = c.person_id "
                             f"where c.tmdb_id = ? and c.kind = 'crew' and c.job in ({','.join('?' * len(jobs))}) "
                             "order by c.ord", (fid, *jobs))]


def _usd(v):
    return f"${v / 1e9:.2f} billion" if v >= 1e9 else f"${v / 1e6:.1f} million" if v >= 1e6 else f"${v:,.0f}"


def _join(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def answer(question, recent_titles=None):
    """{'answer', 'tmdb_id', 'kind', 'ms'} or None (-> the normal pipeline)."""
    t0 = time.perf_counter()
    q = " ".join((question or "").split())
    if not q or len(q) > 120 or re.search(r"\b(and|also|why|but|better|worth|good|compare|vs)\b", q, re.I) \
            and not re.search(r"^who\s+(?:stars|starred)", q, re.I):
        return None
    for kind, rx in SHAPES:
        m = rx.match(q)
        if not m:
            continue
        fid = find_film(m.group("t"), recent_titles)
        if not fid:
            return None
        r = _q("select title, year, release_date, us_release_date, runtime from films where tmdb_id = ?", (fid,))
        if not r:
            return None
        title, year, rd, usrd, rt = r[0]
        name = f"*{title}* ({year})" if year else f"*{title}*"
        text = None
        if kind == "director":
            d = _crew(fid, ("Director",))
            text = f"{name} was directed by {_join(d)}, per TMDB." if d else None
        elif kind == "year":
            if rd:
                text = (f"{name} was released on {rd}" + (f" (US theatrical release {usrd})" if usrd and usrd != rd else "")
                        + ", per TMDB.")
        elif kind == "runtime":
            if rt:
                text = f"{name} runs {rt} minutes ({rt // 60} h {rt % 60:02d} min), per TMDB."
        elif kind == "writer":
            w = _crew(fid, ("Screenplay", "Writer"))
            text = f"{name} was written by {_join(w)}, per TMDB." if w else None
        elif kind == "composer":
            w = _crew(fid, ("Original Music Composer", "Music"))
            text = f"The music for {name} is by {_join(w)}, per TMDB." if w else None
        elif kind == "dop":
            w = _crew(fid, ("Director of Photography",))
            text = f"{name} was shot by cinematographer {_join(w)}, per TMDB." if w else None
        elif kind == "cast":
            c = _q("select p.name, c.character from credits c join people p on p.person_id = c.person_id where "
                   "c.tmdb_id = ? and c.kind = 'cast' order by c.ord limit 5", (fid,))
            if c:
                text = f"{name} stars " + _join([f"{n}" + (f" ({ch})" if ch else "") for n, ch in c]) + ", per TMDB."
        elif kind == "rating":
            c = _q("select certification from certifications where tmdb_id = ? and region = 'US' and certification != '' "
                   "order by release_type = 3 desc, release_type limit 1", (fid,))
            text = f"{name} is rated {c[0][0]} in the US, per TMDB's release data." if c else None
        elif kind in ("budget", "gross"):
            m2 = _q(f"select {kind}, {kind}_src from money where tmdb_id = ?", (fid,))
            if m2 and m2[0][0]:
                src = {"tmdb": "TMDB", "wikipedia_infobox": "Wikipedia", "wikidata": "Wikidata"}.get(m2[0][1], m2[0][1])
                text = (f"{name} had a production budget of {_usd(m2[0][0])}" if kind == "budget" else
                        f"{name} grossed {_usd(m2[0][0])} worldwide") + f", per {src}."
        if not text:
            return None
        return {"answer": text, "tmdb_id": fid, "kind": kind, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    return None


ANALYSIS = {"why", "multihop", "compare", "reviews", "career", "recommend", "tot", "honesty", "current", "detail",
            "awards", "reception", "gap", "divisive", "reappraised", "trend"}


def cmd_check(path):
    qs = json.load(open(path))["questions"]
    fired = ok = wrong_fire = strict_wrong = 0
    for q in qs:
        r = answer(q["question"])
        if not r:
            continue
        fired += 1
        need = q.get("expect_in_context") or []
        has = all(s.casefold() in r["answer"].casefold() for s in need)
        # 29 Oct: "non-factual" = a category that asks for analysis, a list or judgment. The first checker counted every
        # category but "factual" as non-factual, so a43 "Who directed Sinners?" and a44 "What was the budget of ...?"
        # (category "recent": one fact about a 2025 film) counted as wrong fires although the rule is about questions
        # that need more than one fact. Both readings are printed; the write-up reports both.
        analysis = q.get("category") in ANALYSIS
        strict_factual = q.get("category") in ("factual", "fact", "facts")
        ok += has and not analysis
        wrong_fire += analysis
        strict_wrong += not strict_factual
        print(f"  {q['id']} {q.get('category')}: {'ok ' if has and not analysis else 'BAD'} {r['ms']} ms  {r['answer']}"
              + ("" if has else f"   (missing {need})"))
    total_fact = sum(1 for q in qs if q.get("category") in ("factual", "fact", "facts"))
    print(f"fast path answered {fired} questions ({total_fact} labelled factual in the set); facts kept {ok}/{fired}; "
          f"analysis questions answered {wrong_fire}; answered outside the 'factual' label {strict_wrong}")
    print(f"RULE fast path: {'PASS' if fired and ok == fired and wrong_fire == 0 else 'FAIL'}"
          f"  (first checker, 'factual' label only: {'PASS' if fired and strict_wrong == 0 and ok == fired else 'FAIL'})")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "try":
        print(answer(" ".join(a[1:])))
    elif a and a[0] == "check":
        cmd_check(a[1] if len(a) > 1 else "sets/analytic.json")
    else:
        print(__doc__)
