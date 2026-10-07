import sys
import time

import ollama
from retrieve import retrieve, recent_titles_from, llm_options
import retrieve as _R
import os as _os_c

# Model names come from config.yaml / the shell (see mbconfig.py); defaults unchanged.
MODEL = _os_c.environ.get("ANSWER_MODEL", "movie-buff")
EMBED_MODEL = _R.EMBED_MODEL

# Holds both models in VRAM for the length of a session without touching the
# shared Ollama service's global config. Each request refreshes the timer, so
# the GPU is released ~30 min after you stop using it rather than being pinned
# for everyone else on the box.
KEEP_ALIVE = _os_c.environ.get("KEEP_ALIVE", "30m")

# Change 15 switch: ANSWER_STYLE=1 picks the answer instructions by question
# type (answer_style.py). Off until the judge shows it helps.
import os as _os
ANSWER_STYLE = _os.environ.get("ANSWER_STYLE", "0") == "1"


import sqlite3
_CTX_DB = sqlite3.connect("movies.db", check_same_thread=False)

def context_for(h):
    """What the LLM sees. Separate from embed_text on purpose: the embedding
    holds what people search BY, the prompt holds what the model needs to
    ANSWER - so ratings go in, keywords stay out, and cast is capped at 4."""
    row = None if h.get("_facts_only") else _CTX_DB.execute(
        "SELECT title, release_year, genres, director, top_cast, overview, "
        "vote_average, vote_count FROM movies WHERE id = ?", (h["id"],)).fetchone()
    if row is None:
        # v3: a film only facts.db has (released after the catalogue was built).
        import tools as _T
        return _T.film_card(h)
    title, year, genres, director, cast, overview, va, vc = row
    lines = [f"{title} ({year or 'unknown year'})"]
    if genres:
        lines.append(f"Genres: {genres}")
    if director:
        lines.append(f"Director: {director}")
    if cast:
        lines.append("Starring: " + ", ".join(cast.split(", ")[:4]))
    if va and (vc or 0) >= 50:
        lines.append(f"TMDB user rating: {va:.1f}/10 from {vc:,} votes")
    if overview:
        lines.append(f"Plot: {overview}")
    try:
        ws = _CTX_DB.execute("SELECT wiki_summary FROM movies WHERE id = ?", (h["id"],)).fetchone()
    except sqlite3.OperationalError:
        ws = None
    if ws and ws[0]:
        lines.append(f"Summary: {ws[0][:500]}")
    if ANSWER_WIKI:
        # v2: Wikipedia text found by retrieve() - the intro (who made it, what it
        # is) and the plot passage closest to the question (the scene they recall).
        intro, passage = h.get("wiki_intro") or "", h.get("wiki_passage") or ""
        if intro:
            lines.append(f"Wikipedia intro: {_clip(intro, ANSWER_WIKI_CHARS)}")
        if passage and passage not in intro:
            lines.append(f"Wikipedia ({h.get('wiki_section') or 'plot'}): {_clip(passage, ANSWER_WIKI_CHARS)}")
    return "\n".join(lines)


# v2 switch: ANSWER_WIKI=1 (default) adds the Wikipedia intro + matched passage to each
# film's context (up to ANSWER_WIKI_CHARS each). Judge, 28 Sep (E5, 56 own questions):
# pass 40 -> 44, grounded 4.20 -> 4.21, direct 4.96 -> 5.00, honest 4.89 -> 5.00,
# specific 4.55 -> 4.04, concise 3.73 -> 3.57. ANSWER_WIKI=0 turns it off.
ANSWER_WIKI = _os.environ.get("ANSWER_WIKI", "1") == "1"
ANSWER_WIKI_CHARS = int(_os.environ.get("ANSWER_WIKI_CHARS", "700"))


def _clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


# 29 Sep: ANSWER_STRICT=0 (default) drops the anti-hallucination instructions: "say so
# honestly instead of guessing", "only state a director/cast/award ... that appears above,
# otherwise say you don't have it" and "if you are unsure, say so". They cost answer tokens
# (hedges, "you may want to verify" lines) for little gain in this project, and a wrong
# detail is acceptable. The answer still leads with the database's films, and the site's
# rule-based fact-check line still runs. The "found nothing" prompt is unchanged.
# ANSWER_STRICT=1 restores the old prompt exactly (for judge comparisons).
ANSWER_STRICT = _os.environ.get("ANSWER_STRICT", "0") == "1"

