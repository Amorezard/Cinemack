#!/usr/bin/env python3
"""
D1 (v7, 27 Oct): TV shows - search, answers and cards, behind the TV switch (off until the TV set's rule passes:
TV on if the 30-question TV set passes >= 20 and tune / check / analytic each lose <= 1).

Data (v7_data.py tv / tv-wiki / imdb, Friday 23 Oct): tv_shows, tv_seasons, tv_credits, tv_keywords, tv_videos,
tv_ratings, tv_providers, tv_reviews, tv_wiki, imdb_* (shows and their episodes). TMDB TV ids and film ids are
different id spaces that overlap, so a TV hit carries id = -tmdb_tv_id (never a film id) and "_tv": True.

    python tv.py build                  # passages from each show's Wikipedia article + TMDB card; vectors + keywords
    python tv.py build --no-vectors
    python tv.py try "who created Breaking Bad"
    python tv.py try "a show about a chemistry teacher who starts cooking meth"
    python tv.py show "The Wire"
    python tv.py routecheck sets/tune.json sets/check.json sets/tv.json   # 30 Oct: which questions go to TV

How a question reaches TV (rules, no planner change, so film questions and the call cache are untouched):
    - it says show / series / season / episode / sitcom / miniseries / TV / showrunner / network names; or
    - it names a title that is a TV show and not a film of the same name (Breaking Bad yes, Fargo no:
      "the Fargo show" says so); or
    - the conversation's last answer was about a show and this is a follow-up ("who created it?").
    Then: a named show -> that show; recommendation wording -> shows like it / matching the genre; otherwise the
    question is a description -> vector + keyword search over show passages. Nothing found -> the film path.

Tables (facts.db): tv_passages(pid, tmdb_id, kind, text), tv_pvec(pid, tmdb_id, emb), tv_fts (FTS5 on tv_passages).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

import facts_db as F

TV = os.environ.get("TV", "0") == "1"
# 8 Nov (v8 4a, issue I-001): SAME_NAME=1 changes only what happens when a named title is BOTH a show and a film.
# Off = v7 behaviour exactly.
SAME_NAME = os.environ.get("SAME_NAME", "0") == "1"
# 8 Nov night (4a3): TV_SEASONS=1 adds episodes per season (tv_seasons, TMDB) to a show's context. Off = 3 Nov context.
TV_SEASONS = os.environ.get("TV_SEASONS", "0") == "1"
# 8 Nov night (4a3, SAME_NAME only): "Scream: The TV Series" is also plain "Scream" (tuning tn17).
R_SERIES_SUFFIX = re.compile(r"\s*[:\-\u2013]\s*the (tv |television )?series\s*$", re.I)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
TV_MAX_DISTANCE = float(os.environ.get("TV_MAX_DISTANCE", "0.62"))   # cosine distance cut for descriptions

DDL = """
create table if not exists tv_passages(pid integer primary key, tmdb_id integer, kind text, text text, text_hash text);
create index if not exists tv_passages_show on tv_passages(tmdb_id);
create table if not exists tv_pvec(pid integer primary key, tmdb_id integer, emb blob);
create virtual table if not exists tv_fts using fts5(text, content='tv_passages', content_rowid='pid',
  tokenize='porter unicode61');
