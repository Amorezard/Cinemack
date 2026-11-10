"""3c preference chips (v8, 10 Nov 2026): spot a viewing LIMIT stated in a chat message and offer it as a filter chip
the person can accept ("Under 90 min", "No horror", "Netflix only", "PG and under", "1990 or later"). Nothing is applied
without a click, and the detector never changes an answer by itself: the chip only fills the chat's filter bar.

    detect("keep it under 90 minutes")      -> {"max_runtime": 90}
    detect("we only have Disney+ and Hulu")  -> {"services": ["disney", "hulu"], "services_only": True}
    detect("I love horror, give me the goriest") -> {}

Rules (no model call): a limit needs a limiting cue ("no", "nothing", "only", "under", "can't stand", "keep it" ...)
next to the thing it limits. Questions about a title ("is The Irishman over three hours?", "who directed the musical
Cabaret?") are never read as limits, nor are tastes for something ("I love horror"), mentions ("my favourite Netflix
film") or distances ("two hours from Boston"). Runtimes are offered at the chat filter's steps (90, 120, 150, 180),
rounded down. Tuning set: sets/tune_v8_phrases.json (40, written 8 Nov); rule: all 28 real limits caught, <= 1 of
12 traps triggers. Switch: PREF_CHIPS=1 (default 0: the server sends no chips).
"""
import re

RUNTIME_STEPS = (90, 120, 150, 180)
GENRE_WORDS = [
    ("Horror", r"horror|gore|gory|scary|scare[sd]?|slashers?"),
    ("Music", r"musicals?"),
    ("Romance", r"romance|romantic|rom-?coms?|love stor(?:y|ies)"),
    ("War", r"war (?:films?|movies?)|war"),
    ("Animation", r"animated|animation|cartoons?|anime"),
    ("Documentary", r"documentar(?:y|ies)|docs"),
    ("Western", r"westerns?"),
    ("Science Fiction", r"sci-?fi|science fiction"),
    ("Comedy", r"comed(?:y|ies)"),
    ("Thriller", r"thrillers?"),
    ("Fantasy", r"fantasy"),
    ("Crime", r"crime"),
]
NEG_CUE = (r"\b(?:no|nothing|not into|not a fan of|don'?t (?:like|want|do|enjoy|care for)|do not (?:like|want)|"
           r"hate|can'?t stand|cannot stand|skip|avoid|without|none of|anything but|sick of|tired of|no more)\b")
SERVICE_WORDS = [("netflix", r"netflix"), ("disney", r"disney\s*(?:\+|plus)?"), ("hulu", r"hulu"),
                 ("prime", r"(?:amazon )?prime(?: video)?"), ("apple", r"apple\s*tv\s*(?:\+|plus)?"),
                 ("max", r"hbo max|\bMax\b"), ("peacock", r"peacock"), ("paramount", r"paramount\s*(?:\+|plus)?"),
                 ("tubi", r"tubi"), ("criterion", r"criterion"), ("mubi", r"mubi"), ("kanopy", r"kanopy")]
ONLY_CUE = r"\b(?:only|just have|just got|we have|i have|all we have|all i have)\b"
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "an": 1, "a": 1}
QUESTION_START = re.compile(r"^\s*(?:what|who|whose|which|when|where|why|how|is|was|are|were|does|did|do|has|have)\b", re.I)


def _minutes(text):
    """First duration in the text, in minutes, with its span."""
    t = text.lower()
    pats = [
        (r"(\d{2,3})\s*(?:minutes|mins?|min)\b", lambda m: int(m.group(1))),
        (r"(one|two|three|four|an|a|\d)\s+and\s+a\s+half\s+hours?", lambda m: int((NUM.get(m.group(1)) or int(m.group(1))) * 60 + 30)),
        (r"(?:an|a|one) hour and a half", lambda m: 90),
        (r"(one|two|three|four|an|a|\d(?:\.\d)?)\s*(?:hours?|hrs?)\b", lambda m: int((NUM.get(m.group(1)) or float(m.group(1))) * 60)),
    ]
    best = None
    for p, f in pats:
        m = re.search(p, t)
        if m and (best is None or m.start() < best[1]):
            best = (f(m), m.start(), m.end())
    return best


def _step(minutes):
    ok = [s for s in RUNTIME_STEPS if s <= minutes]
    return ok[-1] if ok else RUNTIME_STEPS[0]


