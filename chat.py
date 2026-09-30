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
    title, year, genres, director, cast, overview, va, vc = _CTX_DB.execute(
        "SELECT title, release_year, genres, director, top_cast, overview, "
        "vote_average, vote_count FROM movies WHERE id = ?", (h["id"],)).fetchone()
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


def build_prompt(question, k=3, recent_titles=None):
    """Returns (prompt, hits). hits feed the next turn's follow-up resolution."""
    hits = retrieve(question, k=k, recent_titles=recent_titles)
    if not hits:
        # Change 9: nothing in the database matched (e.g. a film we don't have).
        # Say so instead of answering from memory.
        return f"""Question: {question}

The movie database found nothing that matches this question. If the question is about a specific film, say plainly and briefly that you don't have that film in your database, and do not describe it, its cast or its plot from memory. If it is a general question about movies, answer briefly and say you are answering without information from the database.""", hits
    context = "\n\n---\n\n".join(context_for(h) for h in hits)
    if not ANSWER_STRICT:
        return f"""Here is information on movies that might be relevant to the question:

{context}

Question: {question}

Answer using the information above.

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

        prompt, hits = build_prompt(question, recent_titles=recent_titles)
        if hits:
            recent_titles = [h["title"] for h in hits]
        history.append({"role": "user", "content": prompt})
        answer = ask(history, thinking_on)
        if hits:
            # In the order the answer talked about them - see recent_titles_from.
            recent_titles = recent_titles_from(hits, answer)

        # Same as before: swap the stuffed prompt back to the plain question so
        # history doesn't balloon with repeated movie blurbs every turn.
        history[-1] = {"role": "user", "content": question}
        history.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