"""

R_TV = re.compile(r"\b(tv|t\.v\.|television|shows?|series|seasons?|episodes?|sitcoms?|miniseries|mini-series|"
                  r"showrunners?|anime series|docuseries|limited series|cartoon series|soap operas?|binge\w*|"
                  r"seasons? \d+|s\d+e\d+|pilot episode|finale|renewed|cancell?ed|spin-?offs?|"
                  r"hbo|netflix original|amc|fx|bbc one|bbc two|nbc|cbs|abc|fox|showtime|starz|apple tv\+?)\b", re.I)
R_NOT_TV = re.compile(r"\b(movies?|films?|feature|cinema|theatrical|box office|trilogy|franchise)\b", re.I)
# A TV word strong enough on its own (a question that only says "I saw it on TV" is usually about a film)
R_TV_STRONG = re.compile(r"\b(tv (show|series|program(me)?)s?|television (show|series|program(me)?)s?|sitcoms?|miniseries|"
                         r"mini-series|showrunners?|docuseries|limited series|anime series|cartoon series|soap operas?|"
                         r"episodes?|seasons? (\d+|one|two|three|four|five|six|seven|eight|nine|ten|finale|premiere)|"
                         r"series finale|pilot episode|spin-?off series|binge\w*|"
                         r"(the|a|this|that|what|which|any) (tv )?(show|series)\b(?! me\b| us\b))", re.I)
R_SHOW_WORD = re.compile(r"\b(the )?(show|series|tv series|tv show|sitcom|miniseries)\b", re.I)
# 3 Nov: + "should I watch / binge / try / see next" anywhere ("I loved Game of Thrones, what fantasy series should I
# watch" returned only Game of Thrones, tv24) and "if I liked / since I loved <show>"
R_REC = re.compile(r"\b(recommend\w*|suggest\w*|similar to|something like|shows? like|series like|what should i "
                   r"(watch|binge)|should i (watch|binge|try|see|start)|watch next|"
                   r"(if|since|as) i (loved|liked|enjoyed)|i (loved|liked|enjoyed) [^?.!]{2,60}\b(what|any|which)\b[^?.!]{0,30}"
                   r"\b(shows?|series|else|next|watch)\b|"
                   r"good (tv )?(shows?|series|sitcoms?)|best (tv )?(shows?|series|sitcoms?)|"
                   r"looking for a (show|series)|any (good )?(shows?|series))\b", re.I)
R_FOLLOW = re.compile(r"\b(it|its|that show|the show|this show|the series|that series|they|them|the creator|"
                      r"the cast|how many seasons|who created|when did it|is it|was it)\b", re.I)

# 30 Oct fix (Thursday's bisect: TV=1 lost 9 film-identification questions on tune, every one "never found" -
# the question went to the TV search and a show came back instead of the film). Three guards, all off when TV=0:
#   1. phrases that look like TV words but describe a plot are removed before the TV words are looked for
#      ("a series of murders", "a psychotic episode", "a talent show", "binge drinking", "shows up");
#   2. a long description (TV_LONG words or more) goes to TV only with an explicit TV word (tv show, sitcom,
#      episode, season 2 ...), not just "the show" / "a series";
#   3. a show is "named" only when the title is written as a title (capitals) or the question is short
#      (TV_SHORT words or fewer, and the show has TV_NAMED_MIN_VOTES+ TMDB votes), and not when the question says
#      film / movie without an explicit TV word;
#      a follow-up goes to TV only when the last title is not also any film's title.
# `python tv.py routecheck sets/tune.json sets/check.json sets/tv.json` shows old vs new routing per question.
TV_LONG = int(os.environ.get("TV_LONG", "40"))
TV_SHORT = int(os.environ.get("TV_SHORT", "8"))
TV_NAMED_MIN_VOTES = int(os.environ.get("TV_NAMED_MIN_VOTES", "50"))
R_FALSE_TV = re.compile(
    r"\b(an? |the |whole |long |strange |bizarre |odd )?series of\b|"
    r"\b(psychotic|manic|depressive|dissociative|schizophrenic|epileptic|violent|blackout|amnesia\w*|panic|"
    r"hallucinat\w+|seizure|fugue|sleepwalking|mental|nervous) episodes?\b|\b(had|has|have|having) an? episode\b|"
    r"\b(talent|magic|puppet|stage|freak|horse|dog|cat|fashion|variety|road|light|slide|picture|art|minstrel|"
    r"roadside|travell?ing|traveling|car|air|boat|flower|trade|peep|sex|strip|drag|wild west|medicine|circus|"
    r"one[- ]man|one[- ]woman|puppet|ice|laser|fireworks?) shows?\b|"
    r"\bshows? (up|off|him|her|them|us|me|that|how|what|the way|no|signs?|interest|mercy)\b|"
    r"\bthe show must go on\b|\bbinge[- ]?(drink|eat|drank|ate)\w*|\bin (a|the) series\b(?= of)", re.I)
# 30 Oct: genre + series ("a British comedy series", "animated series for adults") and a question that starts with
# "series about / shows like" are explicit TV wording too (the TV set's tv08, tv24, tv26-tv29 never reached TV).
R_TV_EXTRA = re.compile(r"\b(comedy|drama|crime|fantasy|animated|cartoon|documentary|anime|british|korean|sci-fi|"
                        r"science fiction|horror|mystery|reality|cooking|detective|police|medical|legal|teen|kids'?|"
                        r"children's|period|costume) (tv )?(series|shows)(?=\s+(for|like|about|to|that|worth|from|set|with|i|we|should|are|is|can|you|on|in)\b|"
                        r"\s*[?.,!;]|\s*$)|"
                        r"^\W*(tv )?(series|shows?) (about|like|where|with|that|set)\b", re.I)
R_TV_GENERIC = re.compile(r"\b(the|a|this|that|what|which|any) (show|series)\b", re.I)
# 8 Nov (SAME_NAME): words that point to the SHOW or to the FILM when a title is both. Used only for a title that
# names both, so plot descriptions ("a radio host on air") are never affected. Sealed v7 s7t08 "When did Twin Peaks
# first air?" went to the 1989 pilot listed as a film, because "air" was not a TV word.
R_TV_SAME = re.compile(r"\b((first|originally|last|still) )?air(ed|s|ing)?\b(?!\s*(force|bud|plane|port|line|craft|ship|strike|raid))|"
                       r"\bon the air\b|\bnetworks?\b|\bpremiere[sd]?\b|\bpremiering\b|\bemmys?\b|\bshowrunners?\b|"
                       r"\brenew(ed|al)\b|\bcancell?(ed|ation)\b|\bseasons?\b|\bepisodes?\b|\bstreaming series\b", re.I)
# 8 Nov (SAME_NAME, tuning tn17): films have no episodes or seasons, so these words beat the film-franchise guard
# ("How many episodes are in the first season of the Scream series?" - Scream is also a film collection).
R_EPISODIC = re.compile(r"\b(episodes?|first season|last season|final season|seasons? (\d+|one|two|three|four|five|six|"
                        r"seven|eight|nine|ten)|showrunners?|renewed|cancell?ed)\b", re.I)
R_FILM_SAME = re.compile(r"\b(movies?|films?|directed|director|oscars?|academy awards?|box office|gross(ed)?|"
                         r"budget|runtime|theatrical|in theaters|in cinemas|sequel|remake|trilogy|franchise)\b", re.I)
_STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with", "from", "by", "or", "is", "it", "my",
         "your", "our", "his", "her", "their", "this", "that", "as", "be", "not", "no", "up", "out", "de", "la", "le"}


def _clean(question):
    """The question with plot phrases that only look like TV words blanked out."""
    return R_FALSE_TV.sub(" ", question or "")


def tv_words(question):
    """'strong' (an explicit TV word), 'generic' (only 'the show' / 'a series'), or None - after _clean."""
    t = _clean(question)
    if R_TV_EXTRA.search(t):
        return "strong"
    if not R_TV_STRONG.search(t):
        return None
    if R_TV_STRONG.search(R_TV_GENERIC.sub(" ", t)):
        return "strong"
    return "generic"


def n_words(question):
    return len(re.findall(r"[A-Za-z0-9']+", question or ""))


def typed_as_title(question, nt):
    """The question contains the normalised title nt written as a title: each content word starts with a capital
    or a digit, and at least one of them is not just the first word of a sentence."""
    toks = list(re.finditer(r"[^\W_]+(?:['’][^\W_]+)?", question or ""))
    w = len(nt.split())
    if not w:
        return False
    for i in range(0, len(toks) - w + 1):
        span = toks[i:i + w]
        if norm(" ".join(m.group(0) for m in span)) != nt:
            continue
        content = [m for m in span if m.group(0).casefold() not in _STOP] or span
        if not all(m.group(0)[:1].isupper() or m.group(0)[:1].isdigit() for m in content):
            continue
        before = (question[:span[0].start()]).rstrip()
        sentence_start = not before or before[-1] in ".!?:\"'“("
        if len(content) == 1 and content[0] is span[0] and sentence_start:
            continue                                      # "Lost in the woods, ..." at the start: not a title
        return True
    return False


_DB = {"conn": None, "tried": False}


def db():
    if not _DB["tried"]:
        _DB["tried"] = True
        _DB["conn"] = F.connect(readonly=True)
    return _DB["conn"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.casefold()).strip()


def _q(sql, p=(), conn=None):
    c = conn or db()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except sqlite3.Error:
        return []


# ------------------------------------------------------------------ title index
_IDX = {"shows": None, "films": None}


def show_index():
    """[(norm title, tmdb_id, name, first_year, vote_count)] most-voted first, and the set of film titles with
    >= 200 TMDB votes (a show whose name is also a known film is TV only when the question says so)."""
    if _IDX["shows"] is None:
        rows = _q("select tmdb_id, name, original_name, first_year, coalesce(vote_count, 0) from tv_shows "
                  "order by vote_count desc")
        out = []
        for sid, name, orig, y, vc in rows:
            for t in {name, orig}:
                if t and len(norm(t)) >= 2:
                    out.append((norm(t), sid, name, y, vc))
        _IDX["shows"] = out
        _IDX["films"] = {norm(t) for (t,) in _q("select title from films where coalesce(vote_count, 0) >= 200")}
        # 8 Nov (SAME_NAME): TMDB lists some pilots and TV specials as films ("Twin Peaks" 1989, genre TV Movie).
        # A TV-Movie title released no later than the show's first year is the show's pilot: it never blocks the show.
        _IDX["pilots"] = {}
        for t, y in _q("select title, year from films where genres_json like '%TV Movie%' and year is not null"):
            _IDX["pilots"].setdefault(norm(t), []).append(y)
        # 8 Nov night (4a3): "X: The TV Series" / "X: The Series" also as plain "X" - named_show() uses these only
        # with SAME_NAME=1, so a short name that is also a film still has to pass same_name_choice().
        # 4a5: also the show's IMDb primary / original title ("Marvel's Daredevil" is "Daredevil" on IMDb).
        alias, seen, disp = [], {(nt, sid) for nt, sid, *_r in out}, {}
        imdb_t = {}
        for sid, pt, ot in _q("select s.tmdb_id, t.primary_title, t.original_title from tv_shows s "
                              "join imdb_titles t on t.tconst = s.imdb_id"):
            imdb_t[sid] = (pt, ot)
        for sid, name, orig, y, vc in rows:
            cands = []
            for t in {name, orig}:
                m = R_SERIES_SUFFIX.search(t or "")
                if m:
                    cands.append(t[:m.start()].strip())
            cands += [t for t in imdb_t.get(sid, ()) if t]
            for t in cands:
                short = norm(t)
                if len(short) >= 2 and (short, sid) not in seen:
                    seen.add((short, sid))
                    alias.append((short, sid, name, y, vc))
                    disp[(short, sid)] = t
        _IDX["series_alias"] = alias
        _IDX["alias_display"] = disp
    return _IDX["shows"], _IDX["films"]


def is_pilot_title(nt, show_year):
    """8 Nov: the only films with this title are TV movies released by the show's first year (its pilot/special)."""
    shows, films = show_index()
    ys = (_IDX.get("pilots") or {}).get(nt)
    if not ys or show_year is None:
        return False
    others = [y for (_t, y) in _film_years(nt)]
    return all(y in ys and y <= show_year for y in others) if others else False


