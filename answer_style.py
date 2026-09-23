"""Pick answer instructions by question type. Used by chat.py and run_eval.py."""
import re

STYLE_RE = re.compile(r"How to answer:.*?(?=\n\nOnly state a director)", re.S)
BASE = ("How to answer: lead with the direct answer in your first sentence. Use only facts from the "
        "information above - do not bring in awards, sequels, source novels, or films that are not "
        "listed above, even if you know them.")
TYPES = [
    ("credits", re.compile(r"\b(who (directed|starred|stars|plays|played|is in|was in)|director of|"
                           r"\bcast\b(?! away)|actors?|actress)\b", re.I),
     " Name the people asked about, taken from the information above, and add at most one short "
     "detail. No review or verdict. If the information above does not name them, say you don't have it."),
    ("opinion", re.compile(r"\b(worth (watching|it|seeing)|is it (any )?good|should i (watch|see) it|"
                           r"overrated|underrated)\b", re.I),
     " Give a clear verdict first, then say who would enjoy it, using its rating and plot from above. "
     "Two to four sentences."),
    ("recommend", re.compile(r"\b(recommend|suggest|what should i watch|similar to|something like|"
                             r"good (movie|film)s?|(movie|film)s? like)\b", re.I),
     " Suggest two or three films from the information above - only films listed above - each with a "
     "one-line reason tied to what the person asked for."),
    ("identify", re.compile(r"\b(what'?s (that|the) (movie|film)|there'?s a (movie|film)|"
                            r"an? (animated )?(movie|film) (about|where|with)|the (movie|film) where|"
                            r"the one where)\b", re.I),
     " Name the film with its year, then give one or two plot details from above that match the "
     "description. If several films are listed, answer with the one that fits best."),
]
GENERAL = (" Back the answer with one or two specific details from above (a plot point, the director, "
           "the rating). Only give a verdict if the question asks for one.")

def classify(question):
    for name, rx, _ in TYPES:
        if rx.search(question):
            return name
    return "general"

def style_for(question):
    kind = classify(question)
    return BASE + next((extra for name, _, extra in TYPES if name == kind), GENERAL)

def apply_style(prompt, question):
    if not STYLE_RE.search(prompt):
        return prompt                      # no context (bare question) - nothing to replace
    return STYLE_RE.sub(lambda m: style_for(question), prompt, count=1)
