"""
Follow-up fix: "can you tell me how the first one ended" after talking about
Back to the Future retrieved How It Ends (2018).

Cause: resolve_followup() only fires on a pronoun (it / that / the one ...).
"the FIRST one" has no pronoun, so the question was embedded on its own and
"ended" matched How It Ends.

Changes
  retrieve.py  - REFERENCE_RE: "the first one", "the sequel", "part 2",
                 "the ending", "the other one" also count as follow-ups.
               - pick_subject(): ordinals choose among the recent titles.
                 A series (Back to the Future / Part II / Part III) is ordered
                 by release year, so "the first one" = the original and
                 "the second one" = Part II. Otherwise, the order the last
                 answer mentioned them.
               - recent_titles_from(hits, answer): recent titles in the order
                 the answer talked about them, so "tell me more about the
                 second one" after a recommendation list means the second film
                 the answer discussed, not the second retrieval hit.
  chat.py      - uses recent_titles_from() after each answer.
  run_eval.py  - same, so the eval keeps measuring what chat.py does.

Run from ~/movie-buff/rag:   python patch_followup.py
Backups: *.bak_followup. Refuses to run twice. Then run quick_check.py.
"""

import shutil
import sys
from pathlib import Path

MARKER = "REFERENCE_RE"

RETRIEVE_EDITS = [
    (
        'r"the film|the movie|the one)\\b", re.I)\n',
        'r"the film|the movie|the one)\\b", re.I)\n'
        '''
# Follow-ups don't always use a pronoun. "How did the first one end?", "what
# about the sequel?", "is part 2 any good?" point back at the last answer just
# as clearly - and embedded on their own they retrieve whatever shares their
# words ("how the first one ended" -> How It Ends, 2018).
REFERENCE_RE = re.compile(
    r"\\b(?:the\\s+)?(?:first|second|third|fourth|last|latest|final|original|other)\\s+"
    r"(?:one|part|installment|entry)s?\\b"
    # "the first movie" - but not "the first movie ever made" / "by Nolan"
    r"|\\bthe\\s+(?:first|second|third|original|other)\\s+(?:film|movie)\\b"
    r"(?!\\s+(?:ever|of|by|from|in|about|that|where|with|to|made)\\b)"
    r"|\\bthe\\s+(?:original|sequel|prequel|remake|ending|end)\\b"
    r"|\\bthe\\s+(?:first|second|third|last)\\b(?=\\s*(?:[?.!,]|$))"
    r"|\\bpart\\s+(?:one|two|three|four|ii|iii|iv|[1-6])\\b", re.I)

# Which of the recent titles an ordinal points at. -1 = the last one.
ORDINAL_INDEX = {
    "first": 0, "original": 0, "one": 0, "1": 0,
    "second": 1, "other": 1, "sequel": 1, "two": 1, "ii": 1, "2": 1,
    "third": 2, "three": 2, "iii": 2, "3": 2,
    "fourth": 3, "four": 3, "iv": 3, "4": 3, "5": 4, "6": 5,
    "last": -1, "latest": -1, "final": -1,
}
''',
    ),
    (
        '''    if not PRONOUN_RE.search(question):
        return question, None
    if titles_in(question, conn):
        return question, None  # names its own subject; leave it alone
    subject = recent_titles[0]
    return f"{question.strip()} ({subject})", subject
''',
        '''    if not (PRONOUN_RE.search(question) or REFERENCE_RE.search(question)):
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
''',
    ),
]

CHAT_EDITS = [
    ("from retrieve import retrieve\n",
     "from retrieve import retrieve, recent_titles_from\n"),
    ("        answer = ask(history, thinking_on)\n",
     "        answer = ask(history, thinking_on)\n"
     "        if hits:\n"
     "            # In the order the answer talked about them - see recent_titles_from.\n"
     "            recent_titles = recent_titles_from(hits, answer)\n"),
]

EVAL_EDITS = [
    ("from retrieve import retrieve as _retrieve\n",
     "from retrieve import retrieve as _retrieve, recent_titles_from\n"),
    ("            out = ask(turn_text, hits, history,\n"
     "                      think=not args.no_think, num_predict=args.num_predict)\n",
     "            out = ask(turn_text, hits, history,\n"
     "                      think=not args.no_think, num_predict=args.num_predict)\n"
     "            if hits:\n"
     "                # Same as chat.py: follow-ups resolve in answer order.\n"
     "                recent_titles = recent_titles_from(hits, out[\"answer\"])\n"),
]

PLAN = [("retrieve.py", RETRIEVE_EDITS), ("chat.py", CHAT_EDITS), ("run_eval.py", EVAL_EDITS)]


def main():
    here = Path(__file__).resolve().parent
    if MARKER in (here / "retrieve.py").read_text():
        sys.exit("Already applied (retrieve.py has REFERENCE_RE). Nothing changed.")

    # Check every anchor in every file before writing anything.
    new_text = {}
    for name, edits in PLAN:
        path = here / name
        if not path.exists():
            sys.exit(f"{name} not found in {here}. Run this from ~/movie-buff/rag. Nothing changed.")
        text = path.read_text()
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                sys.exit(f"{name}: expected 1 match for\n{old!r}\nfound {n}. Nothing changed.")
            text = text.replace(old, new)
        new_text[name] = text

    for name, _ in PLAN:
        path = here / name
        shutil.copy2(path, path.with_name(name + ".bak_followup"))
        path.write_text(new_text[name])
        print(f"patched {name}  (backup: {name}.bak_followup)")
    print("\nNext: python quick_check.py   - retrieval should still pass as before.")


if __name__ == "__main__":
    main()