def _film_years(nt):
    """(norm title, year) of every film with this normalised title (cached per title)."""
    cache = _IDX.setdefault("film_years", {})
    if nt not in cache:
        if "film_rows" not in _IDX:
            _IDX["film_rows"] = {}
            for t, y in _q("select title, year from films where year is not null"):
                _IDX["film_rows"].setdefault(norm(t), []).append(y)
        cache[nt] = [(nt, y) for y in _IDX["film_rows"].get(nt, [])]
    return cache[nt]


def same_name_choice(question, nt, show_year, recent_titles=None):
    """8 Nov (SAME_NAME): for a title that is both a show and a film -> 'tv', 'film' or None (still unclear).
    The question's own words decide; then the conversation (last answer about a show); a pilot-only film title never
    blocks the show. None = keep the v7 default (the film) and let CLARIFY offer the choice."""
    q = _clean(question)
    tvish = bool(R_TV_SAME.search(q) or R_TV_STRONG.search(q) or R_SHOW_WORD.search(q))
    filmish = bool(R_FILM_SAME.search(q))
    if not filmish and is_pilot_title(nt, show_year):
        return "tv"
    if tvish and not filmish:
        return "tv"
    if filmish and not tvish:
        return "film"
    if not tvish and not filmish and recent_is_tv(recent_titles):
        return "tv"
    return None


def all_film_titles():
    """Every film title in facts.db, normalised (cached)."""
    if "all_films" not in _IDX:
        _IDX["all_films"] = {norm(x) for (x,) in _q("select title from films")}
    return _IDX["all_films"]


_TOK = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def inside_film_title(question, nt, max_extra=5):
    """1 Nov fix: the show title nt only appears as part of a LONGER film title written in the question
    ("Where was The Dark Knight filmed?" names the film, not the show "Dark"; "Why did Paranormal Activity make
    so much money?" names the film, not the show "Paranormal"). True when some longer run of words around the
    match, written as a title (content words capitalised), is a film's title."""
    films = all_film_titles()
    if not films:
        return False
    toks = list(_TOK.finditer(question or ""))
    w = len(nt.split())
    for i in range(0, len(toks) - w + 1):
        if norm(" ".join(m.group(0) for m in toks[i:i + w])) != nt:
            continue
        for a in range(max(0, i - max_extra), i + 1):
            for b in range(i + w, min(len(toks), i + w + max_extra) + 1):
                if (a, b) == (i, i + w):
                    continue
                span = toks[a:b]
                ns = norm(" ".join(m.group(0) for m in span))
                if ns == nt or ns not in films:
                    continue
                content = [m for m in span if m.group(0).casefold() not in _STOP] or span
                if all(m.group(0)[:1].isupper() or m.group(0)[:1].isdigit() for m in content):
                    return True
    return False