HOW_TO_ANSWER = ("How to answer: lead with the direct answer in your first sentence - if the question "
                 "asks which film, name it with its year. If several films are listed, answer about the "
                 "one that best fits the question and leave out the others unless they help. Back the "
                 "answer with one or two specific details (a plot point, the director, the rating) rather "
                 "than general praise. For recommendations, suggest two or three films from the "
                 "information above, each with a one-line reason tied to what the person asked for. Only "
                 "give a verdict or recommendation when the question asks for one. When asked whether a "
                 "film is good or worth watching, give a clear verdict first and say who would enjoy it, "
                 "using its rating and plot. Stay on what was asked: no awards, sequels or source novels "
                 "unless the question is about them.")
NO_HEDGING = ("No disclaimers: don't say you are unsure, don't suggest checking or verifying anything, "
              "and never reason out loud or correct yourself mid-answer.")


# ------------------------------------------------------------------ v3 answers (TOOLS=1)
# tools.py adds structured data (facts, comparisons, market context, Wikipedia sections,
# review analysis, rankings, current releases) as tagged blocks after the film records.
# Analysis questions ("why did X work", reviews, rankings) get ANALYSIS_HOW instead of
# HOW_TO_ANSWER + NO_HEDGING: evidence and interpretation kept apart, sources named,
# uncertainty stated - the opposite of v2's "no disclaimers", on purpose, for these questions only.
# TOOLS=0 = the v2 prompt exactly.
TOOLS = _os.environ.get("TOOLS", "0") == "1"
PROMPT_VERSION = "v3.1" if TOOLS else "v2"   # v3.1 (6 Oct): fact / ranking / reviews styles
LAST_TOOL = {}       # the last build_prompt's tool result (route, timings) for logs and evals

DATA_NOTE = ("Lines in the structured data end with their source in brackets. When you use a number "
             "from them, name the source briefly (for example \"per TMDB\"). If two sources disagree, "
             "say so and give both. For recommendations with 'why these may suit this person' notes, "
             "recommend from the top of that list and give each pick's reason in your own words.")
ANALYSIS_HOW = """How to answer: this asks for analysis, not just a fact. Use short bold headings, only the ones the data above supports:
**What happened** - the measurable outcome: gross against budget, ratings, awards, with the numbers above.
**What audiences liked / disliked** - from the audience review analysis, if present.
**What critics said** - from the reception text, if present.
**Market context** - release timing and competition, genre, franchise and director track record from the data above.
**Why it likely worked** (or **why it likely struggled**) - your interpretation, tied to the evidence above.
**Uncertainty** - one or two sentences on what the data cannot show (it shows correlation, not cause; missing or conflicting figures).
Put the source in brackets after every number or reported claim, e.g. [TMDB], [Wikipedia: Box office], [TMDB user reviews]. Mark your own reasoning as interpretation ("this suggests", "likely"); never present it as fact. Use only figures that appear above; if a figure is missing, say it is not in the data. For rankings and trends, lead with the answer, then the table's key rows, then caveats. Keep it under about 350 words."""
# 6 Oct: separate, shorter instructions for rankings, reception questions and single facts
# (tools.run sets the style). ANALYSIS_HOW stays for "why" questions, careers and trends.
FACT_HOW = ("How to answer: this is a factual question. Answer it in one or two sentences: the fact asked for, "
            "with its source (for example \"per TMDB\"). Give only what was asked - no reasons, opinions, "
            "comparisons, box-office figures or other details the question didn't ask for, and nothing that is "
            "not in the information above. If two sources disagree, give both.")
RANKING_HOW = ("How to answer: this asks for a ranking from the table above. Start with a one-sentence answer, then "
               "list the top entries in the table's order (as many as the question asks for, otherwise up to 10), one "
               "line each with the figures that matter for the question (year, gross, budget, gross/budget) and the "
               "source in brackets. Then at most two sentences of caveats, e.g. that the ranking only covers films "
               "with figures in Movie Buff's database. Name only films and people that appear in the table - never "
               "add others from memory. No other headings, and no reasons why films succeeded or market analysis "
               "unless the question asks why. Keep it under about 200 words.")
