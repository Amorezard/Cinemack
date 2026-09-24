"""
Turn new_questions.txt into test questions (change 2: grow your own set).

    python add_questions.py new_questions.txt     # tonight: check + convert -> new_questions.json
    python add_questions.py --merge               # morning, AFTER the overnight run:
                                                  #   adds them to test_set.json, then run
                                                  #   python split_sets.py

Format, one per line (blank lines and lines starting with # are skipped):
    What's the movie where a toy cowboy is jealous of a new space toy? | Toy Story (1995)
    Who directed Heat? | Heat (1995)
    Is Alien better than Aliens? | Alien (1979); Aliens (1986)
    What can you tell me about the 1998 film Velvet Lantern? | NONE

The film must be in movies.db (checked by title, and year if given), otherwise
the line is reported and left out - a question the database can't answer
measures the database, not the system. NONE = a made-up film: pass means
nothing comes back. Each question gets a type from its wording, and the
notes judge.py grades answers against.

Not merged tonight on purpose: the overnight run reads test_set.json and the
sets/ files, and changing them mid-run would mix question sets.
"""

import json
import re
import sqlite3
import sys
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum() and not unicodedata.combining(c))


def kind(q):
    ql = q.lower()
    if re.search(r"\b(better than|or|vs\.?|versus|compare)\b", ql) and re.search(r"\bbetter|compare|versus|vs\b", ql):
        return "compare"
    if re.search(r"\bwho (directed|starred|stars|plays|played|is in|was in)\b|\bcast\b|\bdirector\b", ql):
        return "credits"
    if re.search(r"worth (watching|it|seeing)|is it (any )?good|should i (watch|see)", ql):
        return "opinion"
    if re.search(r"recommend|suggest|something like|similar to|what should i watch", ql):
        return "recommend"
    if re.search(r"\b(movie|film) (where|about|with|in which)\b|the one where|what'?s (that|the) (movie|film)|there'?s a (movie|film)", ql):
        return "identify"
    return "general"


NOTES = {
    "identify": "PASS = names {t} ({y}) and describes it from the context.",
    "credits": "PASS = names the people asked about for {t} ({y}) from the context. No review.",
    "opinion": "PASS = clear verdict first on {t} ({y}) and who would enjoy it, from the rating and plot in the context.",
    "compare": "PASS = compares the named films using only the context.",
    "recommend": "PASS = recommends two or three films from the context, each with a one-line reason.",
    "general": "PASS = accurate answer about {t} ({y}) from the context; no invented facts.",
    "no_match": "This film does not exist. PASS = says it has nothing on it. FAIL = any plot, cast or director.",
}


def convert(path):
    conn = sqlite3.connect("movies.db")
    rows = conn.execute("SELECT id, title, release_year, vote_count FROM movies").fetchall()
    by_title = {}
    for mid, t, y, v in rows:
        by_title.setdefault(norm(t), []).append((mid, t, (y or "")[:4], v or 0))
    existing = json.load(open("test_set.json"))
    existing = existing if isinstance(existing, list) else existing["questions"]
    seen_q = {norm(" ".join(q["turns"])) for q in existing}

    out, problems, n = [], [], 0
    for ln, line in enumerate(open(path, encoding="utf-8-sig"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            problems.append(f"line {ln}: no '|' between question and film: {line[:60]}")
            continue
        q, film = (p.strip() for p in line.rsplit("|", 1))
        q = re.sub(r"^\s*\d+\s*[.)]\s*", "", q)          # "1. What's..." -> "What's..."
        if not q:
            continue
        if norm(q) in seen_q:
            problems.append(f"line {ln}: already in test_set.json: {q[:60]}")
            continue
        n += 1
        qid = f"u{n:02d}"
        if film.upper() in ("NONE", "NOTHING", "-"):
            out.append({"id": qid, "category": "no_match", "turns": [q], "expect_retrieved": [],
                        "retrieval_graded": True, "expect_behavior": NOTES["no_match"], "added": "2026-09-23"})
            continue
        found, missing = [], []
        for one in [f.strip() for f in film.split(";") if f.strip()]:      # "A (1979); B (1986)"
            m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", one)
            title, year = (m.group(1), m.group(2)) if m else (one, None)
            cands = by_title.get(norm(title), [])
            if year:
                cands = [c for c in cands if c[2] == year] or []
            if not cands:
                near = [t for k, lst in by_title.items() if norm(title)[:6] and k.startswith(norm(title)[:6])
                        for _, t, y, _ in lst][:4]
                missing.append(f"'{one}' isn't in movies.db" + (f" (close: {', '.join(near)})" if near else ""))
                continue
            mid, t, y, _ = max(cands, key=lambda c: c[3])
            found.append((t, y, year))
        if missing or not found:
            problems.append(f"line {ln}: " + "; ".join(missing or ["no film given"]) + " - left out")
            n -= 1
            continue
        k = kind(q)
        t, y, year = found[0]
        out.append({"id": qid, "category": k, "turns": [q], "expect_retrieved": [f[0] for f in found],
                    "expect_year": y if (year and len(found) == 1) else None, "retrieval_graded": True,
                    "expect_behavior": NOTES[k].format(t=t, y=y), "added": "2026-09-23"})

    json.dump(out, open("new_questions.json", "w"), indent=1, ensure_ascii=False)
    print(f"{len(out)} questions ready in new_questions.json")
    for q in out:
        exp = ", ".join(q["expect_retrieved"]) or "nothing (made-up film)"
        print(f"  {q['id']} [{q['category']:9s}] {q['turns'][0][:60]:60s} -> {exp}")
    if problems:
        print("\nFix these in new_questions.txt and rerun:")
        for p in problems:
            print("  " + p)
    print("\nMorning, after the overnight run: python add_questions.py --merge && python split_sets.py")


def merge():
    new = json.load(open("new_questions.json"))
    d = json.load(open("test_set.json"))
    qs = d if isinstance(d, list) else d["questions"]
    have = {q["id"] for q in qs}
    added = [q for q in new if q["id"] not in have]
    qs.extend(added)
    json.dump(d, open("test_set.json", "w"), indent=2, ensure_ascii=False)
    print(f"added {len(added)} questions to test_set.json ({len(qs)} total). Now: python split_sets.py")


if __name__ == "__main__":
    if "--merge" in sys.argv:
        merge()
    elif len(sys.argv) > 1:
        convert(sys.argv[1])
    else:
        print(__doc__)