def named_show(question, require_tv_words=False, recent_titles=None):
    """The show the question names (longest title match, >= 2 words or a distinctive single word), or None.
    30 Oct guards (see TV_LONG above): written as a title unless the question is short; with no TV word, not when
    the question says film / movie. 1 Nov: not when the match is part of a longer film title in the question."""
    shows, films = show_index()
    nq = " " + norm(question) + " "
    best = None
    film_words = bool(R_NOT_TV.search(question))
    strong_tv = tv_words(question) == "strong"
    short = n_words(question) <= TV_SHORT
    for nt, sid, name, y, vc in (shows + (_IDX.get("series_alias") or []) if SAME_NAME else shows):
        if len(nt) < 4 and nt.upper() != nt:
            continue
        if f" {nt} " in nq:
            if film_words and not strong_tv:
                continue                                  # "the film where a time machine ...": a film question
            titled = typed_as_title(question, nt)
            if not titled and (not short or (vc or 0) < TV_NAMED_MIN_VOTES):
                continue                                  # a plot phrase that happens to be a show's name
            if SAME_NAME and nt in films:                 # same threshold as v7 (films with 200+ votes)
                if same_name_choice(question, nt, y, recent_titles) != "tv" and not require_tv_words:
                    continue                              # 8 Nov: both a show and a film, and nothing says the show
            elif nt in films and not (require_tv_words or R_TV.search(question) and not R_NOT_TV.search(question)):
                continue                                  # "Fargo": the film unless the question says show
            w0 = name.split()[0]
            if SAME_NAME:
                w0 = ((_IDX.get("alias_display") or {}).get((nt, sid)) or name).split()[0]   # 4a5: alias as written
                w0 = re.sub(r"[^\w'\u2019]+$", "", w0) or w0      # 8 Nov night: "Scream: The TV Series" -> "Scream"
            if len(nt.split()) == 1 and not re.search(rf"\b{re.escape(w0[:1].upper() + w0[1:])}\b",
                                                      question):
                continue                                  # one-word titles ("Friends", "House", "Lost") only when
                                                          # written as a title (capitalised), not as an ordinary word
            if inside_film_title(question, nt):
                continue                                  # 1 Nov: "The Dark Knight" is not the show "Dark"
            if best is None or len(nt) > len(best[0]) or (len(nt) == len(best[0]) and vc > best[4]):
                best = (nt, sid, name, y, vc)
    return best


_COLL = {"names": None}


def names_film_series(question):
    """'the Fast and Furious series', 'the Harry Potter series': a film franchise, not TV."""
    if _COLL["names"] is None:
        _COLL["names"] = {norm(re.sub(r"\s*(collection|series|saga|trilogy)$", "", c or "", flags=re.I))
                          for (c,) in _q("select distinct collection_name from films where collection_name is not null")}
        _COLL["names"].discard("")
    nq = " " + norm(question) + " "
    return any(f" {c} " in nq for c in _COLL["names"] if len(c) >= 4)


def recent_is_tv(recent_titles):
    """The last answer was about a show: its first recent title is a show's name and not a known film's."""
    if not recent_titles:
        return False
    shows, films = show_index()
    t = norm(recent_titles[0])
    # 30 Oct: any film title, not only those with 200+ votes
    return any(nt == t for nt, *_r in shows) and t not in films and t not in all_film_titles()


# 4 Nov fix (Tue night, all switches together: multi-turn 30 -> 28; TV off: 30): inside a conversation about films, a
# follow-up was sent to a show that shares a word with it - "no, he's an astronaut left behind on Mars" -> the series
# Mars, "Who's in the middle one?" -> The Middle. The single-question sets have no earlier turns, so they could not
# show it. Now, when the last answer was about films and the question has no explicit TV word, a named show counts
# only if it is unmistakable: written as a title, two or more words, >= TV_MIDFILM_MIN_VOTES votes, and not
# "the <ordinal> one". Questions with no earlier turn are unchanged.
TV_MIDFILM_MIN_VOTES = int(os.environ.get("TV_MIDFILM_MIN_VOTES", "2000"))
R_ORDINAL_ONE = re.compile(r"\bthe (first|second|third|fourth|middle|last|other|next|previous|final|latter|former) "
                           r"(one|film|movie|pick)s?\b", re.I)


def _mid_film(question, recent_titles):
    """The last answer was about films and the question has no TV word at all (show, series, season, episode, ...)."""
    return bool(recent_titles) and not recent_is_tv(recent_titles) and not R_TV.search(_clean(question))


def _strong_show_name(question, ns):
    nt, _sid, _name, _y, vc = ns
    return (len(nt.split()) >= 2 and (vc or 0) >= TV_MIDFILM_MIN_VOTES and typed_as_title(question, nt)
            and not R_ORDINAL_ONE.search(question))


def wants_tv(question, recent_titles=None):
    """'named' / 'words' / 'followup' / None - why this question goes to TV."""
    if not TV or db() is None or not _q("select 1 from tv_shows limit 1"):
        return None
    ns = named_show(question, recent_titles=recent_titles) if SAME_NAME else named_show(question)
    if ns and _mid_film(question, recent_titles) and not _strong_show_name(question, ns):
        ns = None                                         # 4 Nov: a film conversation's follow-up, not a show
    if ns:
        return "named"
    kind = tv_words(question)
    if kind and n_words(question) >= TV_LONG and kind != "strong":
        kind = None                                       # a long description saying only "a show" / "the series"
    if kind and not R_NOT_TV.search(question) and (not names_film_series(question) or
                                                    (SAME_NAME and R_EPISODIC.search(_clean(question)))):
        return "words"
    if recent_is_tv(recent_titles) and R_FOLLOW.search(question) and not R_NOT_TV.search(question):
        return "followup"
    return None


def wants_tv_v1(question, recent_titles=None):
    """Thursday's rules (before the 30 Oct guards), for `routecheck` only."""
    if db() is None or not _q("select 1 from tv_shows limit 1"):
        return None
    shows, films = show_index()
    nq = " " + norm(question) + " "
    for nt, sid, name, y, vc in shows:
        if len(nt) < 4 and nt.upper() != nt or f" {nt} " not in nq:
            continue
        if nt in films and not (R_TV.search(question) and not R_NOT_TV.search(question)):
            continue
        if len(nt.split()) == 1 and not re.search(rf"\b{re.escape(name.split()[0][:1].upper() + name.split()[0][1:])}\b",
                                                  question):
            continue
        return "named"
    if R_TV_STRONG.search(question) and not R_NOT_TV.search(question) and not names_film_series(question):
        return "words"
    if recent_titles:
        t = norm(recent_titles[0])
        if any(n == t for n, *_r in shows) and t not in films and R_FOLLOW.search(question) \
                and not R_NOT_TV.search(question):
            return "followup"
    return None


def _last_turn(q):
    t = (q.get("turns") or [q.get("question", "")])[-1]
    return t if isinstance(t, str) else t.get("user", "")