REVIEWS_HOW = ("How to answer: say what the question asks about the film's reception. Keep the sources apart: critics "
               "come from the Wikipedia reception text; TMDB user reviews are ordinary viewers, so call them audience "
               "or user reviews, never critics. Give specific points (what was praised or criticised) with the source "
               "in brackets, quoting short phrases where the data has them. No box-office analysis, market context or "
               "explanation of why the film succeeded unless the question asks for it, and mark any interpretation of "
               "your own as such. Keep it under about 250 words.")
CURRENT_HOW = ("How to answer: list the films from the current/upcoming data that fit the question, each with "
               "its date and one line on what it is. Say the list comes from TMDB as of the date shown and that "
               "release dates can still change. Don't add films that are not in the list.")


def _v3_prompt(question, hits, tool, amb_line):
    context = "\n\n---\n\n".join(context_for(h) for h in hits)
    blocks = "\n\n".join(b for _k, b in tool.get("blocks") or [])
    parts = []
    if context:
        parts.append(f"Here is information on movies that might be relevant to the question:\n\n{context}")
    if blocks:
        parts.append(f"Structured data from Movie Buff's database (sources in brackets):\n\n{blocks}")
    style = tool.get("style")
    styled = {"analysis": ANALYSIS_HOW, "current": CURRENT_HOW, "ranking": RANKING_HOW, "reviews": REVIEWS_HOW,
              "fact": FACT_HOW}
    how = styled.get(style) or (f"{HOW_TO_ANSWER}\n\n{DATA_NOTE}\n\n{NO_HEDGING}" if blocks
                                else f"{HOW_TO_ANSWER}\n\n{NO_HEDGING}")
    return "\n\n".join(parts) + f"""

Question: {question}

Answer using the information above.{amb_line}

{how}"""


def _personalize(question, hits, plan, conv, user, tool):
    """v3 recommendations: drop films the question says the person disliked, then (with a
    taste profile, stated tastes or a liked/disliked film in the question) re-rank for this
    person and add a 'why it fits you' block. Returns the new hits."""
    import personal as _P
    import tools as _T
    neg = _P.disliked_in(question)
    nneg = {_R.normalize(t) for t in neg}
    if nneg:
        hits = [h for h in hits if _R.normalize(h["title"]) not in nneg]
    likes, dis, stated = _P.load(user) if (_P.PERSONAL and user) else ([], [], [])
    likes, dis = list(likes), list(dis)
    stated = list(stated) + [(p["text"], p["polarity"]) for p in (conv or {}).get("prefs") or []]
    for t in (plan or {}).get("films") or []:            # "if I liked Arrival ..."
        if _R.normalize(t) not in nneg:
            likes += [h["id"] for h in _T.facts_hits([t])]
    for t in neg:
        dis += [h["id"] for h in _T.facts_hits([t])]
    if not (likes or dis or stated) or not hits:
        return hits
    ranked, _notes = _P.rerank(hits, likes, dis, stated, k=len(hits))
    lines = [f"{h['title']} ({h.get('release_year') or '?'}): {h['_why_you']}" for h in ranked[:6] if h.get("_why_you")]
    if lines:
        tool.setdefault("blocks", []).append(("personal", "Why these may suit this person (matched against the "
                                              "films they liked and disliked; Movie Buff's own analysis):\n" + "\n".join(lines)))
    tool["personal"] = {"likes": len(likes), "dislikes": len(dis), "stated": len(stated)}
    return ranked


