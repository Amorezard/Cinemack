"""
v3 conversation memory (CONV=1): what the planner knows about the conversation so far.

v2 gave the planner one line, "Recent films: 1. A  2. B  3. C" - the previous turn's top
three search results. It never saw earlier questions, earlier films or the answer, so
"no, the one from earlier", "compare that with Barbie" and "what about their budgets?"
could not be resolved (1 Oct demo). This module keeps a small, bounded state and turns it
into a few lines of planner input.

Layers (the brief's sections 12-13), each with a hard size cap so prompts stay small:
    turns      the last MAX_TURNS exchanges: the user's words (verbatim, clipped), the films
               the answer talked about (in the order it mentioned them), the start of the
               last answer only
    entities   every film the conversation touched, in first-mention order, with the turns
               it came up in and how (named by the user / proposed by the answer /
               rejected by the user) - MAX_ENTITIES, oldest dropped first
    summary    one line for turns that fell out of `turns` (rule-based: no model call)
    prefs      durable taste statements ("I hate jump scares"), from the planner's
               `preferences` field; used by recommendations (personal.py), never as facts

The state is plain JSON. The website keeps it in the page and sends it back with each
question (no server-side session store), the terminal chat keeps it in memory, and the
evaluation scripts build it from scripted conversations.

    state = conv.new()
    plan_text = conv.planner_block(state)       # -> retrieve.understand()
    state = conv.update(state, question, hits, plan=notes_plan, answer=answer_text)
"""
import json
import re
import unicodedata

VERSION = 1
MAX_TURNS = 4            # verbatim turns shown to the planner
MAX_KEEP_TURNS = 12      # turns kept in the state (older ones fold into the summary)
MAX_ENTITIES = 12
Q_CHARS = 220            # each earlier question, clipped
A_CHARS = 220            # the last answer's opening, clipped


def new():
    return {"v": VERSION, "turns": [], "entities": [], "summary": "", "prefs": []}


def coerce(state):
    """Accept None, a JSON string or a dict from an untrusted client; always return a
    well-formed state (unknown keys dropped, sizes capped, types checked)."""
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            state = None
    if not isinstance(state, dict):
        return new()
    out = new()
    for t in (state.get("turns") or [])[-MAX_KEEP_TURNS:]:
        if isinstance(t, dict) and isinstance(t.get("q"), str):
            out["turns"].append({"q": t["q"][:600],
                                 "films": [str(f)[:120] for f in (t.get("films") or [])[:10]],
                                 "a": str(t.get("a") or "")[:A_CHARS],
                                 "intent": str(t.get("intent") or "")[:20]})
    for e in (state.get("entities") or [])[-MAX_ENTITIES:]:
        if isinstance(e, dict) and isinstance(e.get("title"), str):
            out["entities"].append({"title": e["title"][:120], "year": str(e.get("year") or "")[:4],
                                    "id": e.get("id") if isinstance(e.get("id"), int) else None,
                                    "turns": [int(x) for x in (e.get("turns") or []) if isinstance(x, int)][-6:],
                                    "how": str(e.get("how") or "")[:20]})
    out["summary"] = str(state.get("summary") or "")[:400]
    if isinstance(state.get("_dropped"), int) and state["_dropped"] > 0:
        out["_dropped"] = state["_dropped"]
    for p in (state.get("prefs") or [])[-10:]:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            out["prefs"].append({"text": p["text"][:120], "polarity": 1 if p.get("polarity", 1) >= 0 else -1})
    return out


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return " ".join("".join(c if c.isalnum() else " " for c in s.casefold()
                            if not unicodedata.combining(c)).split())


def label(title, year):
    return f"{title} ({year})" if year else title


def recent_titles(state):
    """The films of the most recent turn that had any, in the order the answer mentioned
    them - the same list v2 called "recent titles"."""
    for t in reversed(state.get("turns") or []):
        if t.get("films"):
            return [re.sub(r"\s*\(\d{4}\)$", "", f) for f in t["films"]]
    return []


def entity_titles(state):
    return [e["title"] for e in state.get("entities") or []]


def is_empty(state):
    return not (state and (state.get("turns") or state.get("entities")))


