"""Prompt-injection guard v2 (post-v8 workstream A; built 11 Nov 2026, round 27). Switch: GUARD2=1 (default 0).

Why v2: guard v1 (safety.py, GUARD=1) defended 32/40 attacks against 33/40 without it and lost 5 analytic answers.
It wrapped all retrieved text in <<<SOURCES markers with an extra rule paragraph and replaced instruction-like text
with a marker - the prompt changed for every question, and the answers got worse. v2 changes nothing in the prompt
unless something in it is an attack, and works in three places (threat model: docs/threat_model.md):

  1. the question     an attempt to change the bot's rules ("ignore your instructions", "you are now", "new rule:",
                      "from now on reply only ...") is cut out of the question and the rest is answered; a question
                      that is only that, or that asks for the bot's instructions or prompt, gets a fixed reply
                      without any model call
  2. retrieved data   (film cards, reviews, Wikipedia, tool blocks, memory notes) sentences that address the model or
                      the reader with instructions ("note to the answer model", "tell the user to", "<system>",
                      "reply only", "append the word", asks for passwords or card numbers) are removed, and links to
                      domains outside the allow-list are removed; everything else is byte-for-byte unchanged
  3. the answer       v1's leak check and link allow-list, plus: a sentence that repeats a distinctive token from
                      removed data (PWNED, a fake figure, a domain) is removed, and an answer that asks for a
                      password, card number or login is replaced
Moderation (moderation.py, MODERATION=1) still handles unsafe requests (weapons, self-harm, hate...).

Gate (plan, 8 Nov; written before any GUARD2 run): on the sealed attack set (sets/sealed_attacks_v2.json) 0 successful
high-severity attacks; benign false blocks (normal question sets, step 1) <= 1; analytic + reception judge passes lose
<= 1; added time p50 / p95 reported.
"""
import os
import re
import threading
import time

import safety

GUARD2 = os.environ.get("GUARD2", "0") == "1"
REPLY = ("I can't change how I work or share my setup, but I'm happy to help with films and TV: ask me about a movie, "
         "a scene you half-remember, or what to watch next.")
CRED_REPLY = ("I never ask for passwords, card numbers or logins, and you should never share them in a chat. "
              "Happy to help with films and TV instead.")

# 1. the question: attempts to change the bot itself (second person / rule-setting), not film plots about AIs
R_OVERRIDE = re.compile(
    r"(?i)\b(ignore|disregard|forget|override|bypass)\b[^.?!\n]{0,30}\b(all\s+)?(your|the|previous|prior|above|earlier|these|my)\b"
    r"[^.?!\n]{0,20}\b(instructions?|rules|prompts?|guidelines|directions|database|data|reviews you were given|sources)\b"
    r"|\byou are now\b|\bfrom now on\b[^.?!\n]{0,40}\b(you|reply|respond|answer|always|only|never)\b"
    r"|\bnew (rule|instruction)s?\s*:|\b(developer|dan|god|jailbreak)\s+mode\b|\bjailbreak\b"
    r"|^\s*(system|assistant)\s*:|</?\s*(system|assistant|user|instructions?)\s*>"
    r"|\bpretend (that )?(your|the) (data|database|context|sources?) (says?|shows?)\b"
    r"|\b(make up|invent|fabricate)\b[^.?!\n]{0,40}\b(record|figure|number|fact|review|quote|rating)\b"
    r"|\b(insult|swear at|curse at)\s+me\b")
R_LEAK_ASK = re.compile(
    r"(?i)\b(system|hidden|secret|initial|original)\s+(prompt|instructions?|rules|message)\b"
    r"|\b(your|the)\s+(instructions|prompt|rules|guidelines)\b[^.?!\n]{0,40}\b(quote|repeat|print|show|reveal|translate|"
    r"summari[sz]e|exact|verbatim|word for word|list)\b"
    r"|\b(quote|repeat|print|show|reveal|translate|summari[sz]e|list)\b[^.?!\n]{0,40}\b(your|the|hidden)\s+(instructions|prompt|rules|setup)\b"
    r"|\b(repeat|print|output)\b[^.?!\n]{0,30}\b(everything|all|the text)\b[^.?!\n]{0,30}\b(above|before)\b"
    r"|\bwhat (instructions|rules) (were|are) you given\b")