def build_prompt(question, k=3, recent_titles=None, conv=None, user=None):
    """Returns (prompt, hits). hits feed the next turn's follow-up resolution.
    conv: v3 conversation state (conv.py), passed to retrieve() for the planner.
    user: anonymous browser id, for the taste profile (PERSONAL=1)."""
    if conv is not None:
        hits = retrieve(question, k=k, recent_titles=recent_titles, conv=conv)
    else:
        hits = retrieve(question, k=k, recent_titles=recent_titles)
    LAST_TOOL.clear()
    if TOOLS:
        import tools as _T
        plan = (hits[0].get("_plan") if hits else None) or _R.LAST.get("plan")
        if not hits and _R.LAST.get("unknown"):
            hits = _T.facts_hits(_R.LAST["unknown"])     # a film newer than the catalogue
            if hits:
                hits[0]["_plan"] = plan
        elif hits and (plan or {}).get("intent") == "named_film":
            # The catalogue's film of that title is from another year ("Sinners" 2007 vs the
            # 2025 film the planner meant): use facts.db's film of the right year instead.
            for f in _R.LAST.get("plan_films") or []:
                try:
                    y = int(f.get("year") or 0)
                except (TypeError, ValueError):
                    y = 0
                nt = _R.normalize(f["title"])
                same = [i for i, h in enumerate(hits) if _R.normalize(h["title"]) == nt]
                if y and same and all(abs(int(str(hits[i].get("release_year") or 0)[:4] or 0) - y) > 1 for i in same):
                    fh = _T.facts_hits([f["title"]], year=y)
                    if fh and abs(int(fh[0]["release_year"] or 0) - y) <= 1:
                        fh[0]["_plan"] = hits[0].get("_plan")
                        hits[same[0]] = fh[0]
        try:
            tool = _T.run(question, hits, plan, q_text=(plan or {}).get("search_query"))
        except Exception as e:                            # a tool failure must not lose the answer
            print(f"[chat] tools failed: {e!r}", file=sys.stderr)
            tool = {"blocks": [], "replace_hits": False, "style": None, "route": None, "ms": {}}
        if (plan or {}).get("intent") == "recommend" and hits:
            try:
                hits = _personalize(question, hits, plan, conv, user, tool)
            except Exception as e:                        # personalisation must not lose the answer
                print(f"[chat] personalisation failed: {e!r}", file=sys.stderr)
        LAST_TOOL.update(tool)
        if tool.get("replace_hits"):
            hits = []
        if hits or tool.get("blocks"):
            amb = hits[0].get("_ambiguous") if hits else None
            amb_line = (f"\n\nThe question could refer to {amb[0]} or {amb[1]}. Answer for {amb[0]} in one or two "
                        f"sentences, then ask which one they meant.") if amb and len(amb) > 1 else ""
            return _v3_prompt(question, hits, tool, amb_line), hits
    if not hits:
        # Change 9: nothing in the database matched (e.g. a film we don't have).
        # Say so instead of answering from memory.
        return f"""Question: {question}

The movie database found nothing that matches this question. If the question is about a specific film, say plainly and briefly that you don't have that film in your database, and do not describe it, its cast or its plot from memory. If it is a general question about movies, answer briefly and say you are answering without information from the database.""", hits
    context = "\n\n---\n\n".join(context_for(h) for h in hits)
    amb = hits[0].get("_ambiguous") if hits else None
    # v3: the planner said "that" could mean either of two films -> answer for the likelier
    # one and ask, instead of silently picking.
    amb_line = (f"\n\nThe question could refer to {amb[0]} or {amb[1]}. Answer for {amb[0]} in one or two "
                f"sentences, then ask which one they meant.") if amb and len(amb) > 1 else ""
    if not ANSWER_STRICT:
        return f"""Here is information on movies that might be relevant to the question:

{context}

Question: {question}

Answer using the information above.{amb_line}

{HOW_TO_ANSWER}

{NO_HEDGING}""", hits
    prompt = f"""Here is information on movies that might be relevant to the question:

{context}

Question: {question}

Answer using the information above. If none of it actually answers the question, say so honestly instead of guessing.

How to answer: lead with the direct answer in your first sentence - if the question asks which film, name it with its year. If several films are listed, answer about the one that best fits the question and leave out the others unless they help. Back the answer with one or two specific details from the information above (a plot point, the director, the rating) rather than general praise. For recommendations, suggest two or three films from the information above, each with a one-line reason tied to what the person asked for. Only give a verdict or recommendation when the question asks for one. When asked whether a film is good or worth watching, give a clear verdict first and say who would enjoy it, using its rating and plot. Do not bring in awards, sequels, source novels, or films that are not listed above, even if you know them.

Only state a director, cast member, character name, award, or box-office figure if it appears in the information above. If a detail is not there, say you don't have it rather than recalling it from memory. Never reason out loud or correct yourself mid-answer - if you are unsure, say so in one short sentence and move on."""
    if ANSWER_STYLE:
        # Change 15: the "How to answer" paragraph matched to the question type
        # (credits / opinion / recommend / identify / general), from answer_style.py.
        from answer_style import apply_style
        prompt = apply_style(prompt, question)
    return prompt, hits