def _scripted_recent(q):
    """Recent titles a follow-up question would see, when the set scripts the earlier turn's films."""
    for t in reversed((q.get("turns") or [])[:-1]):
        if isinstance(t, dict) and t.get("films"):
            return [re.sub(r"\s*\(\d{4}\)\s*$", "", f) for f in t["films"]]
    return None


def routecheck(paths):
    """Old (Thursday) vs new routing for every question in the given sets; film sets should route ~0 to TV."""
    global TV
    TV = True
    for p in paths:
        d = json.load(open(p))
        qs = d if isinstance(d, list) else d["questions"]
        old_n = new_n = 0
        changed = []
        for q in qs:
            text, recent = _last_turn(q), _scripted_recent(q)
            a, b = wants_tv_v1(text, recent), wants_tv(text, recent)
            old_n += bool(a)
            new_n += bool(b)
            if a or b:
                ns = named_show(text) if b == "named" else None
                changed.append(f"  {q.get('id', '?'):12s} old {a or '-':8s} new {b or '-':8s}"
                               f"{(' -> ' + ns[2]) if ns else ''}  {text[:90]!r}")
        print(f"{p}: {len(qs)} questions; to TV: old {old_n}, new {new_n}")
        print("\n".join(changed[:60]))


def report(paths):
    """1 Nov: per question of a TV-set eval (eval_v3 output): verdict with TV on (and off), where it went, the
    shows / films it got, and the judge's reason - to see whether a failure is routing, search or the answer.
        python tv.py report results/v3eval_tv_on2.json results/v3eval_tv_off.json"""
    global TV
    TV = True
    on = {r["id"]: r for r in json.load(open(paths[0]))["rows"]}
    off = {r["id"]: r for r in json.load(open(paths[1]))["rows"]} if len(paths) > 1 else {}
    shows = {n for n, *_r in (show_index()[0] or [])}
    kinds = {"routing": 0, "search": 0, "answer": 0}
    for i, r in on.items():
        j = r.get("judge") or {}
        v = j.get("verdict", "-")
        vo = (off.get(i, {}).get("judge") or {}).get("verdict", "-")
        why = wants_tv(r["question"]) or "-"
        hits = r.get("hits") or []
        got = (r.get("grades") or {}).get("films")          # eval_v3: expected show among the hits / route films
        if v != "pass":
            kind = "routing" if why == "-" else ("search" if got is False else "answer")
            kinds[kind] += 1
        else:
            kind = ""
        tv_hits = sum(norm(h) in shows for h in hits)
        print(f"{i:5s} on {v:4s} off {vo:4s}  to TV: {why:8s} hits {len(hits)} ({tv_hits} shows) "
              f"expected found: {'-' if got is None else 'yes' if got else 'NO'}  {kind:7s} {r['question'][:60]!r}")
        if v != "pass":
            print(f"        got {hits[:3]}; judge: {str(j.get('reason', ''))[:150]}")
    print(f"failures with TV on by kind: {kinds}  (routing = never sent to TV; search = expected show not found; "
          f"answer = found but judged a fail)")


# ------------------------------------------------------------------ hits
def hit(sid, distance=None):
    r = _q("select name, first_year, end_year, genres_json, overview, vote_count, status, n_seasons, n_episodes "
           "from tv_shows where tmdb_id = ?", (sid,))
    if not r:
        return None
    name, y, ey, g, ov, vc, status, ns, ne = r[0]
    creators = [n for (n,) in _q("select name from tv_credits where tmdb_id = ? and job = 'Creator' order by ord", (sid,))]
    cast = [n for (n,) in _q("select name from tv_credits where tmdb_id = ? and kind = 'cast' order by ord limit 4", (sid,))]
    years = f"{y}" + (f"-{ey}" if ey and ey != y else ("-" if status in ("Returning Series", "In Production") else ""))
    return {"id": -int(sid), "tv_id": int(sid), "_tv": True, "title": name, "release_year": str(y or ""),
            "years": years, "genres": ", ".join(json.loads(g or "[]")), "director": ", ".join(creators),
            "top_cast": ", ".join(cast), "text": ov or "", "vote_count": vc or 0, "distance": distance}


def embed_query(text):
    import ollama
    return ollama.embed(model=EMBED_MODEL, input=[QUERY_PREFIX + text])["embeddings"][0]


def search_desc(question, k=3, q_vec=None, genres=None):
    """Vector + keyword search over show passages: [(sid, distance)] best first."""
    import numpy as np
    ranks = {}
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 3 and w not in
             {"show", "series", "about", "where", "there", "that", "with", "they", "remember", "called", "what", "which"}]
    if words:
        kw = _q("select p.tmdb_id from tv_fts join tv_passages p on p.pid = tv_fts.rowid where tv_fts match ? "
                "order by bm25(tv_fts) limit 200", (" OR ".join(dict.fromkeys(words)),))
        seen = []
        for (sid,) in kw:
            if sid not in seen:
                seen.append(sid)
        ranks["kw"] = {s: i for i, s in enumerate(seen)}
    dist = {}
    if q_vec is not None:
        rows = _q("select tmdb_id, emb from tv_pvec")
        if rows:
            M = np.stack([np.frombuffer(e, dtype=np.float16).astype(np.float32) for _s, e in rows])
            M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
            qv = np.asarray(q_vec, dtype=np.float32)
            qv /= np.linalg.norm(qv) + 1e-9
            sims = M @ qv
            for (sid, _e), s in zip(rows, sims):
                d = 1 - float(s)
                if sid not in dist or d < dist[sid]:
                    dist[sid] = d
            ranks["vec"] = {s: i for i, s in enumerate(sorted(dist, key=dist.get)[:200])}
    score = {}
    for rk in ranks.values():
        for s, i in rk.items():
            score[s] = score.get(s, 0) + 1 / (60 + i)
    out = sorted(score, key=lambda s: -score[s])
    if genres:
        ok = {sid for (sid,) in _q("select tmdb_id from tv_shows where " + " or ".join("genres_json like ?" for _ in genres),
                                   tuple(f'%"{g}"%' for g in genres))}
        out = [s for s in out if s in ok] or out
    return [(s, dist.get(s)) for s in out[:max(k * 5, 20)]]