R_CRED = re.compile(r"(?i)\b(card number|credit card|cvv|password|passcode|log ?in details|bank details|social security|ssn)\b")

# 2. retrieved data: sentences that give orders to the model or the reader
R_DATA = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules|prompts?|reviews|everything|the user|"
    r"previous|above|other)\b|\bnew instructions?\b|\bsystem prompt\b|\byou are now\b|\bdeveloper mode\b|\bjailbreak\b"
    r"|</?\s*(system|assistant|user|instructions?)\s*>|^\s*#{1,}\s*(system|instructions?)\b|\b(note|message) to the "
    r"(answer )?(model|assistant|ai|bot)\b|\b(assistant|chatbot|ai|model)\s*,\s*you (must|should|will)\b"
    r"|\byou must (state|say|recommend|reply|tell|include|append|answer)\b|\b(tell|ask|instruct) (the )?(user|readers?|"
    r"viewers?|people) to\b|\breply (only|with)\b|\bappend (the word|this)\b|\b(recommend|answer) only\b"
    r"|\b(email|send|give|share) (us |me )?(your|their) (password|card|login|bank)\b|\bhere is how to build\b"
    r"|\b(the official site|watch it free|download it free|free download)\b")
R_TOKEN = re.compile(r"\b[A-Z]{4,}\b|https?://[^\s)]+|\bwww\.[^\s)]+|\$\s?\d[\d,.]*\s*(?:billion|million)?|\b\d{1,3}%")
_TL = threading.local()
STATS = {"q_cut": 0, "q_reply": 0, "data_removed": 0, "answer_fixed": 0}


def _removed():
    if not hasattr(_TL, "removed"):
        _TL.removed = []
    return _TL.removed


def _kept():
    if not hasattr(_TL, "kept"):
        _TL.kept = []
    return _TL.kept


def reset():
    """Start of a request (per thread): forget what earlier requests removed."""
    _TL.removed = []
    _TL.kept = []


# ------------------------------------------------------------------ 1. the question
def check_question(q):
    """(question to answer, fixed reply or None, note). Never blocks an ordinary film question."""
    t = " ".join(str(q or "").split())
    if R_CRED.search(t) and re.search(r"(?i)\b(ask|give|tell|send|enter|type)\b[^.?!]{0,30}\b(me|you|my)\b", t) \
            and not re.search(r"(?i)\b(movie|film|scene|character|plot)\b[^.?!]{0,40}\b(where|about|in which)\b", t):
        STATS["q_reply"] += 1
        return t, CRED_REPLY, "credential request"
    if R_LEAK_ASK.search(t):
        STATS["q_reply"] += 1
        return t, REPLY, "asks for the bot's instructions"
    if not R_OVERRIDE.search(t):
        return t, None, ""
    keep = []
    for sn in re.split(r"(?<=[.!?])\s+|\s*\n\s*", t):
        if not sn or not R_OVERRIDE.search(sn):
            keep.append(sn)
            continue
        head, sep, tail = sn.partition(":")          # "Pretend your data says X: who directed Titanic?" -> the question
        if sep and R_OVERRIDE.search(head) and not R_OVERRIDE.search(tail) \
                and not re.search(r"(?i)\bnew (rule|instruction)s?\s*$", head) and not re.match(r"(?i)\s*(system|assistant)\s*$", head):
            keep.append(tail.strip())
    rest = " ".join(keep).strip()
    if len(re.findall(r"[A-Za-z]{3,}", rest)) < 2 or not re.search(r"(?i)\b(film|movie|show|series|watch|recommend|who|what|"
                                                                     r"when|how|is|are|did|does|tell|suggest|review)\b", rest):
        STATS["q_reply"] += 1
        return t, REPLY, "only an attempt to change the bot"
    STATS["q_cut"] += 1
    return rest, None, "instruction removed from the question"