def prewarm():
    """Pay the load cost before the prompt appears, not on the first question."""
    print("Warming up...", end=" ", flush=True)
    t0 = time.perf_counter()
    try:
        ollama.embed(model=EMBED_MODEL, input=["warmup"], keep_alive=KEEP_ALIVE)
        ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": "hi"}],
            options=llm_options({"num_predict": 1}),
            keep_alive=KEEP_ALIVE,
        )
    except Exception as e:
        print(f"\n(warmup failed: {e} -- continuing anyway)")
        return
    print(f"ready in {time.perf_counter() - t0:.1f}s\n")


def ask(history, thinking_on):
    """Stream one response. Prints tokens as they arrive instead of waiting for
    the whole generation, so text starts appearing in ~2s rather than ~30s.

    thinking_on is passed straight to the model, so turning it off stops the
    trace being GENERATED, not merely displayed. The trace is real output
    tokens - on a typical turn it is roughly as long as the answer itself, so
    switching it off close to halves the time to a finished reply."""
    stream = ollama.chat(
        model=MODEL,
        messages=history,
        stream=True,
        think=thinking_on,
        keep_alive=KEEP_ALIVE,
        options=llm_options(),   # same num_batch as retrieve's calls (NUM_BATCH)
    )

    answer, thinking = [], []
    in_thinking = False
    started_answer = False
    t0 = time.perf_counter()
    first_token = None

    for chunk in stream:
        msg = chunk.get("message") if isinstance(chunk, dict) else chunk.message
        thought = (msg.get("thinking") if isinstance(msg, dict) else getattr(msg, "thinking", None)) or ""
        piece = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)) or ""

        if (thought or piece) and first_token is None:
            first_token = time.perf_counter() - t0

        if thought:
            thinking.append(thought)
            if not in_thinking:
                print("--- Thinking ---")
                in_thinking = True
            print(thought, end="", flush=True)

        if piece:
            if in_thinking:
                print("\n----------------")
                in_thinking = False
            if not started_answer:
                print("\nMovie Buff: ", end="", flush=True)
                started_answer = True
            answer.append(piece)
            print(piece, end="", flush=True)

    if in_thinking:
        print("\n----------------")
    print(f"\n\n[first text in {first_token:.1f}s, "
          f"{time.perf_counter() - t0:.1f}s total]\n")

    return "".join(answer).strip()


def main():
    print("Movie Buff -- ask a question, '/think' to show reasoning, 'quit' to exit.\n")
    prewarm()

    history = []
    # Off by default. The reasoning trace is real generated tokens - roughly
    # half of every response - and almost nobody reads it. /think turns it on
    # when you want to see the model work, e.g. for a demo.
    thinking_on = False
    recent_titles = []   # what the last turn was about, for "who directed it?"
    import conv as _conv
    state = _conv.new()  # v3 (CONV=1): the whole conversation, for the planner

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            break
        if question.lower() == "/think":
            thinking_on = not thinking_on
            print(f"  reasoning: {'on' if thinking_on else 'OFF (faster replies)'}\n")
            continue

        if _R.CONV:
            prompt, hits = build_prompt(question, conv=state)
        else:
            prompt, hits = build_prompt(question, recent_titles=recent_titles)
        if hits:
            recent_titles = [h["title"] for h in hits]
        history.append({"role": "user", "content": prompt})
        answer = ask(history, thinking_on)
        if hits:
            # In the order the answer talked about them - see recent_titles_from.
            recent_titles = recent_titles_from(hits, answer)
        state = _conv.update(state, question, hits, plan=(hits[0].get("_plan") if hits else None),
                             answer=answer)

        # Same as before: swap the stuffed prompt back to the plain question so
        # history doesn't balloon with repeated movie blurbs every turn.
        history[-1] = {"role": "user", "content": question}
        history.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
