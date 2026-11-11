"""3b long-chat summaries (v8, 10 Nov 2026). Switch: LONG_SUMMARY=1 (default 0 = exactly the old behaviour).

Problem: the answer model sees the last 12 messages, cut to 6,000 characters (pipeline.trim_history), and the planner
sees the last 4 turns verbatim (conv.py). In a long chat, what the person said at the start ("we're watching with
my 7-year-old", "nothing over 90 minutes") falls out of both, and a late "one more pick?" ignores it.

What this adds, with no model call (so no extra latency and nothing to hallucinate):
  * note(full, kept): a short "Earlier in this conversation" block for the turns that were cut: the person's own
    words for each cut turn (clipped) and the start of each cut answer, plus one line of the LIMITS the person stated
    anywhere in the chat (detected by pref_phrases, the 3c detector: runtime, age rating, genres to avoid, earliest
    year, services). It goes at the top of the answer prompt. At most NOTE_CHARS characters.
  * planner_lines(turns, shown): the same limits line and the earlier questions for conv.planner_block, so retrieval
    knows them too.
  * v2 (10 Nov ~16:30, after the first tuning run failed 2/6 = 2/6): the limits the person stated in EARLIER messages
    also become this request's chat filters (with_stated_limits; the stricter value wins, and a film whose fact is
    unknown fails the limit, as in Pick Tonight). Reason seen in the tuning output: the final "one film for tonight"
    retrieved films titled "...Night"; the note told the model the limit but every candidate broke it.
Rule (validation matrix, 8 Nov): sealed_v8_long >= 5/6 with the switch on; multi-turn (sealed_v8_mt) unchanged.
"""
import os
import re

LONG_SUMMARY = os.environ.get("LONG_SUMMARY", "0") == "1"
NOTE_CHARS = 1600
Q_CLIP, A_CLIP = 180, 110

try:
    import pref_phrases
except ImportError:                                      # conv.py imports this from the rag folder
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pref_phrases


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def limits(user_texts):
    """Limits stated across the person's messages, oldest first (a later statement of the same kind wins).
    -> (dict of fields, human-readable list)"""
    out = {}
    for t in user_texts:
        d = pref_phrases.detect(t)
        if "never_genres" in d:
            out["never_genres"] = sorted(set(out.get("never_genres", [])) | set(d.pop("never_genres")))
        out.update(d)
    words = []
    for k in ("max_runtime", "max_cert", "never_genres", "min_year", "services"):
        if k in out:
            words.append(pref_phrases.LABELS[k](out[k]))
    return out, words


def _first_sentence(a):
    a = re.sub(r"\[[^\]]*\]\([^)]*\)", "", str(a or ""))          # links out
    a = re.sub(r"[*_#>]+", "", a)
    m = re.split(r"(?<=[.!?])\s", " ".join(a.split()), maxsplit=1)
    return _clip(m[0] if m else a, A_CLIP)


def note(full, kept):
    """Text to put before the answer prompt, or '' when nothing was cut. full/kept: lists of {role, content}."""
    if not LONG_SUMMARY:
        return ""
    cut = full[:max(0, len(full) - len(kept))] if kept else list(full)
    if not cut:
        return ""
    lines = ["Earlier in this conversation (older turns, shortened; the full text is no longer shown):"]
    n = 0
    for m in cut:
        if m["role"] == "user":
            n += 1
            lines.append(f"- The user said: \"{_clip(m['content'], Q_CLIP)}\"")
        elif m["role"] == "assistant" and m.get("content"):
            lines.append(f"  You answered: \"{_first_sentence(m['content'])}\"")
    _lim, words = limits([m["content"] for m in full if m["role"] == "user"])
    head = ""
    if words:
        head = ("Limits the user set in this chat (they still apply to every recommendation unless the user changes them): "
                + "; ".join(words) + ".")
    body = "\n".join(lines)
    while len(body) + len(head) > NOTE_CHARS and len(lines) > 4:          # keep the first turn and the newest cut ones
        if lines[2] != "  ...":
            lines.insert(2, "  ...")
        del lines[3]
        body = "\n".join(lines)
    return (head + "\n" + body if head else body) + "\n\n"


def planner_lines(turns, shown):
    """Extra planner lines when turns fell out of the verbatim window: the limits and the earlier questions."""
    if not LONG_SUMMARY or len(turns) <= shown:
        return []
    older = turns[:-shown]
    out = []
    _lim, words = limits([t.get("q", "") for t in turns])
    if words:
        out.append("Limits the user set in this conversation: " + "; ".join(words))
    qs = [_clip(t.get("q", ""), 100) for t in older][-8:]
    if qs:
        out.append("Earlier questions: " + " | ".join(f"\"{q}\"" for q in qs))
    return out


def cache_tag(text):
    import hashlib
    return "ls:" + hashlib.sha1(text.encode()).hexdigest()[:10] if text else ""


def with_stated_limits(filters, earlier_user_texts):
    """3b v2: this chat's filter bar plus the limits stated in earlier messages (LONG_SUMMARY=1 only)."""
    if not LONG_SUMMARY:
        return filters
    lim, _w = limits(earlier_user_texts or [])
    if not lim:
        return filters
    f = dict(filters or {})
    if lim.get("max_runtime"):
        f["max_runtime"] = min(f.get("max_runtime") or 10 ** 4, lim["max_runtime"])
    if lim.get("max_cert"):
        order = ["G", "PG", "PG-13", "R"]
        cur = f.get("max_cert")
        f["max_cert"] = lim["max_cert"] if cur not in order else order[min(order.index(cur), order.index(lim["max_cert"]))]
    if lim.get("never_genres"):
        f["never_genres"] = sorted(set(f.get("never_genres") or []) | set(lim["never_genres"]))
    if lim.get("min_year"):
        f["min_year"] = max(f.get("min_year") or 0, lim["min_year"])
    if lim.get("services"):
        f["services"], f["services_only"] = lim["services"], True
    f["strict"] = True
    return f