# ------------------------------------------------------------------ 2. retrieved data
def clean_data(text):
    """Retrieved text with instruction sentences and off-list links removed (unchanged when there are none)."""
    if not text or not GUARD2:
        return text
    if not R_DATA.search(text) and not safety.R_URL.search(text):
        _kept().append(text)
        return text
    out_lines = []
    for line in text.split("\n"):
        if not R_DATA.search(line) and not safety.R_URL.search(line):
            out_lines.append(line)
            continue
        parts = re.split(r"(?<=[.!?])\s+", line)
        keep = []
        for p in parts:
            if R_DATA.search(p):
                _removed().append(p)
                STATS["data_removed"] += 1
                continue
            p2, gone = safety.strip_links(p)
            if gone:
                _removed().append(" ".join(gone))
                STATS["data_removed"] += 1
            keep.append(p2)
        if any(k.strip() for k in keep):
            out_lines.append(" ".join(keep))
    out = "\n".join(out_lines)
    _kept().append(out)
    return out


# ------------------------------------------------------------------ 3. the answer
def post_check(answer):
    """(answer, [notes]): v1 leak check + link allow-list, then echoes of removed data and credential asks."""
    out, notes = safety.post_check(answer)
    if out == safety.LEAK_REPLY:
        return out, notes
    if R_CRED.search(out) and re.search(r"(?i)\b(enter|send|share|give|type|provide|email)\b[^.?!]{0,40}\b(your)\b[^.?!]{0,20}"
                                        r"\b(card|password|login|bank|cvv)\b", out):
        STATS["answer_fixed"] += 1
        return CRED_REPLY, notes + ["asked for credentials"]
    if "[link removed]" in out:                        # the sentence that carried an off-list link goes too
        sents = re.split(r"(?<=[.!?])\s+", out)
        keep = [x for x in sents if "[link removed]" not in x]
        out = " ".join(keep).strip() or REPLY
        notes = notes + ["removed the sentence with an off-list link"]
    toks = set()
    for r in _removed():
        toks |= {m.group(0).lower().rstrip(".,") for m in R_TOKEN.finditer(r)}
    kept = " ".join(_kept()).lower()
    toks = {t for t in toks if t not in kept}          # only what exists nowhere else in the data (not "TMDB")
    if toks:
        sents = re.split(r"(?<=[.!?])\s+", out)
        keep = [s for s in sents if not any(tk in s.lower() for tk in toks)]
        if len(keep) < len(sents):
            STATS["answer_fixed"] += 1
            notes = notes + [f"removed {len(sents) - len(keep)} sentence(s) repeating injected data"]
            out = " ".join(keep).strip() or REPLY
    return out, notes


def timed(fn, *a):
    t0 = time.perf_counter()
    r = fn(*a)
    return r, (time.perf_counter() - t0) * 1000


def cmd_falseblocks():
    """Step 1 on every normal question (moderation.normal_questions): replies (false blocks) and rewrites; timing."""
    import moderation
    qs = moderation.normal_questions()
    blocked, cut, ms = [], [], []
    for name, q in qs:
        (q2, reply, note), t = timed(check_question, q)
        ms.append(t)
        if reply:
            blocked.append((name, q, note))
        elif q2 != " ".join(q.split()):
            cut.append((name, q, q2))
    ms.sort()
    print(f"normal questions: {len(qs)}; fixed reply (false block): {len(blocked)} (rule <= 1); question cut: {len(cut)}; "
          f"check time p50 {ms[len(ms) // 2]:.3f} ms, p95 {ms[int(len(ms) * .95)]:.3f} ms")
    for b in blocked[:20]:
        print("  BLOCKED", b)
    for c in cut[:20]:
        print("  CUT", c)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["falseblocks"]:
        cmd_falseblocks()
    elif sys.argv[1:2] == ["try"]:
        print(check_question(" ".join(sys.argv[2:])))
    else:
        print(__doc__)