def detect(text):
    """The limits stated in one message, as chat-filter fields. {} when there is none."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return {}
    t = raw.lower()
    if QUESTION_START.search(raw) and raw.rstrip().endswith("?"):
        return {}                                                   # a question about something, not a limit
    out = {}

    # runtime: "under 90 minutes", "nothing over two hours", "longer than ...", "<duration> max"
    d = _minutes(t)
    if d:
        mins, a, b = d
        before, after = t[max(0, a - 40):a], t[b:b + 12]
        if re.search(r"(?:under|less than|shorter than|no more than|nothing (?:over|longer than|above)|anything (?:over|longer than)|"
                     r"longer than|max(?:imum)?(?: of)?|at most|up to|keep it (?:to|under|below)?|below)\s*$", before) \
                or re.match(r"\s*(?:max|maximum|tops|at most|or less|or shorter)\b", after):
            out["max_runtime"] = _step(mins)

    # certificate
    m = re.search(r"\b(?:keep (?:it|everything|things|them|this)|only|stuff only|rated|nothing above|no higher than|max)\s+(g|pg-13|pg)\b(?!-)"
                  r"|\b(g|pg-13|pg)[- ]rated\b.*\bonly\b|\b(g|pg-13|pg)\s+(?:or (?:under|below|milder)|and under|at most|max)\b", t)
    if m:
        out["max_cert"] = (m.group(1) or m.group(2) or m.group(3)).upper()
    elif re.search(r"\bnothing rated r\b|\bno r[- ]rated\b|\bnothing r[- ]rated\b", t):
        out["max_cert"] = "PG-13"
    elif re.search(r"\b(?:family|kid|kids|child)[- ]friendly\b", t):
        out["max_cert"] = "PG"

    # earliest year: "nothing before 1990", "nothing older than 2010", "from the 2000s onwards", "no black and white"
    m = re.search(r"\bnothing (?:before|older than|earlier than|from before)\s+(?:the\s+)?(\d{4})", t) \
        or re.search(r"\bonly (?:films?|movies?|stuff|things)?\s*(?:from|since)\s+(?:the\s+)?(\d{4})s?\s*(?:on(?:wards)?|and later|or later|onward)?", t) \
        or re.search(r"\b(?:from|since)\s+(?:the\s+)?(\d{4})s?\s+(?:on|onwards|onward|and later|or later)\b", t)
    if m:
        out["min_year"] = int(m.group(1))
    elif re.search(r"black[- ]and[- ]white", t) and re.search(NEG_CUE, t):
        out["min_year"] = 1960

    # genres to avoid: a negative cue within a few words BEFORE the genre word ("no horror", "I hate musicals"),
    # or "nothing/anything scary"
    nev = []
    for g, pat in GENRE_WORDS:
        for m in re.finditer(rf"\b(?:{pat})\b", t):
            window = t[max(0, m.start() - 45):m.start()]
            if re.search(NEG_CUE + r"(?:\s+\S+){0,4}\s*$", window) or re.search(r"\b(?:anything|nothing)\s+$", window) and g == "Horror":
                if g == "War" and m.group(0) == "war" and not re.search(r"\bwar\s+(?:films?|movies?|stuff)\b", t[m.start():m.start() + 15]):
                    continue
                nev.append(g)
                break
    if "Romance" in nev and "Comedy" in nev and re.search(r"\bromantic comed|\brom-?com", t):
        nev.remove("Comedy")                                        # "romantic comedies" is one kind of film
    if nev:
        out["never_genres"] = list(dict.fromkeys(nev))

    # services: "only stuff on Netflix", "we only have Disney+ and Hulu", "we just have Max", "only Prime Video for me"
    if re.search(ONLY_CUE, t) and not re.search(r"\b(?:my|his|her|their) (?:mom|mum|dad|wife|husband|partner|friend|brother|sister|"
                                               r"son|daughter|kids?|parents?)\s+only\b|\b(?:he|she|they) only\b", t):   # someone else's habit
        svc = [k for k, pat in SERVICE_WORDS if re.search(pat, raw if k == "max" else t, 0 if k == "max" else re.I)]
        if svc:
            out["services"], out["services_only"] = svc, True
    return out


LABELS = {"max_runtime": lambda v: f"Under {v // 60}h{'' if v % 60 == 0 else f' {v % 60}m'}" if v >= 120 else f"Under {v} min",
          "max_cert": lambda v: f"{v} and under",
          "min_year": lambda v: f"{v} or later",
          "never_genres": lambda v: "No " + ", ".join(g.lower() if g != "Science Fiction" else "sci-fi" for g in v),
          "services": lambda v: " + ".join({"netflix": "Netflix", "disney": "Disney+", "hulu": "Hulu", "prime": "Prime Video", "apple": "Apple TV+",
                                            "max": "Max", "peacock": "Peacock", "paramount": "Paramount+", "tubi": "Tubi", "criterion": "Criterion",
                                            "mubi": "MUBI", "kanopy": "Kanopy"}.get(s, s) for s in v) + " only"}


def chips(text):
    """[{field, value, label}] for the page: one chip per limit found."""
    d = detect(text)
    out = []
    for k in ("max_runtime", "max_cert", "min_year", "never_genres", "services"):
        if k in d:
            out.append({"field": k, "value": d[k], "label": LABELS[k](d[k])})
    return out