TV_GENRES = {"comedy": "Comedy", "sitcom": "Comedy", "drama": "Drama", "crime": "Crime", "animated": "Animation",
             "animation": "Animation", "anime": "Animation", "documentary": "Documentary", "mystery": "Mystery",
             "sci-fi": "Sci-Fi & Fantasy", "science fiction": "Sci-Fi & Fantasy", "fantasy": "Sci-Fi & Fantasy",
             "action": "Action & Adventure", "adventure": "Action & Adventure", "war": "War & Politics",
             "political": "War & Politics", "western": "Western", "kids": "Kids", "family": "Family",
             "reality": "Reality", "soap": "Soap"}


def retrieve_tv(question, k=3, recent_titles=None, why=None):
    """Hits for a TV question, shaped like retrieve()'s (with _plan), or []."""
    why = why or wants_tv(question, recent_titles)
    if not why:
        return []
    ns = named_show(question, require_tv_words=bool(R_TV.search(question)))
    if why == "followup" and not ns:
        t = norm(recent_titles[0])
        ns = next(((n, s, nm, y, vc) for n, s, nm, y, vc in show_index()[0] if n == t), None)
    genres = [v for k_, v in TV_GENRES.items() if re.search(rf"\b{re.escape(k_)}\b", question, re.I)]
    rec = bool(R_REC.search(question))
    hits = []
    if ns and not rec:
        h = hit(ns[1], 0.0)
        hits = [h] if h else []
        intent = "named_film"
    else:
        try:
            q_vec = embed_query(question)
        except Exception:  # noqa: BLE001
            q_vec = None
        if rec and ns:                                    # "shows like The Wire": its passages as the query
            text = " ".join(t for (t,) in _q("select text from tv_passages where tmdb_id = ? order by pid limit 2",
                                             (ns[1],)))
            try:
                q_vec = embed_query(text[:1500] or ns[2])
            except Exception:  # noqa: BLE001
                pass
        found = search_desc(question, k, q_vec, genres)
        if rec and not found:                             # nothing matched the words: the best-known of the genre
            where = " or ".join("genres_json like ?" for _ in genres) or "1"
            found = [(sid, None) for (sid,) in _q(f"select tmdb_id from tv_shows where ({where}) and vote_count >= 200 "
                                                  f"order by vote_average * min(vote_count, 3000) desc limit 40",
                                                  tuple(f'%"{g}"%' for g in genres))]
        if rec:
            found = [(s, d) for s, d in found if not ns or s != ns[1]]
            cand = [hit(s, d) for s, d in found[:40]]
            cand = [h for h in cand if h]
            cand.sort(key=lambda h: -(h["vote_count"] or 0) * (1.0 if h["distance"] is None else max(0.05, 1 - h["distance"])))
            hits = cand[:max(k, 6)]
            intent = "recommend"
        else:
            hits = [h for h in (hit(s, d) for s, d in found[:k * 3]) if h and (h["distance"] is None
                                                                              or h["distance"] <= TV_MAX_DISTANCE)][:k]
            intent = "identify"
    if hits:
        hits[0]["_plan"] = {"intent": intent, "films": [ns[2]] if ns else [], "media": "tv", "why_tv": why,
                            "search_query": question}
    return hits


# ------------------------------------------------------------------ answer context
def season_line(sid, max_seasons=40):
    """8 Nov night (TV_SEASONS=1): episodes per season from tv_seasons (TMDB), specials (season 0) left out:
    'Episodes per season: S1 (2015) 10; S2 (2016) 12; S3 (2019) 6 [TMDB]'. '' when there is nothing."""
    rows = [r for r in _q("select season_number, air_date, episode_count from tv_seasons where tmdb_id = ? and "
                          "season_number > 0 order by season_number", (sid,)) if r[2]]
    if not rows:
        return ""
    parts = [f"S{n}" + (f" ({d[:4]})" if d else "") + f" {c}" for n, d, c in rows[:max_seasons]]
    more = f"; and {len(rows) - max_seasons} more seasons" if len(rows) > max_seasons else ""
    return "Episodes per season: " + "; ".join(parts) + more + " [TMDB]"


def seasons_wanted(h):
    """8 Nov night (4a4): TV_SEASONS=1 and this hit is the one show the question names (intent named_film) - not a
    description search (identify) or a recommendation, where the line only invites padding (TV set tv05)."""
    return TV_SEASONS and ((h or {}).get("_plan") or {}).get("intent") == "named_film"


