"""
A/B test of query understanding (patch_understand.py): old title matcher vs.
the LLM plan, on the same questions, retrieval only (no answers generated).

    cd ~/movie-buff/rag && source ~/venvs/movie-rag/bin/activate
    python test_understand.py            # everything (~10 min)
    python test_understand.py --fresh    # only the new questions (~2 min)

Three groups:
  TEST SET   every single-turn auto-graded question in test_set.json (what
             quick_check.py runs).
  FOLLOW-UP  multi-turn conversations. Turn 1 is retrieved, its titles become
             the recent titles, then turn 2 is graded.
  FRESH      questions nothing has been tuned on. Most are plot descriptions
             that contain phrases which are ALSO film titles ("boarding
             school", "last night", "bachelor party", "one night", "seize the
             day") - the q42/q43 trap - plus a typo, a named film whose title
             is ordinary words, and an invented film. Run them once and report
             the result; if you change code because of one, it stops counting
             as fresh.

Pass = every expected film is in the returned hits (same rule as
quick_check.py). expect [] = nothing should come back (honesty cases).
Writes results/understand_ab.json.
"""

import contextlib
import io
import json
import os
import sys
import time
import unicodedata

import retrieve as R

FOLLOWUPS = [
    {"id": "f1", "turns": ["Tell me about Back to the Future.",
                           "Can you tell me how the first one ended?"],
     "expect": ["Back to the Future"]},
    {"id": "f2", "turns": ["Tell me about The Godfather.",
                           "What about the sequel?"],
     "expect": ["The Godfather Part II"]},
    {"id": "f3", "turns": ["Tell me about The Departed.", "Who directed it?"],
     "expect": ["The Departed"]},
    {"id": "f4", "turns": ["What's Toy Story about?", "Is the third one any good?"],
     "expect": ["Toy Story 3"]},
]

FRESH = [
    {"id": "n1", "q": "What's the movie where an English teacher at a strict boarding school "
                      "inspires his students to seize the day?",
     "expect": ["Dead Poets Society"]},
    {"id": "n2", "q": "There's a film where three friends wake up in Las Vegas after a bachelor "
                      "party with no memory of last night and the groom is missing.",
     "expect": ["The Hangover"]},
    {"id": "n3", "q": "A movie where a New York cop visits his wife's office Christmas party and "
                      "spends one night fighting terrorists alone in the tower.",
     "expect": ["Die Hard"]},
    {"id": "n4", "q": "The film where a kid makes a wish at a carnival machine and wakes up the "
                      "next day as a grown man.",
     "expect": ["Big"]},
    {"id": "n5", "q": "What's that movie about a man who gets stuck living in an airport "
                      "terminal because his home country collapsed?",
     "expect": ["The Terminal"]},
    {"id": "n6", "q": "A movie where a black man meets his white girlfriend's family for the "
                      "first time and the weekend turns sinister.",
     "expect": ["Get Out"]},
    {"id": "n7", "q": "Who starred in Gladiater?",                       # typo on purpose
     "expect": ["Gladiator"]},
    {"id": "n8", "q": "Who directed The Other Guys?",                    # title = ordinary words
     "expect": ["The Other Guys"]},
    {"id": "n9", "q": "Is Stand by Me a good movie to watch with my kids?",
     "expect": ["Stand by Me"]},
    {"id": "n10", "q": "What can you tell me about the 2003 film Harbor of Glass, "
                       "starring Mira Castellane?",                      # invented
     "expect": []},
]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum() and not unicodedata.combining(c))


def run(question, recent=None):
    """Retrieve quietly; return (titles, years, notes, seconds)."""
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        hits = R.retrieve(question, k=3, recent_titles=recent, explain=True)
    secs = time.perf_counter() - t0
    notes = hits[0].get("_notes", {}) if hits else {}
    return ([h["title"] for h in hits],
            [str(h.get("release_year") or "")[:4] for h in hits], notes, secs)


def graded(titles, years, expect, expect_year=None):
    if not expect:
        return not titles
    # 16 Oct: compare years as 4-character text on both sides, so an expect_year written as a
    # number (1991) or text ("1991") grades the same (the 15 Oct nickname runs failed on this).
    ey = str(expect_year)[:4] if expect_year else None
    got = list(zip(map(norm, titles), (str(y or "")[:4] for y in years)))
    return all(any(t == norm(e) and (not ey or y == ey) for t, y in got)
               for e in expect)