def planner_block(state):
    """The conversation, as the planner reads it. '' for a new conversation, so first
    questions get exactly the v2 planner prompt (and the v2 results)."""
    if is_empty(state):
        return ""
    turns = state["turns"]
    first_no = len(turns) - min(len(turns), MAX_TURNS) + 1 + _offset(state)
    lines = []
    if state.get("summary"):
        lines.append(f"Earlier in the conversation: {state['summary']}")
    shown = turns[-MAX_TURNS:]
    for i, t in enumerate(shown):
        n = first_no + i
        q = t["q"] if len(t["q"]) <= Q_CHARS else t["q"][:Q_CHARS].rsplit(" ", 1)[0] + " ..."
        lines.append(f"Turn {n}. User: {q}")
        if t.get("films"):
            lines.append(f"  Films in the answer: {', '.join(t['films'])}")
        if i == len(shown) - 1 and t.get("a"):
            lines.append(f'  The answer began: "{t["a"]}"')
    ents = state.get("entities") or []
    if ents:
        def tag(e):
            bits = [f"turn {','.join(map(str, e['turns']))}"] if e.get("turns") else []
            if e.get("how") == "rejected":
                bits.append("user said it is NOT the film")
            return f"{label(e['title'], e['year'])} [{'; '.join(bits)}]" if bits else label(e["title"], e["year"])
        lines.append("Films mentioned so far, in order: " +
                     "  ".join(f"{i}. {tag(e)}" for i, e in enumerate(ents, 1)))
    if state.get("prefs"):
        lines.append("User's stated tastes: " + "; ".join(
            ("likes " if p["polarity"] > 0 else "dislikes ") + p["text"] for p in state["prefs"]))
    return "\n".join(lines)


def _offset(state):
    """Turn numbers keep counting after old turns are dropped from the state."""
    return int(state.get("_dropped", 0)) if isinstance(state.get("_dropped"), int) else 0


def _mentions(answer, title):
    if not answer or not title:
        return -1
    text = f" {_norm(answer)} "
    return text.find(f" {_norm(title)} ")


def update(state, question, hits, plan=None, answer=None, k=3):
    """The state after one exchange. hits: what retrieve() returned (dicts with title,
    release_year, id). plan: retrieve's notes["plan"] (or None). answer: the answer text,
    if one was generated - films it names move to the front, in the order named."""
    state = coerce(state)
    turn_no = len(state["turns"]) + 1 + _offset(state)
    films = []
    for h in (hits or [])[:max(k, 3)]:
        if h.get("title") and all(f[0] != h["title"] for f in films):
            films.append((h["title"], str(h.get("release_year") or "")[:4], h.get("id")))
    if answer:
        pos = {f[0]: _mentions(answer, f[0]) for f in films}
        named = sorted((f for f in films if pos[f[0]] >= 0), key=lambda f: pos[f[0]])
        films = named + [f for f in films if pos[f[0]] < 0]
        talked = named or films[:1]
    else:
        talked = films[:1] if (plan or {}).get("intent") == "identify" else films
    a = ""
    if answer:
        a = " ".join(answer.split())
        a = a if len(a) <= A_CHARS else a[:A_CHARS].rsplit(" ", 1)[0] + " ..."
    state["turns"].append({"q": question[:600], "films": [label(t, y) for t, y, _ in films],
                           "a": a, "intent": str((plan or {}).get("intent") or "")})
    rejected = {_norm(t) for t in (plan or {}).get("rejected") or []}
    named_by_user = {_norm(t) for t in (plan or {}).get("films") or []}

    def touch(title, year, mid, how):
        nt = _norm(title)
        for e in state["entities"]:
            if _norm(e["title"]) == nt and (not year or not e["year"] or e["year"] == year):
                if turn_no not in e["turns"]:
                    e["turns"].append(turn_no)
                if how == "rejected" or e.get("how") != "rejected":
                    e["how"] = how
                e["year"] = e["year"] or year
                e["id"] = e["id"] or mid
                return
        state["entities"].append({"title": title, "year": year, "id": mid, "turns": [turn_no], "how": how})

    for t, y, mid in films:
        if _norm(t) in named_by_user:
            touch(t, y, mid, "named")
    for t, y, mid in talked:
        touch(t, y, mid, "named" if _norm(t) in named_by_user else "answer")
    for t in (plan or {}).get("rejected") or []:
        touch(t, "", None, "rejected")
    for e in state["entities"]:
        if _norm(e["title"]) in rejected:
            e["how"] = "rejected"
    for p in (plan or {}).get("preferences") or []:
        if isinstance(p, dict) and p.get("text") and all(q["text"] != p["text"] for q in state["prefs"]):
            state["prefs"].append({"text": str(p["text"])[:120],
                                   "polarity": 1 if p.get("polarity", 1) >= 0 else -1})
    # Bound the state: old turns fold into a one-line summary, old entities drop off.
    while len(state["turns"]) > MAX_KEEP_TURNS:
        old = state["turns"].pop(0)
        state["_dropped"] = _offset(state) + 1
        if old.get("films"):
            bit = f"asked \"{old['q'][:60]}\" ({', '.join(old['films'][:2])})"
            state["summary"] = (state["summary"] + "; " + bit).strip("; ")[-400:]
    state["entities"] = state["entities"][-MAX_ENTITIES:]
    state["prefs"] = state["prefs"][-10:]
    return state


def digest(state):
    """Stable text for cache keys: everything the planner would see."""
    return planner_block(coerce(state))