def context(h):
    """What the answer model sees for a show (chat.context_for for TV hits)."""
    sid = h.get("tv_id") or -int(h["id"])
    r = _q("""select name, original_name, first_air_date, last_air_date, status, type, n_seasons, n_episodes,
        episode_runtime, original_language, origin_country_json, genres_json, networks_json, created_by_json, overview,
        vote_average, vote_count, imdb_id from tv_shows where tmdb_id = ?""", (sid,))
    if not r:
        return f"{h.get('title')} (TV series)"
    (name, orig, fa, la, status, typ, ns, ne, rt, lang, oc, g, nets, cb, ov, va, vc, imdb) = r[0]
    lines = [f"{name} (TV series, {h.get('years') or (fa or '')[:4]})" + (f" - original title {orig}" if orig and orig != name else "")]
    bits = []
    if fa:
        bits.append(f"first aired {fa}")
    if la and status in ("Ended", "Canceled"):
        bits.append(f"last aired {la} ({status.lower()})")
    elif status:
        bits.append(status.lower())
    if ns:
        bits.append(f"{ns} season{'s' if ns != 1 else ''}, {ne or '?'} episodes")
    if rt:
        bits.append(f"about {rt} min an episode")
    lines.append("; ".join(bits) + " [TMDB]")
    if seasons_wanted(h):
        sl = season_line(sid)
        if sl:
            lines.append(sl)
    if nets and json.loads(nets):
        lines.append(f"Network: {', '.join(json.loads(nets)[:3])} [TMDB]")
    creators = [c["name"] for c in json.loads(cb or "[]") if c.get("name")]
    if creators:
        lines.append(f"Created by: {', '.join(creators)} [TMDB]")
    if g:
        lines.append(f"Genres: {', '.join(json.loads(g))} [TMDB]")
    cast = _q("select name, character, episodes from tv_credits where tmdb_id = ? and kind = 'cast' order by ord limit 6", (sid,))
    if cast:
        lines.append("Main cast: " + ", ".join(f"{n} as {c}" if c else n for n, c, _e in cast) + " [TMDB]")
    crew = _q("select job, name from tv_credits where tmdb_id = ? and kind = 'crew' and job in ('Showrunner', "
              "'Executive Producer', 'Original Music Composer') order by episodes desc limit 4", (sid,))
    if crew:
        lines.append("Crew: " + "; ".join(f"{n} ({j})" for j, n in crew) + " [TMDB]")
    if va and (vc or 0) >= 20:
        lines.append(f"TMDB user rating {va:.1f}/10 from {vc:,} votes [TMDB]")
    if imdb:
        ir = _q("select rating, votes from imdb_ratings where imdb_id = ?", (imdb,))
        if ir and ir[0][0]:
            lines.append(f"IMDb {ir[0][0]:.1f}/10 from {ir[0][1]:,} votes [IMDb]")
        top = _q("""select e.season, e.episode, t.primary_title, r.rating, r.votes from imdb_episodes e
            join imdb_ratings r on r.imdb_id = e.tconst left join imdb_titles t on t.tconst = e.tconst
            where e.parent = ? and r.votes >= 1000 order by r.rating desc limit 3""", (imdb,))
        if top:
            lines.append("Highest-rated episodes on IMDb: " + "; ".join(
                f"S{s}E{e} {t or ''} ({rr:.1f}, {v:,} votes)" for s, e, t, rr, v in top) + " [IMDb]")
    pv = _q("select kind, provider_name from tv_providers where tmdb_id = ? and region = 'US' order by priority", (sid,))
    if pv:
        by = {}
        for kind, n in pv:
            by.setdefault(kind, [])
            if n not in by[kind]:
                by[kind].append(n)
        lab = {"flatrate": "streaming on", "free": "free on", "ads": "free with ads on", "rent": "to rent on", "buy": "to buy on"}
        lines.append("Where to watch in the US: " + "; ".join(f"{lab[k]} {', '.join(v[:4])}" for k, v in by.items() if k in lab)
                     + " [JustWatch via TMDB]")
    cert = _q("select rating from tv_ratings where tmdb_id = ? and region = 'US'", (sid,))
    if cert:
        lines.append(f"US TV rating: {cert[0][0]} [TMDB]")
    if TV_REVIEWS:
        crit = critics_lines(sid)
        if crit:
            lines.append("Critics (from the show's Wikipedia reception section): " + " ".join(crit) + " [Wikipedia]")
    if ov:
        lines.append(f"Premise (TMDB): {ov}")
    wp = _q("select text from tv_passages where tmdb_id = ? and kind in ('lead', 'premise', 'plot') order by pid limit 2", (sid,))
    for (t,) in wp:
        lines.append(f"Wikipedia: {t[:700]}")
    return "\n".join(lines)


# 4 Nov: TV critics. tv_wiki holds each show's whole Wikipedia article as plain text with "== Heading ==" lines
# (v7_data.py tv-wiki, 7,244 shows), so its Reception / Critical response section is already here: TV_REVIEWS=1 adds
# up to TV_CRIT_SENTS sentences from it - aggregator scores (Rotten Tomatoes, Metacritic, per season when stated)
# first, then sentences naming critics or outlets - to the show's context. TV_REVIEWS=0 = the 3 Nov context.
TV_REVIEWS = os.environ.get("TV_REVIEWS", "0") == "1"
TV_CRIT_SENTS = int(os.environ.get("TV_CRIT_SENTS", "5"))
_R_RECEP_HEAD = re.compile(r"^(={2,6})\s*([^=\n]*?(reception|critical|response|reviews|critics|accolades)[^=\n]*?)\s*\1\s*$",
                           re.I | re.M)
_R_ANY_HEAD = re.compile(r"^(={2,6})\s*[^=\n]+?\s*\1\s*$", re.M)
_R_AGG = re.compile(r"\b(rotten tomatoes|metacritic)\b", re.I)
_R_CRIT = re.compile(r"\b(critics?|reviewers?|praised|acclaim\w*|criticized|criticised|panned|wrote|called it|"
                     r"the guardian|the new york times|variety|the hollywood reporter|vulture|the a\.v\. club|"
                     r"rolling stone|the atlantic|time|slant|indiewire|entertainment weekly|the telegraph|ign)\b", re.I)


def critics_lines(sid, limit=None):
    """Up to `limit` sentences from the show's Wikipedia reception section, aggregator scores first, or []."""
    r = _q("select extract from tv_wiki where tmdb_id = ? and status = 'ok'", (sid,))
    if not r or not r[0][0]:
        return []
    text = r[0][0]
    out = []
    for m in _R_RECEP_HEAD.finditer(text):
        lvl = len(m.group(1))
        end = len(text)
        for n in _R_ANY_HEAD.finditer(text, m.end()):
            if len(n.group(1)) <= lvl:
                end = n.start()
                break
        body = _R_ANY_HEAD.sub(" ", text[m.end():end])
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", " ".join(body.split())) if 30 <= len(x.strip()) <= 400]
        agg = [x for x in sents if _R_AGG.search(x)]
        crit = [x for x in sents if x not in agg and _R_CRIT.search(x)]
        for x in agg + crit:
            if x not in out:
                out.append(x)
    return out[:limit or TV_CRIT_SENTS]


def reviews_block(h):
    """Audience reviews for a show (TMDB TV reviews with Movie Buff sentiment), or None."""
    sid = h.get("tv_id") or -int(h["id"])
    rows = _q("""select r.rating, r.content, s.label from tv_reviews r left join review_sentiment s
        on s.item_key = r.review_id and s.source_type = 'tv_audience' where r.tmdb_id = ?""", (sid,)) or \
        [(r_, c_, None) for r_, c_ in _q("select rating, content from tv_reviews where tmdb_id = ?", (sid,))]
    if not rows:
        return None
    labs = [l for _r, _c, l in rows if l]
    lines = [f"{h['title']} - TMDB user reviews of the show: {len(rows)}"
             + (f" ({labs.count('positive')} positive, {labs.count('mixed')} mixed, {labs.count('negative')} negative; "
                f"sentiment by Movie Buff)" if labs else "")
             + (" - too few to generalise" if len(rows) < 10 else "") + " [TMDB user reviews]"]
    for rating, content, lab in rows[:3]:
        lines.append(f"- {'(' + format(rating, 'g') + '/10) ' if rating else ''}{' '.join((content or '').split())[:260]}")
    return "\n".join(lines)