def read_as(notes):
    p = notes.get("plan")
    if not p:
        return "(regex path)"
    films = ", ".join(p.get("films") or [])
    return p["intent"] + (f": {films}" if films else "")


def cases(fresh_only):
    out = []
    if not fresh_only:
        d = json.load(open("test_set.json"))
        for q in (d if isinstance(d, list) else d["questions"]):
            if q.get("expect_retrieved") and len(q["turns"]) == 1:
                out.append(("TEST SET", q["id"], [q["turns"][0]], q["expect_retrieved"],
                            q.get("expect_year")))
        for f in FOLLOWUPS:
            out.append(("FOLLOW-UP", f["id"], f["turns"], f["expect"], None))
    for f in FRESH:
        out.append(("FRESH", f["id"], [f["q"]], f["expect"], None))
    return out


def evaluate(turns, expect, expect_year):
    recent = None
    for q in turns[:-1]:                     # earlier turns set the conversation
        titles, _, _, _ = run(q, recent)
        recent = titles or recent
    titles, years, notes, secs = run(turns[-1], recent)
    return {"pass": graded(titles, years, expect, expect_year), "hits": titles,
            "read_as": read_as(notes), "secs": round(secs, 2)}


def main():
    fresh_only = "--fresh" in sys.argv
    if not hasattr(R, "USE_QUERY_LLM"):
        sys.exit("retrieve.py has no query understanding yet - run patch_understand.py first.")
    todo = cases(fresh_only)
    print(f"{len(todo)} cases, each run with the old matcher and with the LLM plan.\n")

    with contextlib.redirect_stdout(io.StringIO()):     # load models before timing
        R.retrieve("warm up", k=3)

    rows = []
    for group, qid, turns, expect, ey in todo:
        R.USE_QUERY_LLM = False
        old = evaluate(turns, expect, ey)
        R.USE_QUERY_LLM = True
        new = evaluate(turns, expect, ey)
        rows.append({"group": group, "id": qid, "question": turns[-1],
                     "expect": expect, "old": old, "new": new})
        mark = lambda r: "PASS" if r["pass"] else "FAIL"
        flag = "   <- fixed" if new["pass"] and not old["pass"] else (
               "   <- BROKE" if old["pass"] and not new["pass"] else "")
        print(f"{group:9s} {qid:4s} old {mark(old)}  new {mark(new)}{flag}")
        if not (old["pass"] and new["pass"]):
            print(f"      Q:        {turns[-1][:90]}")
            print(f"      expect:   {expect or 'nothing (honesty case)'}")
            print(f"      old hits: {', '.join(old['hits'])[:90] or '(none)'}")
            print(f"      new hits: {', '.join(new['hits'])[:90] or '(none)'}")
            print(f"      read as:  {new['read_as']}")

    print("\n" + "-" * 60)
    for group in ("TEST SET", "FOLLOW-UP", "FRESH"):
        g = [r for r in rows if r["group"] == group]
        if not g:
            continue
        o = sum(r["old"]["pass"] for r in g)
        n = sum(r["new"]["pass"] for r in g)
        print(f"{group:10s} old {o}/{len(g)}   new {n}/{len(g)}")
    to = sum(r["old"]["secs"] for r in rows) / len(rows)
    tn = sum(r["new"]["secs"] for r in rows) / len(rows)
    print(f"\nAverage retrieval time: old {to:.1f}s   new {tn:.1f}s   (+{tn - to:.1f}s)")
    broke = [r["id"] for r in rows if r["old"]["pass"] and not r["new"]["pass"]]
    fixed = [r["id"] for r in rows if r["new"]["pass"] and not r["old"]["pass"]]
    print(f"Fixed: {', '.join(fixed) or 'none'}")
    print(f"Broke: {', '.join(broke) or 'none'}")

    os.makedirs("results", exist_ok=True)
    with open("results/understand_ab.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("\nFull results: results/understand_ab.json")


if __name__ == "__main__":
    main()
