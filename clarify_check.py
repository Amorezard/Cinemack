#!/usr/bin/env python3
"""
3 Nov: which questions get "Did you mean the film or the show?" choices (CLARIFY=1, needs TV=1). No answers are
generated; prints the route and the choices for a fixed list written before any result.

    TV=1 CLARIFY=1 python clarify_check.py
Expect choices only for the "both" lines (titles that are a well-known film AND a well-known show, question says
neither); none for the rest.
"""
import contextlib
import io
import os

os.environ.setdefault("TV", "1")
os.environ.setdefault("CLARIFY", "1")
import retrieve as R  # noqa: E402

CASES = [
    ("both", "Who stars in Fargo?"),
    ("both", "When did Shameless come out?"),
    ("both", "Who directed Westworld?"),
    ("both", "What is Snowpiercer about?"),
    ("film", "Who stars in the film Fargo?"),
    ("film", "Who directed the 1996 movie Fargo?"),
    ("tv", "How many seasons does the TV series Fargo have?"),
    ("tv", "Who created Breaking Bad?"),
    ("film", "Who directed Heat?"),
    ("film", "What is Inception about?"),
    ("film", "Who stars in Dune?"),
    ("click", "Who stars in Fargo? (I mean the TV series Fargo)"),
    ("click", "Who stars in Fargo? (I mean the 1996 film Fargo)"),
]


def main():
    ok = 0
    for kind, q in CASES:
        with contextlib.redirect_stdout(io.StringIO()):
            hits = R.retrieve(q, k=3)
        h = hits[0] if hits else {}
        ch = h.get("_choices")
        route = "tv" if h.get("_tv") else ("film" if hits else "none")
        good = bool(ch) == (kind == "both")
        ok += good
        print(f"  {'ok ' if good else 'BAD'} [{kind:5s}] {q[:55]:55s} -> {route:4s} {h.get('title', '-')!s:22.22s} "
              f"choices: {[c['label'] for c in ch] if ch else '-'}")
    print(f"clarify check: {ok}/{len(CASES)} as expected (reported only; the rule is the TV set and analytic)")


if __name__ == "__main__":
    main()