def card(h):
    """Fields for the site's film card (web/pipeline.card + film_info.enrich)."""
    sid = h.get("tv_id") or -int(h["id"])
    r = _q("select poster_path, imdb_id, n_seasons from tv_shows where tmdb_id = ?", (sid,))
    out = {"kind": "tv"}
    if r:
        if r[0][0]:
            out["poster"] = f"https://image.tmdb.org/t/p/w185{r[0][0]}"
            out["poster_lg"] = f"https://image.tmdb.org/t/p/w342{r[0][0]}"
        if r[0][2]:
            out["seasons"] = r[0][2]
        ir = _q("select rating, votes from imdb_ratings where imdb_id = ?", (r[0][1],)) if r[0][1] else []
        if ir and ir[0][0]:
            out["imdb"] = {"rating": ir[0][0], "votes": ir[0][1]}
    c = _q("select rating from tv_ratings where tmdb_id = ? and region = 'US'", (sid,))
    if c:
        out["cert"] = c[0][0]
    return out


# ------------------------------------------------------------------ build
def passages(extract, max_words=180):
    """Wikipedia plain-text extract ('== Section ==' headings) -> [(kind, text)]."""
    parts, cur_kind, cur = [], "lead", []
    for line in (extract or "").split("\n"):
        m = re.match(r"^(=+)\s*(.+?)\s*\1\s*$", line.strip())
        if m:
            if cur:
                parts.append((cur_kind, " ".join(cur)))
            h = m.group(2).lower()
            cur_kind = ("premise" if re.search(r"premise|synopsis|plot|overview|summary|series overview", h) else
                        "cast" if "cast" in h or "characters" in h else
                        "production" if re.search(r"production|development|filming|casting|writing", h) else
                        "reception" if re.search(r"reception|critical|ratings|viewership|accolades|awards", h) else
                        "episodes" if "episode" in h or "season" in h else
                        "skip" if re.search(r"references|external links|see also|notes|further reading|bibliography", h)
                        else "other")
            cur = []
        elif line.strip():
            cur.append(line.strip())
    if cur:
        parts.append((cur_kind, " ".join(cur)))
    out = []
    for kind, text in parts:
        if kind in ("skip",):
            continue
        w = text.split()
        for i in range(0, len(w), max_words):
            chunk = " ".join(w[i:i + max_words])
            if len(chunk) > 80:
                out.append((kind, chunk))
        if len([o for o in out if o[0] == kind]) >= 6:
            continue
    return out[:24]


def cmd_build(a):
    import hashlib
    conn = F.connect(a.facts)
    conn.executescript(DDL)
    shows = conn.execute("""select s.tmdb_id, s.name, s.first_year, s.overview, s.genres_json, w.extract from tv_shows s
        left join tv_wiki w on w.tmdb_id = s.tmdb_id and w.status = 'ok'""").fetchall()
    kws = {}
    for sid, n in conn.execute("select tmdb_id, name from tv_keywords"):
        kws.setdefault(sid, []).append(n)
    have = {}
    for pid, sid, th in conn.execute("select pid, tmdb_id, text_hash from tv_passages"):
        have.setdefault(sid, []).append((pid, th))
    t0, made = time.time(), 0
    for sid, name, y, ov, g, ext in shows:
        ps = []
        head = f"{name} (TV series{', ' + str(y) if y else ''})"
        card_txt = f"{head}. {ov or ''} Genres: {', '.join(json.loads(g or '[]'))}. Keywords: {', '.join(kws.get(sid, [])[:20])}."
        ps.append(("card", card_txt))
        ps += [(k, f"{head} | {k}: {t}") for k, t in passages(ext)]
        th = hashlib.sha1(json.dumps(ps).encode()).hexdigest()[:16]
        if have.get(sid) and all(x[1] == th for x in have[sid]) and not a.again:
            continue
        for pid, _th in have.get(sid, []):
            conn.execute("insert into tv_fts(tv_fts, rowid, text) select 'delete', pid, text from tv_passages where pid = ?", (pid,))
            conn.execute("delete from tv_pvec where pid = ?", (pid,))
        conn.execute("delete from tv_passages where tmdb_id = ?", (sid,))
        for kind, text in ps:
            cur = conn.execute("insert into tv_passages(tmdb_id, kind, text, text_hash) values (?,?,?,?)", (sid, kind, text, th))
            conn.execute("insert into tv_fts(rowid, text) values (?, ?)", (cur.lastrowid, text))
        made += 1
        if made % 500 == 0:
            conn.commit()
    conn.commit()
    print(f"tv passages: {made:,} shows (re)built, {conn.execute('select count(*) from tv_passages').fetchone()[0]:,} "
          f"passages ({time.time() - t0:.0f}s)", flush=True)
    if a.no_vectors:
        return
    import numpy as np
    import ollama
    todo = conn.execute("select p.pid, p.tmdb_id, p.text from tv_passages p left join tv_pvec v on v.pid = p.pid "
                        "where v.pid is null").fetchall()
    print(f"  vectors to make: {len(todo):,}", flush=True)
    t1 = time.time()
    for s in range(0, len(todo), 256):
        part = todo[s:s + 256]
        try:
            vecs = ollama.embed(model=EMBED_MODEL, input=[t[:2000] for _p, _s, t in part], keep_alive="30m")["embeddings"]
        except Exception as e:  # noqa: BLE001
            print(f"  embedding failed ({str(e)[:100]}); the rest next run")
            break
        conn.executemany("insert or replace into tv_pvec values (?,?,?)",
                         [(p, sid, np.asarray(v, dtype=np.float16).tobytes()) for (p, sid, _t), v in zip(part, vecs)])
        conn.commit()
        if (s // 256) % 20 == 0:
            print(f"    {s + len(part):,}/{len(todo):,} ({time.time() - t1:.0f}s)", flush=True)
    F.set_meta(conn, "tv_passages_at", F.now())
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "try", "show", "routecheck", "report"])
    ap.add_argument("text", nargs="*")
    ap.add_argument("--facts")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--no-vectors", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "routecheck":
        return routecheck(a.text)
    if a.cmd == "report":
        return report(a.text)
    global TV
    TV = True
    q = " ".join(a.text)
    if a.cmd == "show":
        ns = named_show(q, require_tv_words=True)
        print(context(hit(ns[1])) if ns else "no such show")
        return
    print("why TV:", wants_tv(q))
    for h in retrieve_tv(q):
        print(f"  {h['title']} ({h['years']}) d={h['distance']}  plan={h.get('_plan')}")


if __name__ == "__main__":
    main()
