"""
v3 tools (TOOLS=1): structured lookups and analysis context from facts.db, chosen per question.

v2 answered every question from the same material: up to 3 films' TMDB records plus their
Wikipedia intro and the closest plot passage. That is right for "what's that movie where..."
and wrong for "why did Oppenheimer do so well?", "compare the budgets of Dune and Dune: Part
Two", "which sci-fi directors made money on moderate budgets in the 2010s?" or "what's coming
out next month?" - questions whose answer is in numbers, credits, dates and review text, not
in a plot synopsis.

    question ─► planner (retrieve.py, unchanged) ─► route() ─► tools ─► context blocks
                                                     │ rules on the question's words +
                                                     │ the plan; an LLM only for aggregate
                                                     │ questions that need a query spec
                                                     ▼
          facts      budget / gross (best source + conflicts), runtime, dates, crew, cast,
                     franchise, awards, ratings (TMDB, MovieLens)             SQL
          compare    the same facts side by side for 2-3 named films         SQL
          market     release-window competition, genre-year baseline,
                     director track record, franchise history                SQL
          sections   Wikipedia Production / Reception / Box office / Themes
                     passages closest to the question, for the named films  vectors (per film)
          reviews    audience + critic aspect counts with quotes             SQL (reviews_analyze.py)
          people     filmography / career stats / recurring keywords         SQL
          aggregate  rankings and trends across many films ("which directors...") SQL from an LLM spec
          current    now playing / upcoming / announced, by date and genre   SQL (fetch_tmdb.py current)
          perf       v5: the box-office model's expectation and its factors   SQL (performance.py fit)

Every line handed to the answer model carries a source tag ([TMDB], [Wikipedia infobox],
[Wikidata], [Wikipedia: Reception], [TMDB user reviews], [MovieLens]) and, for data that
changes, the date it was fetched. Conflicting money figures are shown together, never
silently resolved. The analysis answer format (answer_v3.py) separates evidence from
interpretation.

    python tools.py build                 # money table (best figure per film) + MovieLens stats
    python tools.py try "why did Jaws do so well?"     # show the route and the blocks, no answer
"""
import datetime
import json
import os
import re
import sqlite3
import statistics
import sys
import time

import facts_db as F

TOOLS = os.environ.get("TOOLS", "0") == "1"
SPEC_MODEL = os.environ.get("SPEC_MODEL", "llama3.2")
TOOL_CHARS = int(os.environ.get("TOOL_CHARS", "9000"))    # all tool blocks together (~2.3k tokens)
# 7 Oct (context-window experiment): Wikipedia section passages per film and characters each.
SECTION_N = int(os.environ.get("SECTION_N", "3"))
SECTION_CHARS = int(os.environ.get("SECTION_CHARS", "650"))
PROFIT_X = 2.5        # gross >= 2.5 x budget counts as profitable (rule of thumb, stated in answers)
# v4 (14 Oct) switches, each off until its run in run_v4.sh says otherwise:
#   NEWS       news at release (news.py): "news" task for news questions, and with every "why" question
#   SUCCESS    the fixed success definition: money / reception / recognition measures computed against
#              films of the same genre and year, plus an "evidence is thin" line under 10 analysed reviews
#   ROUTE_FIX  comparison wording ("X vs Y", "which made more") finds both films even when retrieval
#              returned only one; a named film with no route gets its facts
NEWS = os.environ.get("NEWS", "0") == "1"
SUCCESS = os.environ.get("SUCCESS", "0") == "1"
ROUTE_FIX = os.environ.get("ROUTE_FIX", "0") == "1"
EVIDENCE_MIN = 10
# v5 (15 Oct evening) switches:
#   PERF           the box-office model's block (performance.py fit) for "why" and hit/flop questions
#   CRITIC_SCORES  Rotten Tomatoes / Metacritic / CinemaScore as numbers (money_extra.py critics) in the
#                  reviews and success blocks
#   MONEY_REAL     budget and gross in today's dollars (money_extra.py real) in the facts block
PERF = os.environ.get("PERF", "0") == "1"
CRITIC_SCORES = os.environ.get("CRITIC_SCORES", "0") == "1"
MONEY_REAL = os.environ.get("MONEY_REAL", "0") == "1"
# v6 (20 Oct night) switch:
#   EXTRA_DATA  the free data from fetch_free_data.py (extra_data.py): OMDb critic scores where Wikipedia had
#               none, Guardian / NYT review lines in the reviews block, a "watch" block (where to stream,
#               age rating, trailer) for where-to-watch questions, and an "audience" block (IMDb, MovieLens
#               32M, Netflix views, trailer lead time and reach) with why / success / reviews questions
EXTRA_DATA = os.environ.get("EXTRA_DATA", "0") == "1"
# v7 (27 Oct) switch, off until the reception set's rule passes (RECEPTION on if passes >= off + 6, unsupported rate
# not higher, grounding not lower):
#   RECEPTION  reception questions get the film's reception card (reception_cards.py) and numbered evidence from the
#              reception index (evidence.py) instead of the v3 reviews block; the answer cites [n] and the site lists
#              the cited sources under it. Questions about many films ("which films did critics and audiences
#              disagree on", "most divisive", "cult classics", "mixed reviews but made money") get ranked SQL lists.
RECEPTION = os.environ.get("RECEPTION", "0") == "1"
# 4 Nov: ISSUES=1 (needs RECEPTION=1) - questions about how people viewed an issue a film raised ("How did audiences
# react to Joker's portrayal of mental illness?", "Was Barbie's feminism divisive?") go to the reception task with
# the issue as the evidence query, the film's Wikipedia Themes / Controversy paragraphs among the evidence
# (evidence.py "themes" units), and their own answer paragraph (chat.ISSUE_HOW). ISSUES=0 = the 3 Nov routing.
ISSUES = os.environ.get("ISSUES", "0") == "1"
R_ISSUE_TOPIC = re.compile(r"\b(portray\w*|depict\w*|representation|represent(s|ed)?|treatment of|handl(e|ed|ing|es) "
                           r"(of |the )?|take on|message|messaging|commentary|themes?|stance|politics|political|"
                           r"controvers\w*|backlash|offensive|problematic|accurate|accuracy|inaccura\w*|sexis\w*|racis\w*|"
                           r"feminis\w*|gender|mental (illness|health)|violence|religio\w*|class|capitalis\w*|grief|"
                           r"trauma|addiction|masculinity|race|queer|lgbt\w*|disabilit\w*|war|colonial\w*|propaganda)\b",
                           re.I)
R_ISSUE_VIEW = re.compile(r"\b(how (did|do|does|was|were|is|are) (people|audiences?|viewers|critics|fans|the public|"
                          r"it|its|the film|the movie)|react\w*|respond\w*|view(ed|s)?|receiv\w*|felt|feel|"
                          r"think|thought|debate\w*|divid\w*|divisive|split|discuss\w*|criticiz\w*|criticis\w*|"
                          r"prais\w*|controvers\w*|backlash|offend\w*|opinions?)\b", re.I)


def is_issue_question(q):
    return bool(q) and bool(R_ISSUE_TOPIC.search(q)) and bool(R_ISSUE_VIEW.search(q))


IMDB = os.environ.get("IMDB", "0") == "1"                      # v7 D2: IMDb rating line in every film's facts
RECEPTION_K = int(os.environ.get("RECEPTION_K", "8"))          # evidence units per question (split across films)
# 21 Oct: per-person settings for the one question being answered (web/server.py sets it around prepare and
# clears it after; the site answers one question at a time). hide = {"money", "critics"}: those lines are left
# out of the blocks unless the question itself asks about money / reviews. Empty outside the site.
from request_state import LocalDict as _LocalDict   # v7 D4: per-thread
REQUEST = _LocalDict(lambda: {"hide": set()})
R_MONEY_LINE = re.compile(r"\b(budget|gross|box[- ]office|revenue|profit|earned|opening weekend|returned \d|"
                          r"break-?even|today's dollars|\$\d)", re.I)
R_CRITIC_LINE = re.compile(r"^(Critics' scores|The Guardian \(|The New York Times \(|Audience reviews|Critics \(from)"
                           r"|Rotten Tomatoes|Metacritic|CinemaScore|\[(Guardian|NYT) review\]", re.I)
R_PERF = re.compile(r"\b(hit|flop\w*|bomb\w*|success\w*|do(es)? well|did (well|badly|poorly)|perform\w*|"
                    r"expect\w*|under-?perform\w*|over-?perform\w*|disappoint\w*|exceed\w*|surpris\w*)\b", re.I)

_DB = {"conn": None, "tried": False}


def db():
    if not _DB["tried"]:
        _DB["tried"] = True
        _DB["conn"] = F.connect(readonly=True)
    return _DB["conn"]


def has(table):
    c = db()
    if c is None:
        return False
    try:
        return c.execute(f"select 1 from {table} limit 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ================================================================== routing
_W = lambda p: re.compile(p, re.I)   # noqa: E731
R_CURRENT = _W(r"\b(coming out|upcoming|come out (soon|next|this)|out (soon|next)|releas\w* (soon|next|this|later)|"
               r"in (theaters|theatres|cinemas)|now playing|playing now|out now|new releases?|just came out|"
               r"this (week|weekend|month)|next (week|month|year)|later this year|currently (showing|playing))\b")
R_WHY = _W(r"\bwhy\b.{0,80}\b(succe\w*|work(ed|s)?|flop\w*|fail\w*|bomb\w*|popular|hit|so well|so badly|perform\w*|"
           r"underperform\w*|beloved|loved|hated|disappoint\w*|cult|overrated|underrated|"
           r"(make|made|earn|earned|lose|lost) (so much |so little |a lot of |more |less |any )?money)\b"
           r"|\bwhat made\b.{0,60}\b(work|successful|a hit|so good|so popular|special|fail|flop)\b"
           r"|\b(succe\w*|flop\w*|fail\w*|work(ed)?) (because|due to)\b|\bdriven (more )?by\b"
           r"|\bhow did\b.{0,60}\b(do|perform) at the box office\b|\bexpectations\b")
R_REVIEWS = _W(r"\b(critics?|reviews?|reviewers?|audiences?|viewers|fans|moviegoers|people)\b.{0,40}\b(like[ds]?|love[ds]?|hate[ds]?|"
               r"dislike[ds]?|prais\w*|critici\w*|think|thought|say|said|react\w*|respond\w*|receiv\w*)\b"
               r"|\breception\b|\bwell[- ]received\b|\bcritical(ly)?\b|\brotten\b|\breviews?\b"
               r"|\bhow was\b.{0,60}\breceived\b|\b(liked|disliked|hated) (it|about)\b")
R_MONEY = _W(r"\b(budgets?|box[- ]office|gross(ed|es)?|revenue|earn(ed|ings)?|made (more |the most |less )?money|profit\w*|"
             r"cost|how much did|opening weekend|financial\w*|commercial\w*|roi)\b")
R_FACTS = _W(r"\b(runtime|how long is|length|release date|when (did|does|was|is)\b.{0,40}\b(come out|released?|premiere)|"
             r"who (wrote|scored|composed|shot|produced|edited)|writer|composer|cinematograph\w*|producer|studio|"
             r"franchise|sequel|prequel|series|rated|rating)\b")
R_PRODUCTION = _W(r"\b(filmed|shot (in|on|at)|filming|production|behind the scenes|making of|casting|cast as|"
                  r"developed|development|special effects|visual effects|cgi|practical effects|soundtrack|score|"
                  r"music|composed|camera|budget overruns?|reshoots?|troubled|stunts?|sets?|locations?)\b"
                  r"|\bhow did they (film|shoot|make|do|create|pull off)\b")
R_THEMES = _W(r"\b(themes?|meaning|symboli\w*|message|interpret\w*|allegor\w*|what is .{0,30} really about)\b")
R_AWARDS = _W(r"\b(awards?|oscars?|academy awards?|nominat\w*|golden globes?|won best|baftas?|palme|accolades?)\b")
R_PEOPLE = _W(r"\b(career|filmograph\w*|track record|over (time|the years)|(early|earlier|later|recent) (films|movies|work)|"
              r"style (changed|evolved|develop\w*)|throughout (his|her|their)|body of work|directors?|actors?|actresses|"
              r"filmmakers?|directed by|starring)\b|\b[A-Z][a-z]+(?: [A-Z][a-z]+)*'s (films|movies|work|career|filmography)\b"
              r"|\bcompare [A-Z][a-z]+ [A-Z][a-z]+ (and|with|to|vs\.?) [A-Z][a-z]+ [A-Z][a-z]+\b")
R_NEWS = _W(r"\b(news|headlines?|current events|zeitgeist|world events|cultural moment|political climate|"
            r"national mood|what was (going on|happening)|at the time|the times|timing|"
            r"(during|amid|after|before|right after|in the wake of) (the |a )?(war|pandemic|covid|recession|"
            r"election|attacks?|9/11|strike|crisis|depression|lockdown)|9/11|september 11|pandemic|covid|"
            r"recession|real[- ]world events?)\b")
R_WATCH = _W(r"\b(where (can|could|do|should|to) (i |we |you )?(watch|stream|see|rent|buy|find)|stream(ing|ed)?|"
             r"netflix|hulu|prime video|disney\+|disney plus|hbo( max)?|peacock|paramount\+|apple tv|tubi|"
             r"rent(al)?|on demand|available (on|to)|trailers?|teasers?|age rating|rated (g|pg|pg-13|r|nc-17)\b|"
             r"mpaa|parental|kid[- ]friendly|family[- ]friendly|appropriate for|ok for (kids|children))")
R_RECEPTION = _W(r"\b(popular|loved|beloved|hated|divisive|divided|polari[sz]\w*|mixed reviews?|disagree\w*|"
                 r"complain\w*|complaints?|prais\w*|critici[sz]\w*|reactions?|react\w*|reception|received|opinions?|"
                 r"verdict|consensus|cult (classic|following|film|status)|reappraise\w*|re-?evaluat\w*|underrated|"
                 r"overrated|aged (well|badly|poorly)|hold(s)? up|video essays?|essayists?|youtubers?|analysts?|"
                 r"breakdowns?|reviewers?|critics?|audiences?|fans|viewers|moviegoers|word of mouth|"
                 r"what did .{0,40}\b(think|say|make of)|reputation|standing|legacy|regarded|seen (today|now)|"
                 r"new york times|nyt|the guardian|variety|hollywood reporter|roger ebert|rotten tomatoes|metacritic|"
                 r"did .{0,50}\b(like|enjoy|love|hate|dislike))\b")
R_REC_GAP = _W(r"\bcritics?\b.{0,60}\b(audiences?|fans|viewers|people|moviegoers|the public)\b.{0,60}\b(disagree\w*|"
               r"lov(e|ed)|hat(e|ed)|lik(e|ed)|dislik(e|ed)|split|gap|differ\w*|more)\b|\b(audiences?|fans|viewers|people)\b"
               r".{0,40}\b(lov(e|ed)|lik(e|ed)|enjoy(ed)?)\b.{0,40}\bcritics?\b.{0,20}\b(hat(e|ed)|dislik(e|ed)|pann(ed)?|"
               r"didn'?t)\b|\b(critics and audiences|audiences and critics)\b.{0,40}\b(disagree\w*|split|differ\w*)\b")
R_REC_DIVISIVE = _W(r"\b(most |more )?(divisive|polari[sz]\w*|split (audiences|opinion|viewers)|love[- ]it[- ]or[- ]"
                    r"hate[- ]it|strongest (audience )?reactions?|most (controversial|debated))\b")
R_REC_REAPPRAISE = _W(r"\b(cult (classics?|films?|movies?|following)|reappraise\w*|re-?evaluat\w*|under-?appreciated|"
                      r"initially (panned|flopped|bombed|hated|dismissed)|aged well|found (its|an|their) audience|"
                      r"better (with|over) time|grew on)\b")
R_REC_MIXED_MONEY = _W(r"\b(mixed|bad|poor|negative|weak|middling) reviews?\b.{0,80}\b(made (a lot of |so much |good |"
                       r"big |lots of )?money|profit\w*|box[- ]office|hits?|success\w*|earn\w*|gross\w*)\b|"
                       r"\b(made (a lot of |so much |good |big )?money|profit\w*|hits?|success\w*|box[- ]office)\b"
                       r".{0,60}\b(despite|but|even though|with)\b.{0,30}\b(mixed|bad|poor|negative|weak) reviews?\b")
R_COMPARE = _W(r"\b(compare\w*|comparison|versus|vs\.?|better than|worse than|differ\w*|"
               r"which (one |film |movie )?(made|earned|cost|did|was|is|had)|both)\b")
R_AGG = _W(r"\b(which|what|who|top|most|least|highest|lowest|best|worst)\b.{0,60}\b(directors?|actors?|actresses|"
           r"filmmakers?|studios?|genres?|years?|movies|films)\b.{0,80}\b(most|best|highest|lowest|consistently|"
           r"multiple|profitable|successful|track record|budgets?|box office|gross\w*|rated|money|records?|"
           r"outperform\w*|ratio|roi)\b"
           r"|\b(in|during) (the )?(\d{4}s?|\d0s)\b.{0,40}\b(successful|popular|profitable|box office)\b"
           r"|\b(successful|popular|profitable)\b.{0,40}\b(in|during) (\d{4}|the \d{2,4}s)\b"
           r"|\bcompare\b.{0,40}\b(directors?|actors?|careers?)\b|\bover the years\b|\btrend\w*\b"
           r"|\b(highest|top|biggest|lowest|best|worst)[- ](grossing|rated|earning|budget|reviewed)\b")


def route(question, plan, hits):
    """Which tools to run. Rules on the question's words and the plan; no model call.
    Returns {"tasks": [...], "films": [hit dicts the question is about], "why": bool}."""
    q = question or ""
    intent = (plan or {}).get("intent") or ""
    hits = hits or []
    about = []
    if intent == "identify":
        about = hits[:1]
    elif intent in ("named_film", "other") and hits:
        import retrieve as R
        want = [R.normalize(t) for t in (plan or {}).get("films") or []]
        by = {R.normalize(h["title"]): h for h in hits}
        about = [by[w] for w in want if w in by]
        if not about and intent == "named_film":
            about = hits[:1]
    tasks = []
    # A plot description gets its film's facts only when the question also asks for them
    # ("what's that movie ... and how much did it make?"); the v2 answer is otherwise unchanged.
    auto_facts = intent != "identify"
    if R_CURRENT.search(q):
        tasks.append("current")
    if R_AGG.search(q) or (R_PEOPLE.search(q) and intent in ("other", "recommend")):
        tasks.append("aggregate")
    if about and not auto_facts and not (R_MONEY.search(q) or R_FACTS.search(q) or R_WHY.search(q)
                                         or R_REVIEWS.search(q) or R_AWARDS.search(q)):
        about = []
    if ROUTE_FIX and R_COMPARE.search(q) and intent in ("named_film", "other"):
        # 7 Oct sealed set: compare questions got the compare route 0/3 times, because the second
        # film was not among retrieve()'s 3 hits. Take every film the planner named, from facts.db
        # when retrieval missed it.
        have = {(h.get("title") or "").casefold() for h in about}
        for t in (plan or {}).get("films") or []:
            title = t.get("title") if isinstance(t, dict) else t
            if title and title.casefold() not in have:
                extra = facts_hits([title], year=(t.get("year") if isinstance(t, dict) else None))
                if extra:
                    about.append(extra[0])
                    have.add(extra[0]["title"].casefold())
        about = about[:3]
    rec_list = None
    if RECEPTION and not about:
        # v7 R15: reception questions about many films are answered from the reception cards in SQL (E2, E4, R8)
        rec_list = ("mixed_money" if R_REC_MIXED_MONEY.search(q) else "gaps" if R_REC_GAP.search(q) else
                    "divisive" if R_REC_DIVISIVE.search(q) else "reappraised" if R_REC_REAPPRAISE.search(q) else None)
        if rec_list:
            tasks = [t for t in tasks if t != "aggregate"] + ["reception_list"]
    if about:
        tasks.append("facts")
        if len(about) > 1:
            tasks.append("compare")
        why = bool(R_WHY.search(q))
        issue = bool(ISSUES and RECEPTION and is_issue_question(q))
        if RECEPTION and (R_REVIEWS.search(q) or R_RECEPTION.search(q) or why or issue):
            tasks.append("reception")          # v7 R15: card + numbered evidence (replaces the v3 reviews block)
        if why:
            tasks += ["market", "sections", "reviews"]
            if SUCCESS:
                tasks.append("success")
            if PERF:
                tasks.append("perf")
            if NEWS:
                tasks.append("news")
        elif NEWS and R_NEWS.search(q):
            tasks += ["news", "sections"]
        else:
            if R_REVIEWS.search(q):
                tasks += ["reviews", "sections"]
            if R_PRODUCTION.search(q) or R_THEMES.search(q) or R_MONEY.search(q) or R_AWARDS.search(q):
                tasks.append("sections")
            if R_MONEY.search(q) and len(about) == 1:
                tasks.append("market")
            if SUCCESS and (R_MONEY.search(q) or R_REVIEWS.search(q)) and R_COMPARE.search(q):
                tasks.append("success")
            if PERF and R_PERF.search(q):
                tasks.append("perf")
        if EXTRA_DATA:
            if R_WATCH.search(q):
                tasks.append("watch")
            if why or R_MONEY.search(q) or "success" in tasks or "perf" in tasks:
                tasks.append("boxoffice")              # 21 Oct: Box Office Mojo via the IMDb API, when fetched
            if why or "success" in tasks or "perf" in tasks or R_REVIEWS.search(q):
                tasks.append("audience")
    else:
        why = False
        issue = False
    if "reception" in tasks:            # the card already has the reviews and audience numbers
        tasks = [t for t in tasks if t not in ("reviews", "audience")]
    seen, out = set(), []
    for t in tasks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return {"tasks": out, "films": about, "why": why, "issue": issue,
            "kinds": _section_kinds(q, why), "reception_list": rec_list}


def _section_kinds(q, why):
    kinds = []
    if why or R_MONEY.search(q) or (NEWS and R_NEWS.search(q)):
        kinds += ["box_office", "release"]
    if why or R_REVIEWS.search(q):
        kinds += ["reception"]
    if R_PRODUCTION.search(q):
        kinds += ["production", "music"]
    if R_THEMES.search(q):
        kinds += ["themes", "legacy"]
    if R_AWARDS.search(q):
        kinds += ["accolades"]
    if why:
        kinds += ["legacy", "production"]
    return list(dict.fromkeys(kinds)) or ["reception", "production", "box_office", "themes"]


# ================================================================== facts
def money(fid):
    """{'budget': (value, source, [(value, source)...], conflict), 'gross': (...)} in USD."""
    c = db()
    out = {}
    if c is None:
        return out
    try:
        row = c.execute("select budget, budget_src, budget_alt, gross, gross_src, gross_alt, roi, conflict "
                        "from money where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        if row[0]:
            out["budget"] = (row[0], row[1], json.loads(row[2] or "[]"), bool(row[7] & 1))
        if row[3]:
            out["gross"] = (row[3], row[4], json.loads(row[5] or "[]"), bool(row[7] & 2))
        if row[6]:
            out["roi"] = row[6]
    return out


SRC = {"tmdb": "TMDB", "wikipedia_infobox": "Wikipedia infobox", "wikidata": "Wikidata",
       "wikipedia_infobox_fx": "Wikipedia infobox, converted to USD at that year's rate",
       "wikidata_fx": "Wikidata, converted to USD at that year's rate"}


def usd(v):
    if v is None:
        return "?"
    if v >= 1e9:
        return f"${v / 1e9:.2f} billion"
    if v >= 1e6:
        return f"${v / 1e6:.1f} million"
    return f"${v:,.0f}"


def _money_line(name, m):
    v, src, alts, conflict = m
    s = f"{name}: {usd(v)} [{SRC.get(src, src)}]"
    others = [(a, b) for a, b in alts if b != src]
    if conflict and others:
        s += " - sources disagree: " + ", ".join(f"{usd(a)} [{SRC.get(b, b)}]" for a, b in others)
    return s


def film_row(fid):
    c = db()
    if c is None:
        return None
    return c.execute("select tmdb_id, title, year, release_date, us_release_date, runtime, genres_json, "
                     "companies_json, collection_id, collection_name, vote_average, vote_count, overview, "
                     "tier, tmdb_fetched_at, original_language, countries_json from films where tmdb_id = ?",
                     (fid,)).fetchone()


def crew(fid):
    c = db()
    out = {}
    for job, name in c.execute("select c.job, p.name from credits c join people p on p.person_id = c.person_id "
                               "where c.tmdb_id = ? and c.kind = 'crew'", (fid,)):
        out.setdefault(job, [])
        if name not in out[job]:
            out[job].append(name)
    cast = [f"{n} as {ch}" if ch else n for n, ch in c.execute(
        "select p.name, c.character from credits c join people p on p.person_id = c.person_id "
        "where c.tmdb_id = ? and c.kind = 'cast' order by c.ord limit 6", (fid,))]
    return out, cast


def awards(fid):
    c = db()
    rows = c.execute("select award, status, year from awards where tmdb_id = ?", (fid,)).fetchall()
    if not rows:
        return None
    won = [a for a, s, _y in rows if s == "won" and a]
    nom = [a for a, s, _y in rows if s == "nominated" and a]
    oscars_won = sum(1 for a in won if "Academy Award" in a)
    oscars_nom = sum(1 for a in nom if "Academy Award" in a)
    top = [a for a in won if "Academy Award" in a][:4] or won[:4]
    s = f"Awards: {len(won)} wins, {len(nom)} nominations listed"
    if oscars_won or oscars_nom:
        s += f" (Academy Awards: {oscars_won} won, {oscars_nom} nominated)"
    if top:
        s += "; won include " + "; ".join(top)
    return s + " [Wikidata]"


def ml_line(fid):
    c = db()
    try:
        row = c.execute("select n_ratings, mean from ml_stats where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        return None
    if row and row[0] >= 20:
        return f"MovieLens audience: {row[1]:.2f}/5 from {row[0]:,} ratings [MovieLens]"
    return None


def imdb_line(fid):
    """v7 D2 (IMDB=1): the IMDb users' rating and votes, or None."""
    c = db()
    try:
        r = c.execute("select r.rating, r.votes, r.fetched_at from films f join imdb_ratings r on r.imdb_id = f.imdb_id "
                      "where f.tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        return None
    if r and r[0] and (r[1] or 0) >= 50:
        return f"IMDb users: {r[0]:.1f}/10 from {r[1]:,} votes [IMDb, {str(r[2])[:7]}]"
    return None


def real_line(fid):
    """Budget and gross in today's dollars (money_extra.py real), or None."""
    c = db()
    try:
        r = c.execute("select budget_real, gross_real, year, base_year from money_real where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        return None
    if not r or not (r[0] or r[1]) or r[2] == r[3]:
        return None
    bits = ([f"budget {usd(r[0])}"] if r[0] else []) + ([f"gross {usd(r[1])}"] if r[1] else [])
    return f"In {r[3]} dollars (adjusted with the US consumer price index from {r[2]}): " + ", ".join(bits) + " [computed]"


def critic_line(fid):
    """Critics' scores stated in the film's Wikipedia reception text (money_extra.py critics), or None."""
    c = db()
    try:
        r = c.execute("select rt_pct, rt_n, rt_avg, mc_score, mc_n, cinemascore, snapshot from critic_scores "
                      "where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    rt_pct, rt_n, rt_avg, mc, mc_n, cs, snap = r
    bits = []
    if rt_pct is not None:
        bits.append(f"Rotten Tomatoes {rt_pct:.0f}% positive" + (f" of {rt_n:,} reviews" if rt_n else "")
                    + (f", average {rt_avg:g}/10" if rt_avg is not None else ""))
    if mc is not None:
        bits.append(f"Metacritic {mc:.0f}/100" + (f" from {mc_n} critics" if mc_n else ""))
    if cs:
        bits.append(f"CinemaScore (opening-night audiences) {cs}")
    if not bits:
        return None
    when = f"as stated on Wikipedia, {str(snap)[:7]}" if snap else "as stated on Wikipedia"
    return "Critics' scores: " + "; ".join(bits) + f" [Wikipedia: Reception, {when}]"


def facts_block(fid, short=False):
    """The film's structured facts as tagged lines, or None if facts.db lacks it."""
    r = film_row(fid)
    if not r:
        return None
    (_fid, title, year, rd, usrd, runtime, genres, companies, coll_id, coll, va, vc, _ov,
     tier, fetched, lang, countries) = r
    lines = [f"{title} ({year or '?'}) - structured data"]
    bits = []
    if rd:
        bits.append(f"released {rd}" + (f" (US theatrical {usrd})" if usrd and usrd != rd else ""))
    if runtime:
        bits.append(f"{runtime} min")
    if genres:
        bits.append(", ".join(json.loads(genres)[:4]))
    if lang:
        bits.append(f"language {lang}")
    if bits:
        lines.append("; ".join(bits) + " [TMDB]")
    m = money(fid)
    if "budget" in m:
        lines.append(_money_line("Budget", m["budget"]))
    if "gross" in m:
        lines.append(_money_line("Worldwide gross", m["gross"]))
    if "roi" in m:
        lines.append(f"Gross / budget: {m['roi']:.1f}x (break-even is roughly {PROFIT_X}x once marketing and "
                     f"the theaters' share are counted - a rule of thumb, not data)")
    if MONEY_REAL:
        rl = real_line(fid)
        if rl:
            lines.append(rl)
    cr, cast = crew(fid)
    for job, label in (("Director", "Director"), ("Screenplay", "Screenplay"), ("Writer", "Writer"),
                       ("Novel", "Based on novel by"), ("Director of Photography", "Cinematographer"),
                       ("Original Music Composer", "Composer"), ("Editor", "Editor"), ("Producer", "Producers")):
        if cr.get(job) and not (short and job in ("Editor", "Producer", "Writer")):
            lines.append(f"{label}: {', '.join(cr[job][:3])} [TMDB]")
    if cast and not short:
        lines.append(f"Cast: {', '.join(cast[:6])} [TMDB]")
    if coll:
        lines.append(f"Franchise: {coll} [TMDB]")
    if companies and not short:
        cs = json.loads(companies)[:3]
        if cs:
            lines.append(f"Studios: {', '.join(cs)} [TMDB]")
    if va and vc:
        lines.append(f"TMDB user rating {va:.1f}/10 from {vc:,} votes [TMDB, fetched {str(fetched)[:10]}]")
    ml = ml_line(fid)
    if ml:
        lines.append(ml)
    if IMDB:
        il = imdb_line(fid)
        if il:
            lines.append(il)
    aw = awards(fid)
    if aw:
        lines.append(aw)
    if tier in ("upcoming", "now_playing"):
        lines.append(f"Status: {tier.replace('_', ' ')} as of {str(fetched)[:10]} [TMDB]")
    return "\n".join(lines)


def compare_block(fids):
    """Side-by-side numbers for 2-3 films."""
    rows = []
    for fid in fids[:3]:
        r = film_row(fid)
        if not r:
            continue
        m = money(fid)
        cr, _ = crew(fid)
        rows.append((r[1], r[2], r[5], m.get("budget", (None,))[0], m.get("gross", (None,))[0], m.get("roi"),
                     r[10], r[11], ", ".join(cr.get("Director", [])[:2])))
    if len(rows) < 2:
        return None
    out = ["Side by side: title | year | runtime | budget | worldwide gross | gross/budget | TMDB rating | director"]
    for t, y, rt, b, g, roi, va, vc, d in rows:
        out.append(f"{t} | {y} | {rt or '?'} min | {usd(b) if b else '?'} | {usd(g) if g else '?'} | "
                   f"{f'{roi:.1f}x' if roi else '?'} | {f'{va:.1f} ({vc:,} votes)' if va else '?'} | {d or '?'}")
    out.append("(money from the best available source per film; see each film's lines for sources) ")
    return "\n".join(out)


# ================================================================== market context
def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def market_block(fid):
    """Release window, genre-year baseline, director track record, franchise history."""
    c = db()
    r = film_row(fid)
    if not r:
        return None
    title, year, rd, usrd, genres = r[1], r[2], r[3], r[4], json.loads(r[6] or "[]")
    lines = [f"Market context for {title} ({year})"]
    date = usrd or rd
    if date:
        try:
            d0 = datetime.date.fromisoformat(date[:10])
            lo, hi = (d0 - datetime.timedelta(days=14)).isoformat(), (d0 + datetime.timedelta(days=14)).isoformat()
            comp = c.execute("""select f.title, coalesce(f.us_release_date, f.release_date), m.gross
                from films f join money m on m.tmdb_id = f.tmdb_id
                where coalesce(f.us_release_date, f.release_date) between ? and ? and f.tmdb_id != ?
                  and m.gross > 0 order by m.gross desc limit 5""", (lo, hi, fid)).fetchall()
            if comp:
                lines.append(f"Released {date[:10]} ({d0.strftime('%B')}); other films within two weeks, by gross: " +
                             "; ".join(f"{t} ({dd[:10]}, {usd(g)})" for t, dd, g in comp) + " [TMDB / Wikipedia]")
        except ValueError:
            pass
    if genres and year:
        g = genres[0]
        rows = c.execute("""select m.gross, m.roi from films f join money m on m.tmdb_id = f.tmdb_id
            where f.year = ? and f.genres_json like ? and m.gross > 0""", (year, f'%"{g}"%')).fetchall()
        if len(rows) >= 5:
            mg, mr = _median([x[0] for x in rows]), _median([x[1] for x in rows])
            my = money(fid)
            rank = sorted([x[0] for x in rows], reverse=True).index(my["gross"][0]) + 1 \
                if "gross" in my and my["gross"][0] in [x[0] for x in rows] else None
            lines.append(f"{g} films released in {year} with box-office data: {len(rows)}; median gross {usd(mg)}"
                         + (f", median gross/budget {mr:.1f}x" if mr else "")
                         + (f"; this film ranks #{rank} of {len(rows)}" if rank else "") + " [TMDB / Wikipedia, computed]")
    cr, _ = crew(fid)
    for d in (cr.get("Director") or [])[:1]:
        prev = c.execute("""select f.title, f.year, m.gross, f.vote_average from credits k
            join people p on p.person_id = k.person_id join films f on f.tmdb_id = k.tmdb_id
            left join money m on m.tmdb_id = f.tmdb_id
            where p.name = ? and k.job = 'Director' and f.tmdb_id != ? and f.year < ?
            order by f.year desc limit 6""", (d, fid, year or 9999)).fetchall()
        if prev:
            lines.append(f"{d}'s earlier films: " + "; ".join(
                f"{t} ({y}{', ' + usd(g) if g else ''}{f', {va:.1f}/10' if va else ''})" for t, y, g, va in prev)
                + " [TMDB / Wikipedia]")
        else:
            lines.append(f"{d}: no earlier directing credits in the database (a first feature, or older work missing)")
    if r[8]:
        fr = c.execute("""select f.title, f.year, m.gross, f.vote_average from films f
            left join money m on m.tmdb_id = f.tmdb_id where f.collection_id = ? order by f.year""",
                       (r[8],)).fetchall()
        if len(fr) > 1:
            lines.append(f"{r[9]}: " + "; ".join(f"{t} ({y}{', ' + usd(g) if g else ''})" for t, y, g, _va in fr)
                         + " [TMDB]")
    return "\n".join(lines) if len(lines) > 1 else None


# ================================================================== Wikipedia sections
_SEC = {"mat": None, "ids": None}


def _sec_vectors(fid):
    c = db()
    import numpy as np
    rows = c.execute("""select s.sec_id, s.kind, s.section_key, s.text, v.emb from sections s
        join sec_vec v on v.sec_id = s.sec_id where s.tmdb_id = ?""", (fid,)).fetchall()
    if not rows:
        return [], None
    mat = np.stack([np.frombuffer(r[4], dtype=np.float32) for r in rows])
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    return rows, mat


def sections_block(fid, title, q_vec, kinds, n=3, chars=650):
    """The film's Wikipedia passages (non-plot) closest to the question, preferring the
    kinds the route asked for. Passages, not summaries: what the answer quotes is the text."""
    c = db()
    if c is None:
        return None
    rows, mat = ([], None)
    if q_vec is not None and has("sec_vec"):
        rows, mat = _sec_vectors(fid)
    if rows and mat is not None:
        import numpy as np
        qv = np.asarray(q_vec, dtype=np.float32)
        qv /= np.linalg.norm(qv) + 1e-9
        sim = mat @ qv
        bonus = np.array([0.05 if r[1] in kinds else 0.0 for r in rows], dtype=np.float32)
        order = list(np.argsort(-(sim + bonus))[:n])
        picked = [(rows[i][1], rows[i][2], rows[i][3]) for i in order]
    else:
        picked = []
        for k in kinds:
            for kind, key, text in c.execute("select kind, section_key, text from sections where tmdb_id = ? "
                                             "and kind = ? order by position limit 1", (fid, k)):
                picked.append((kind, key, text))
            if len(picked) >= n:
                break
    if not picked:
        return None
    out = [f"{title} - Wikipedia sections (2023 snapshot)"]
    for kind, key, text in picked:
        t = text if len(text) <= chars else text[:chars].rsplit(" ", 1)[0] + " ..."
        out.append(f"[Wikipedia: {key.title()}] {t}")
    return "\n".join(out)


# ================================================================== reviews
def reviews_block(fid, title):
    c = db()
    if c is None:
        return None
    lines = []
    kinds = [("audience", "Audience reviews (TMDB users)"), ("critic", "Critics (from Wikipedia's reception text)")]
    if EXTRA_DATA:                 # 21 Oct: reviews_analyze.py --kinds pro,ytc
        kinds += [("pro", "Professional reviews (the Guardian, the New York Times)"),
                  ("ytc", "Comments under the trailer on YouTube (mostly written after release)")]
    for st, label in kinds:
        try:
            rows = c.execute("select aspect, pos, neg, mixed, n_reviews, quotes_json from film_aspects "
                             "where tmdb_id = ? and source_type = ? order by (pos + neg + mixed) desc limit 6",
                             (fid, st)).fetchall()
        except sqlite3.Error:
            rows = []
        if not rows:
            continue
        n = rows[0][4]
        parts = []
        for aspect, pos, neg, mixed, _n, quotes in rows:
            qs = json.loads(quotes or "[]")[:1]
            parts.append(f"{aspect}: {pos} positive, {neg} negative" + (f", {mixed} mixed" if mixed else "")
                         + (f' ("{qs[0]}")' if qs else ""))
        src = {"audience": "TMDB user reviews", "critic": "Wikipedia: Reception", "pro": "Guardian / NYT reviews",
               "ytc": "YouTube trailer comments"}[st]
        lines.append(f"{label}, {n} analysed - " + "; ".join(parts) + f" [{src}, aspects by Movie Buff]")
    if CRITIC_SCORES or EXTRA_DATA:
        cl = critic_line(fid) if CRITIC_SCORES else None
        if not cl and EXTRA_DATA:
            try:
                import extra_data as _x
                cl = _x.critic_line(fid)              # OMDb, for films whose Wikipedia text had no score
            except Exception:  # noqa: BLE001
                cl = None
        if cl:
            lines.insert(0, cl)
    if not [x for x in lines if not x.startswith("Critics' scores")]:
        n = c.execute("select count(*) from reviews where tmdb_id = ?", (fid,)).fetchone()[0]
        if n:
            ex = c.execute("select content, rating from reviews where tmdb_id = ? order by length(content) "
                           "limit 2", (fid,)).fetchall()
            lines.append(f"{n} TMDB user reviews (not yet analysed); e.g. " + " | ".join(
                f"{'(' + str(rt) + '/10) ' if rt else ''}{' '.join(t.split())[:220]}" for t, rt in ex) + " [TMDB user reviews]")
    if EXTRA_DATA:
        try:
            import extra_data as _x
            lines += _x.critic_review_lines(fid)      # Guardian (stars) and NYT professional reviews
        except Exception:  # noqa: BLE001
            pass
    return (f"{title} - what reviewers said\n" + "\n".join(lines)) if lines else None


# ================================================================== success (v4, SUCCESS=1)
def _peers(c, fid, year, genre):
    """Films of the same first genre and year (or decade when the year has < 10) with money data."""
    for lo, hi in ((year, year), ((year // 10) * 10, (year // 10) * 10 + 9)):
        rows = c.execute("""select f.tmdb_id, m.roi, f.vote_average, f.vote_count from films f
            join money m on m.tmdb_id = f.tmdb_id where f.year between ? and ? and f.genres_json like ?""",
                         (lo, hi, f'["{genre}"%')).fetchall()
        if len(rows) >= 10:
            return rows, (str(lo) if lo == hi else f"the {lo}s")
    return [], None


def _pct(values, v):
    vals = [x for x in values if x is not None]
    if not vals or v is None:
        return None
    return round(100 * sum(1 for x in vals if x < v) / len(vals))


def success_block(fid, title):
    """The fixed success definition (Future Work, 8 Oct): each measure computed in code against
    similar films, so the answer says which measure it means instead of the model judging 'success'."""
    c = db()
    r = film_row(fid)
    if not r:
        return None
    year, genres, va, vc = r[2], json.loads(r[6] or "[]"), r[10], r[11]
    lines = [f"{title} - success by Movie Buff's fixed measures (computed, not opinion)"]
    m = money(fid)
    peers, label = _peers(c, fid, year or 0, genres[0]) if (genres and year) else ([], None)
    roi = m.get("roi")
    if roi:
        verdict = ("lost money even before marketing costs" if roi < 1 else
                   f"below the ~{PROFIT_X}x break-even (studios keep roughly half of ticket sales; marketing is "
                   f"not in the budget)" if roi < PROFIT_X else f"profitable by the {PROFIT_X}x rule of thumb")
        p = _pct([x[1] for x in peers], roi)
        lines.append(f"Money: gross {roi:.1f}x budget - {verdict}"
                     + (f"; higher than {p}% of {len(peers)} {genres[0].lower()} films of {label}" if p is not None else "")
                     + " [TMDB / Wikipedia / Wikidata, computed]")
    elif m.get("gross"):
        lines.append("Money: gross known but no budget, so no return on budget [computed]")
    else:
        lines.append("Money: no box-office figures stored")
    if va and vc and vc >= 50:
        p = _pct([x[2] for x in peers if (x[3] or 0) >= 50], va)
        lines.append(f"Reception: TMDB {va:.1f}/10 from {vc:,} votes"
                     + (f", higher than {p}% of {label}'s {genres[0].lower()} films" if p is not None else "")
                     + " [TMDB, computed]")
    try:
        asp = c.execute("select source_type, sum(pos), sum(neg), max(n_reviews) from film_aspects where tmdb_id = ? "
                        "group by source_type", (fid,)).fetchall()
    except sqlite3.Error:
        asp = []
    for st, pos, neg, n in asp:
        if pos or neg:
            who = "Audience reviews" if st == "audience" else "Critics (Wikipedia reception text)"
            lines.append(f"{who}: {100 * pos / (pos + neg):.0f}% of aspect mentions positive, {n} analysed"
                         + (f" - fewer than {EVIDENCE_MIN}: thin evidence, say so" if st == "audience" and n < EVIDENCE_MIN
                            else "") + " [aspects by Movie Buff]")
    if CRITIC_SCORES:
        cl = critic_line(fid)
        if cl:
            lines.append(cl)
    aw = c.execute("select sum(status = 'won'), sum(status = 'nominated') from awards where tmdb_id = ?", (fid,)).fetchone()
    if aw and (aw[0] or aw[1]):
        lines.append(f"Recognition: {aw[0] or 0} wins, {aw[1] or 0} nominations [Wikidata]")
    else:
        lines.append("Recognition: no awards listed [Wikidata]")
    lines.append("Staying power: not measured (no weekly box-office figures stored)")
    return "\n".join(lines)


# ================================================================== people + aggregates
GENRES = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
          "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "War", "Western"]

SPEC_SYSTEM = """Turn a question about movies into a database query spec. Reply with JSON only.

task: "filmography" (one or more named people's films / careers / styles), "ranking" (which films, people or years are best, worst, most, least by some measure), "trend" (how something changed across years), or "none" (anything else).
people: people the question names, [{"name": "...", "role": "director"|"actor"|"writer"|"composer"|"any"}].
group_by: what the answer lists - "film", "director", "actor", "genre" or "year".
genres: from this list only: Action, Adventure, Animation, Comedy, Crime, Documentary, Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, Science Fiction, Thriller, War, Western.
year_from, year_to: integers or null. "the 2010s" = 2010-2019.
metric: "gross", "roi" (gross divided by budget; "profitable", "made money", "outperformed its budget"), "rating", "budget", "count" or "awards".
min_films: films each person or group needs (1 unless the question says "multiple" = 2 or "consistently" = 3).
max_budget: US dollars or null ("moderate budgets" = 60000000, "low-budget" = 15000000).
order: "desc" or "asc". limit: 10 unless the question asks for a number.

Examples
Question: Which science-fiction directors consistently made commercially successful movies on moderate budgets?
{"task":"ranking","people":[],"group_by":"director","genres":["Science Fiction"],"year_from":null,"year_to":null,"metric":"roi","min_films":3,"max_budget":60000000,"order":"desc","limit":10}
Question: How has Christopher Nolan's style changed over time?
{"task":"filmography","people":[{"name":"Christopher Nolan","role":"director"}],"group_by":"film","genres":[],"year_from":null,"year_to":null,"metric":"rating","min_films":1,"max_budget":null,"order":"asc","limit":20}
Question: Why were horror movies so successful in 2017?
{"task":"trend","people":[],"group_by":"year","genres":["Horror"],"year_from":2012,"year_to":2019,"metric":"roi","min_films":1,"max_budget":null,"order":"asc","limit":10}
Question: Which actors have the best track record in comedies?
{"task":"ranking","people":[],"group_by":"actor","genres":["Comedy"],"year_from":null,"year_to":null,"metric":"rating","min_films":3,"max_budget":null,"order":"desc","limit":10}"""

_PERSON = {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
           "required": ["name", "role"]}
SPEC_SCHEMA = {"type": "object", "properties": {
    "task": {"type": "string", "enum": ["filmography", "ranking", "trend", "none"]},
    "people": {"type": "array", "items": _PERSON},
    "group_by": {"type": "string", "enum": ["film", "director", "actor", "genre", "year"]},
    "genres": {"type": "array", "items": {"type": "string"}},
    "year_from": {"type": ["integer", "null"]}, "year_to": {"type": ["integer", "null"]},
    "metric": {"type": "string", "enum": ["gross", "roi", "rating", "budget", "count", "awards"]},
    "min_films": {"type": "integer"}, "max_budget": {"type": ["integer", "null"]},
    "order": {"type": "string", "enum": ["desc", "asc"]}, "limit": {"type": "integer"}},
    "required": ["task", "people", "group_by", "genres", "year_from", "year_to", "metric", "min_films",
                 "max_budget", "order", "limit"]}


_SPEC_CACHE = {}


def spec_for(question):
    """One short structured-output call (SPEC_MODEL, default llama3.2: ~1 s). The model only
    fills in a spec; the SQL is written by code below, never by the model. Cached per process
    (the spec depends only on the question's words)."""
    key = " ".join(question.casefold().split())
    if key in _SPEC_CACHE:
        return dict(_SPEC_CACHE[key])
    s = _spec_call(question)
    if s is not None:
        if len(_SPEC_CACHE) > 500:
            _SPEC_CACHE.clear()
        _SPEC_CACHE[key] = dict(s)
    return s


def _spec_call(question):
    try:
        import ollama
        import retrieve as R
        r = ollama.chat(model=SPEC_MODEL, think=False, format=SPEC_SCHEMA,
                        options=R.llm_options({"num_predict": 200, "temperature": 0}) if SPEC_MODEL == R.QUERY_MODEL
                        else {"num_predict": 200, "temperature": 0},
                        keep_alive=os.environ.get("KEEP_ALIVE", "30m"),
                        messages=[{"role": "system", "content": SPEC_SYSTEM},
                                  {"role": "user", "content": f"Question: {question}"}])
        R._note_llm("spec", r)
        s = json.loads(r["message"]["content"])
    except Exception:
        return None
    s["genres"] = [g for g in s.get("genres") or [] if g in GENRES]
    s["limit"] = max(1, min(int(s.get("limit") or 10), 25))
    s["min_films"] = max(1, min(int(s.get("min_films") or 1), 10))
    return s


# 6 Oct (v3b L2 regressions a29, a36): llama3.2 padded specs with genres and years the question
# never mentioned (a36 "highest-grossing movies of 1997" got 16 genres; a29 "Ridley Scott's early
# and later films" got 6 genres and 1975-2012) and _film_filter ANDs genres, so both returned no
# rows. The spec's filters are now taken from the question's own words: genres only when the
# question names them, years only when it has a time expression, and a named person always
# means their filmography (a29 was a "trend" spec that ignored the person).
GENRE_WORDS = {
    "Action": r"\baction\b", "Adventure": r"\badventures?\b",
    "Animation": r"\banimat\w*|\bcartoons?\b|\bpixar\b",
    "Comedy": r"\bcomed\w*|\bfunny\b|\brom-?coms?\b", "Crime": r"\bcrime\b|\bgangsters?\b|\bheists?\b|\bmob\b",
    "Documentary": r"\bdocumentar\w*", "Drama": r"\bdramas?\b", "Family": r"\bfamily\b|\bkids'?\b|\bchildren'?s?\b",
    "Fantasy": r"\bfantasy\b", "History": r"\bhistor\w*|\bperiod (pieces?|films?|dramas?)\b",
    "Horror": r"\bhorror\b|\bscary\b|\bslashers?\b", "Music": r"\bmusicals?\b|\bmusic (films?|movies?)\b",
    "Mystery": r"\bmyster\w*|\bwhodunn?its?\b", "Romance": r"\bromanc\w*|\bromantic\b|\brom-?coms?\b|\blove stor",
    "Science Fiction": r"\bsci-?fi\b|\bscience[- ]fiction\b|\bscifi\b", "Thriller": r"\bthrillers?\b|\bsuspense\b",
    "War": r"\bwar (films?|movies?|dramas?)\b|\bwartime\b", "Western": r"\bwesterns?\b"}
R_TIME = re.compile(r"\b(1[89]\d\d|20\d\d)s?\b|'?\b\d0s\b|\b(nineties|eighties|seventies|sixties|fifties|forties|"
                    r"decades?|this year|last year|recent\w*|lately|nowadays|these days)\b", re.I)


def clean_spec(question, spec):
    """Filters from the question's words, not the model's guesses (see above)."""
    if not spec:
        return spec
    s = dict(spec)
    q = question or ""
    s["genres"] = [g for g, p in GENRE_WORDS.items() if re.search(p, q, re.I)]
    if not R_TIME.search(q):
        s["year_from"] = s["year_to"] = None
    # Only people the question really names: llama3.2 sometimes fills "people" for "which
    # science-fiction directors ..." (a30 a31 a33 lost their ranking to a filmography on 6 Oct).
    ql = q.casefold()
    s["people"] = [p for p in s.get("people") or []
                   if (p.get("name") or "").strip() and p["name"].casefold() in ql]
    if s["people"] and s.get("task") in ("trend", "ranking", "none"):
        s["task"] = "filmography"
    return s


def person_id(name, role="any"):
    c = db()
    jobs = {"director": ("Director",), "actor": ("Actor",), "writer": ("Screenplay", "Writer", "Story"),
            "composer": ("Original Music Composer", "Music")}.get(role)
    rows = c.execute("select person_id, name, popularity from people where name = ? collate nocase", (name,)).fetchall()
    if not rows:
        rows = c.execute("select person_id, name, popularity from people where name like ? collate nocase "
                         "order by popularity desc limit 5", (f"%{name}%",)).fetchall()
    best = None
    for pid, n, pop in rows:
        q = "select count(*) from credits where person_id = ?" + (
            f" and job in ({','.join('?' * len(jobs))})" if jobs else "")
        cnt = c.execute(q, (pid, *(jobs or ()))).fetchone()[0]
        if cnt and (best is None or cnt > best[2]):
            best = (pid, n, cnt)
    return best


def filmography_block(name, role):
    c = db()
    p = person_id(name, role)
    if not p:
        return None
    pid, pname, _ = p
    jobs = {"director": "('Director')", "actor": "('Actor')", "writer": "('Screenplay','Writer','Story')",
            "composer": "('Original Music Composer','Music')"}.get(role)
    rows = c.execute(f"""select distinct f.tmdb_id, f.title, f.year, k.job, f.genres_json, f.vote_average, f.vote_count,
            m.gross, m.budget, m.roi from credits k join films f on f.tmdb_id = k.tmdb_id
            left join money m on m.tmdb_id = f.tmdb_id
            where k.person_id = ? {'and k.job in ' + jobs if jobs else ''} and f.year is not null
            order by f.year""", (pid,)).fetchall()
    if not rows:
        return None
    lines = [f"{pname} - films in the database ({role}), oldest first: title | year | genres | TMDB rating | "
             f"budget | worldwide gross | gross/budget"]
    seen = set()
    for fid, t, y, job, g, va, vc, gross, bud, roi in rows:
        if fid in seen:
            continue
        seen.add(fid)
        lines.append(f"{t} | {y} | {', '.join(json.loads(g or '[]')[:2])} | "
                     f"{f'{va:.1f}' if va and (vc or 0) >= 50 else '?'} | {usd(bud) if bud else '?'} | "
                     f"{usd(gross) if gross else '?'} | {f'{roi:.1f}x' if roi else '?'}")
    ids = list(seen)
    kw = {}
    for (name_,) in c.execute(f"select name from keywords where tmdb_id in ({','.join('?' * len(ids))})", ids):
        kw[name_] = kw.get(name_, 0) + 1
    common = [k for k, n in sorted(kw.items(), key=lambda x: -x[1]) if n >= 2][:12]
    if common:
        lines.append("Keywords that recur across these films (TMDB tags, count): " +
                     ", ".join(f"{k} ({kw[k]})" for k in common))
    ratings = [r[5] for r in rows if r[5] and (r[6] or 0) >= 50]
    if len(ratings) >= 4:
        half = len(ratings) // 2
        lines.append(f"Mean TMDB rating, first {half} films: {statistics.mean(ratings[:half]):.2f}; "
                     f"later {len(ratings) - half}: {statistics.mean(ratings[half:]):.2f} [TMDB, computed]")
    lines.append("[TMDB credits; money: TMDB / Wikipedia infobox / Wikidata]")
    return "\n".join(lines)


def _film_filter(s):
    where, args = ["f.year is not null"], []
    if s.get("year_from"):
        where.append("f.year >= ?"); args.append(int(s["year_from"]))
    if s.get("year_to"):
        where.append("f.year <= ?"); args.append(int(s["year_to"]))
    for g in s.get("genres") or []:
        where.append("f.genres_json like ?"); args.append(f'%"{g}"%')
    if s.get("max_budget"):
        where.append("m.budget <= ?"); args.append(int(s["max_budget"]))
    return " and ".join(where), args


METRIC_SQL = {"gross": "m.gross", "roi": "m.roi", "budget": "m.budget",
              "rating": "case when f.vote_count >= 200 then f.vote_average end"}


def aggregate_block(question, spec):
    """Rankings and trends computed in SQL from the spec. The model never writes SQL."""
    c = db()
    if not spec or spec.get("task") == "none" or c is None:
        return None
    if spec["task"] == "filmography":
        blocks = [filmography_block(p["name"], p.get("role") or "any") for p in spec.get("people") or []][:2]
        return "\n\n".join(b for b in blocks if b) or None
    where, args = _film_filter(spec)
    metric = spec.get("metric") or "gross"
    order = "asc" if spec.get("order") == "asc" else "desc"
    lim = spec.get("limit") or 10
    note = (f"Profitable = worldwide gross >= {PROFIT_X}x production budget (rule of thumb; marketing and the "
            f"theaters' share are not in the data). ") if metric == "roi" else ""
    desc = (f"genres {', '.join(spec.get('genres') or []) or 'any'}, years {spec.get('year_from') or 'any'}-"
            f"{spec.get('year_to') or 'any'}" + (f", budget <= {usd(spec['max_budget'])}" if spec.get("max_budget") else ""))
    gb = spec.get("group_by") or "film"
    if spec["task"] == "trend" or gb == "year":
        data = {}
        for y, g, roi in c.execute(f"select f.year, m.gross, m.roi from films f left join money m on m.tmdb_id = f.tmdb_id "
                                   f"where {where}", args):
            data.setdefault(y, []).append((g, roi))
        all_years = {}
        for y, g, roi in c.execute("select f.year, m.gross, m.roi from films f join money m on m.tmdb_id = f.tmdb_id "
                                   "where f.year between ? and ? and m.gross > 0",
                                   (spec.get("year_from") or 1900, spec.get("year_to") or 2100)):
            all_years.setdefault(y, []).append(roi)
        lines = [f"By year ({desc}): year | films | with box office | median gross | median gross/budget | "
                 f"median gross/budget, all genres"]
        for y in sorted(data):
            vals = data[y]
            gs = [g for g, _ in vals if g]
            rs = [r for _, r in vals if r]
            ar = [r for r in all_years.get(y, []) if r]
            lines.append(f"{y} | {len(vals)} | {len(gs)} | {usd(_median(gs)) if gs else '?'} | "
                         f"{f'{_median(rs):.1f}x' if rs else '?'} | {f'{_median(ar):.1f}x' if ar else '?'}")
        top = c.execute(f"""select f.title, f.year, m.budget, m.gross, m.roi from films f join money m on m.tmdb_id = f.tmdb_id
            where {where} and m.gross > 0 order by {METRIC_SQL.get(metric, 'm.gross')} desc limit 8""", args).fetchall()
        if top:
            lines.append("Top films by " + metric + ": " + "; ".join(
                f"{t} ({y}, budget {usd(b) if b else '?'}, gross {usd(g)}{f', {r:.1f}x' if r else ''})" for t, y, b, g, r in top))
        return note + "\n".join(lines) + "\n[TMDB / Wikipedia infobox / Wikidata, computed by Movie Buff]"
    if gb in ("director", "actor"):
        job = "Director" if gb == "director" else "Actor"
        extra = " and k.ord < 4" if gb == "actor" else ""
        rows = c.execute(f"""select p.name, count(distinct f.tmdb_id), group_concat(f.title || ' (' || f.year || ')', '; '),
                avg(case when f.vote_count >= 200 then f.vote_average end),
                sum(case when m.roi >= {PROFIT_X} then 1 else 0 end), sum(case when m.roi is not null then 1 else 0 end),
                sum(m.gross), group_concat(m.roi)
            from credits k join people p on p.person_id = k.person_id join films f on f.tmdb_id = k.tmdb_id
            left join money m on m.tmdb_id = f.tmdb_id
            where k.job = ?{extra} and {where} group by p.person_id having count(distinct f.tmdb_id) >= ?""",
                         (job, *args, spec.get("min_films") or 1)).fetchall()
        def score(r):
            rois = [float(x) for x in (r[7] or "").split(",") if x]
            return {"roi": _median(rois) or -1, "rating": r[3] or -1, "gross": r[6] or -1,
                    "count": r[1], "budget": r[6] or -1}.get(metric, r[6] or -1)
        if metric == "roi":
            rows = [r for r in rows if r[5] >= (spec.get("min_films") or 1)]
        rows.sort(key=score, reverse=(order == "desc"))
        lines = [f"{gb.title()}s ranked by {metric} ({desc}; at least {spec.get('min_films') or 1} films): "
                 f"name | films | profitable / with data | median gross/budget | total gross | mean TMDB rating | films"]
        for r in rows[:lim]:
            rois = [float(x) for x in (r[7] or "").split(",") if x]
            lines.append(f"{r[0]} | {r[1]} | {r[4]}/{r[5]} | {f'{_median(rois):.1f}x' if rois else '?'} | "
                         f"{usd(r[6]) if r[6] else '?'} | {f'{r[3]:.1f}' if r[3] else '?'} | {(r[2] or '')[:160]}")
        if len(lines) == 1:
            return None
        return note + "\n".join(lines) + "\n[TMDB credits; money: TMDB / Wikipedia infobox / Wikidata; computed by Movie Buff]"
    if gb == "genre":
        rows = c.execute(f"""select f.genres_json, m.gross, m.roi, f.vote_average from films f
            left join money m on m.tmdb_id = f.tmdb_id where {where}""", args).fetchall()
        agg = {}
        for gj, g, r, va in rows:
            for gname in json.loads(gj or "[]")[:2]:
                agg.setdefault(gname, []).append((g, r, va))
        lines = [f"Genres ({desc}): genre | films | median gross | median gross/budget"]
        for gname, vals in sorted(agg.items(), key=lambda x: -len(x[1]))[:lim]:
            gs = [g for g, _, _ in vals if g]
            rs = [r for _, r, _ in vals if r]
            lines.append(f"{gname} | {len(vals)} | {usd(_median(gs)) if gs else '?'} | {f'{_median(rs):.1f}x' if rs else '?'}")
        return note + "\n".join(lines) + "\n[TMDB / Wikipedia, computed]"
    col = METRIC_SQL.get(metric, "m.gross")
    rows = c.execute(f"""select f.title, f.year, m.budget, m.gross, m.roi, f.vote_average, f.vote_count from films f
        left join money m on m.tmdb_id = f.tmdb_id where {where} and {col} is not null
        order by {col} {order} limit ?""", (*args, lim)).fetchall()
    if not rows:
        return None
    lines = [f"Films ranked by {metric} ({desc}): title | year | budget | worldwide gross | gross/budget | TMDB rating"]
    for t, y, b, g, r, va, vc in rows:
        lines.append(f"{t} | {y} | {usd(b) if b else '?'} | {usd(g) if g else '?'} | {f'{r:.1f}x' if r else '?'} | "
                     f"{f'{va:.1f}' if va and (vc or 0) >= 50 else '?'}")
    return note + "\n".join(lines) + "\n[TMDB / Wikipedia infobox / Wikidata, computed by Movie Buff]"


# ================================================================== current / upcoming
def current_block(question):
    c = db()
    if c is None or not has("lists"):
        return None
    import retrieve as R
    f = R.parse_filters(question)
    genres = f.get("genres") or []
    today = datetime.date.today()
    ql = question.lower()
    if re.search(r"\b(now playing|in (theaters|theatres|cinemas)|playing now|out now|currently)\b", ql):
        lists, lo, hi = ("now_playing",), None, None
    elif "next month" in ql:
        nm = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        lists, lo, hi = ("upcoming", "announced"), nm.isoformat(), \
            ((nm + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)).isoformat()
    elif re.search(r"\b(this (week|weekend))\b", ql):
        lists, lo, hi = ("now_playing", "upcoming"), (today - datetime.timedelta(days=3)).isoformat(), \
            (today + datetime.timedelta(days=7)).isoformat()
    elif re.search(r"\b(just came out|recent|new releases?|this year)\b", ql):
        lists, lo, hi = ("now_playing", "since_snapshot"), (today - datetime.timedelta(days=120)).isoformat(), today.isoformat()
    else:
        lists, lo, hi = ("upcoming", "announced"), today.isoformat(), (today + datetime.timedelta(days=180)).isoformat()
    sql = (f"select distinct f.tmdb_id, f.title, coalesce(f.us_release_date, f.release_date) d, f.genres_json, f.overview, "
           f"f.popularity, l.fetched_at from lists l join films f on f.tmdb_id = l.tmdb_id "
           f"where l.list_name in ({','.join('?' * len(lists))})")
    args = list(lists)
    if lo:
        sql += " and coalesce(f.us_release_date, f.release_date) between ? and ?"
        args += [lo, hi]
    for g in genres:
        sql += " and f.genres_json like ?"
        args.append(f'%"{g}"%')
    sql += " order by f.popularity desc limit 12"
    rows = c.execute(sql, args).fetchall()
    if not rows:
        return f"No films in the current/upcoming lists match ({', '.join(lists)}{', ' + ', '.join(genres) if genres else ''}). [TMDB]"
    fetched = max(r[6] for r in rows)[:10]
    out = [f"From TMDB's {' / '.join(x.replace('_', ' ') for x in lists)} lists (US), fetched {fetched}; "
           f"release dates can still move:"]
    for fid, t, d, g, ov, _pop, _f in sorted(rows, key=lambda r: r[2] or ""):
        cr, cast = crew(fid)
        dirs = ", ".join(cr.get("Director", [])[:1])
        out.append(f"{t} - {d or 'date not set'} - {', '.join(json.loads(g or '[]')[:3])}"
                   + (f" - dir. {dirs}" if dirs else "") + (f" - {', '.join(c_.split(' as ')[0] for c_ in cast[:2])}" if cast else "")
                   + (f": {' '.join((ov or '').split())[:160]}" if ov else ""))
    return "\n".join(out) + " [TMDB]"


# ================================================================== films only in facts.db
def facts_hits(titles, year=None):
    """Hits for films the catalogue (movies.db) doesn't have but facts.db does - mostly
    films released after the catalogue was built. Shaped like retrieve()'s hits.
    year: prefer that release year (+/- 1), e.g. Sinners (2025) over an older Sinners."""
    c = db()
    if c is None:
        return []
    import retrieve as R
    out = []
    for t in titles:
        order = (f"abs(coalesce(year, 0) - {int(year)}) <= 1 desc, " if year else "") + "vote_count desc"
        rows = c.execute("select tmdb_id, title, year, genres_json, overview, vote_count from films "
                         f"where title = ? collate nocase order by {order} limit 1", (t,)).fetchall()
        if not rows:
            nt = R.normalize(t)
            rows = [r for r in c.execute("select tmdb_id, title, year, genres_json, overview, vote_count from films "
                                         "where tier != 'catalog' order by vote_count desc")
                    if R.normalize(r[1]) == nt][:1]
        for fid, title, year, g, ov, vc in rows:
            cr, cast = crew(fid)
            out.append({"id": fid, "title": title, "release_year": str(year or ""),
                        "genres": ", ".join(json.loads(g or "[]")), "director": ", ".join(cr.get("Director", [])),
                        "top_cast": ", ".join(cast[:4]), "text": ov or "", "vote_count": vc or 0,
                        "distance": None, "_facts_only": True})
    return out


def film_card(h):
    """What the answer model sees for a film that is only in facts.db (chat.context_for's job
    for catalogue films). v7: TV shows (h["_tv"]) get tv.py's card."""
    if h.get("_tv"):
        import tv as _tv
        return _tv.context(h)
    b = facts_block(h["id"]) or f"{h.get('title')} ({h.get('release_year')})"
    r = film_row(h["id"])
    if r and r[12]:
        b += f"\nPlot (TMDB): {r[12]}"
    return b


# ================================================================== run
def run(question, hits, plan, q_text=None):
    """All tool blocks for this question: {"blocks": [...], "route": {...}, "replace_hits": bool,
    "ms": {...}}. Safe when facts.db is missing (returns no blocks)."""
    t0 = time.perf_counter()
    out = {"blocks": [], "route": None, "replace_hits": False, "ms": {}, "style": None, "citations": {}}
    if db() is None:
        return out
    rt = route(question, plan, hits)
    out["route"] = {k: v for k, v in rt.items() if k != "films"}
    out["route"]["films"] = [h.get("title") for h in rt["films"]]
    q_vec = None
    blocks = []
    films = [h for h in rt["films"] if not h.get("_tv") and film_row(h["id"])]
    tv_films = [h for h in rt["films"] if h.get("_tv")]
    if tv_films and ({"reviews", "reception"} & set(rt["tasks"]) or R_REVIEWS.search(question or "")):
        try:
            import tv as _tv
            for h in tv_films[:2]:
                b = _tv.reviews_block(h)
                if b:
                    blocks.append(("tv_reviews", b))
        except Exception as e:  # noqa: BLE001
            print(f"[tools] TV reviews failed: {e!r}", file=sys.stderr)
    if "sections" in rt["tasks"] and films:
        try:
            import retrieve as R
            q_vec = R.embed_query(q_text or question)
        except Exception:
            q_vec = None
    for h in films[:3]:
        b = facts_block(h["id"], short=len(films) > 1)
        if b:
            blocks.append(("facts", b))
    if "compare" in rt["tasks"] and len(films) > 1:
        b = compare_block([h["id"] for h in films])
        if b:
            blocks.append(("compare", b))
    if "reception" in rt["tasks"] and films:
        try:
            for kind, b in reception_blocks(question, films[:2], q_text, out, issue=rt.get("issue")):
                blocks.append((kind, b))
        except Exception as e:  # noqa: BLE001 - reception data missing must not lose the answer
            print(f"[tools] reception blocks failed: {e!r}", file=sys.stderr)
    if "market" in rt["tasks"] and films:
        b = market_block(films[0]["id"])
        if b:
            blocks.append(("market", b))
    if "reviews" in rt["tasks"]:
        for h in films[:2]:
            b = reviews_block(h["id"], h["title"])
            if b:
                blocks.append(("reviews", b))
    if "success" in rt["tasks"]:
        for h in films[:2]:
            b = success_block(h["id"], h["title"])
            if b:
                blocks.append(("success", b))
    if "perf" in rt["tasks"]:
        for h in films[:2]:
            try:
                import performance as _perf
                b = _perf.performance_block(h["id"], h["title"], db())
            except Exception as e:  # noqa: BLE001 - a missing model must not lose the answer
                print(f"[tools] performance block failed: {e!r}", file=sys.stderr)
                b = None
            if b:
                blocks.append(("perf", b))
    if "news" in rt["tasks"] and films:
        try:
            import news as _news
            b = _news.news_block(films[0]["id"], films[0]["title"], db())
        except Exception as e:  # noqa: BLE001 - news data missing must not lose the answer
            print(f"[tools] news block failed: {e!r}", file=sys.stderr)
            b = None
        if b:
            blocks.append(("news", b))
    if EXTRA_DATA and ("watch" in rt["tasks"] or "audience" in rt["tasks"] or "boxoffice" in rt["tasks"]):
        try:
            import extra_data as _x
            for h in films[:2]:
                for kind, fn in (("boxoffice", getattr(_x, "boxoffice_block", None)), ("watch", _x.watch_block),
                                 ("audience", _x.audience_block)):
                    if fn is None:
                        continue
                    if kind in rt["tasks"]:
                        b = fn(h["id"], h["title"])
                        if b:
                            blocks.append((kind, b))
        except Exception as e:  # noqa: BLE001 - extra data missing must not lose the answer
            print(f"[tools] extra data block failed: {e!r}", file=sys.stderr)
    if "sections" in rt["tasks"]:
        for h in films[:2]:
            b = sections_block(h["id"], h["title"], q_vec, rt["kinds"],
                               n=SECTION_N if len(films) == 1 else max(2, SECTION_N - 1), chars=SECTION_CHARS)
            if b:
                blocks.append(("sections", b))
    out["ms"]["sql"] = round((time.perf_counter() - t0) * 1000)
    if "aggregate" in rt["tasks"]:
        t1 = time.perf_counter()
        spec = clean_spec(question, spec_for(question))
        out["ms"]["spec"] = round((time.perf_counter() - t1) * 1000)
        out["route"]["spec"] = spec
        b = aggregate_block(question, spec)
        if b:
            blocks.insert(0, ("aggregate", b))
            if not films:
                out["replace_hits"] = True
    if "current" in rt["tasks"]:
        b = current_block(question)
        if b:
            blocks.insert(0, ("current", b))
            if not films:
                out["replace_hits"] = True
    if "reception_list" in rt["tasks"]:
        try:
            b = reception_list_block(question, rt.get("reception_list"))
        except Exception as e:  # noqa: BLE001
            print(f"[tools] reception list failed: {e!r}", file=sys.stderr)
            b = None
        if b:
            blocks.insert(0, ("reception_list", b))
            if not films:
                out["replace_hits"] = True
    hide = set(REQUEST.get("hide") or ())
    if hide:
        blocks = _hide_lines(blocks, hide, question)
    # Budget: the whole tool context stays under TOOL_CHARS; later (less central) blocks shrink first.
    total = 0
    kept = []
    for kind, b in blocks:
        room = TOOL_CHARS - total
        if room < 300:
            break
        if len(b) > room:
            b = b[:room].rsplit("\n", 1)[0] + "\n..."
        kept.append((kind, b))
        total += len(b)
    out["blocks"] = kept
    # Answer style (chat.py picks the "How to answer" text). 6 Oct: the one analysis template
    # for everything made the judge fail plain facts, rankings and "what did critics say"
    # answers for unrequested interpretation and length (a03 a05 a16 a34 a35 a43), so
    # rankings, reception and single facts get their own, shorter instructions.
    tasks = set(rt["tasks"])
    spec_task = ((out["route"] or {}).get("spec") or {}).get("task")
    if "reception_list" in tasks and not films:
        style = "reception_list"
    elif "reception" in tasks and films and rt.get("issue"):
        style = "issue"                         # 4 Nov (ISSUES=1)
    elif "reception" in tasks and films and not (rt["why"] and R_MONEY.search(question or "")):
        style = "reception"
    elif rt["why"]:
        style = "analysis"
    elif "aggregate" in tasks and spec_task == "ranking":
        style = "ranking"
    elif "aggregate" in tasks:
        style = "analysis"                      # careers and trends
    elif "reviews" in tasks and films:
        style = "reviews"
    elif "current" in tasks:
        style = "current"
    elif "news" in tasks:
        style = "news"
    elif films and tasks <= {"facts", "watch", "boxoffice"}:
        style = "fact"
    else:
        style = None
    out["style"] = style
    out["ms"]["total"] = round((time.perf_counter() - t0) * 1000)
    return out


# ================================================================== reception (v7 R15 / R16)
def reception_card_text(fid):
    """The film's reception card (reception_cards.py): the stored text, else built now."""
    c = db()
    try:
        r = c.execute("select text from reception_card where tmdb_id = ?", (fid,)).fetchone()
    except sqlite3.Error:
        r = None
    if r and r[0]:
        return r[0]
    try:
        import reception_cards as RC
        card = RC.build_card(c, fid)
        return card["text"] if card else None
    except Exception:  # noqa: BLE001
        return None


# 4 Nov: YT_COMMENTS=1 - the reception block also gets one line on the allow-listed channels' videos about the film
# (count, views, likes: YouTube API data) and, when scored, the sentiment of the viewer comments under them (Cinemack's
# own measure, labelled as such); the evidence then includes those comments. YT_COMMENTS=0 = the 3 Nov blocks.
YT_COMMENTS = os.environ.get("YT_COMMENTS", "0") == "1"


def _q(sql, p=()):
    import sqlite3 as _sq
    c = db()
    if c is None:
        return []
    try:
        return c.execute(sql, p).fetchall()
    except _sq.Error:
        return []


def youtube_line(fid):
    r = _q("""select count(distinct a.video_key), sum(a.views), sum(a.likes), sum(a.comment_count), max(a.fetched_at)
        from yt_analysis a join (select distinct video_key from yt_analysis_units where tmdb_id = ? and match_ok = 1) u
        on u.video_key = a.video_key""", (fid,))
    if not r or not r[0][0]:
        return None
    n, views, likes, ncom, ts = r[0]
    line = (f"YouTube: {n} video{'s' if n != 1 else ''} about this film from the allow-listed review and analysis channels"
            + (f", {views:,} views" if views else "") + (f", {likes:,} likes" if likes else "")
            + (f", {ncom:,} comments" if ncom else "") + f" in total [YouTube Data API{', ' + ts[:10] if ts else ''}]")
    s = _q("""select count(*), sum(label = 'positive'), sum(label = 'mixed'), sum(label = 'negative')
        from review_sentiment where source_type = 'yt_comment' and tmdb_id = ?""", (fid,))
    if s and s[0][0]:
        m, pos, mix, neg = s[0]
        line += (f"; the top comments under them, scored by Cinemack (not a YouTube metric): {pos or 0} positive, "
                 f"{mix or 0} mixed, {neg or 0} negative of {m}" + (" - too few to generalise" if m < 20 else ""))
    return line


def reception_blocks(question, films, q_text, out, issue=False):
    """[(kind, block)] for each film: its reception card, then numbered evidence. Sets out["citations"]
    ({n: {label, url, ...}}) so the site can list the sources the answer cited."""
    import evidence as EV
    blocks, cites = [], dict(out.get("citations") or {})
    q_vec = None
    try:
        import retrieve as R
        q_vec = R.embed_query(question if issue else (q_text or question))   # 4 Nov: an issue is searched as asked
    except Exception:  # noqa: BLE001 - keyword search alone still works
        q_vec = None
    k = RECEPTION_K if len(films) == 1 else max(3, RECEPTION_K // 2)
    if issue:
        k += 4
    for h in films:
        card = reception_card_text(h["id"])
        if card and YT_COMMENTS:
            try:
                yl = youtube_line(h["id"])
                if yl:
                    card = card + "\n" + yl
            except Exception as e:  # noqa: BLE001
                print(f"[tools] youtube line failed: {e!r}", file=sys.stderr)
        if card:
            blocks.append(("reception", card))
        ev = EV.search(question, [h["id"]], k=k, q_vec=q_vec, conn=db())
        b, c = EV.evidence_block(f"{h['title']} ({h.get('release_year') or '?'})", ev, start=len(cites) + 1)
        if b:
            blocks.append(("evidence", b))
            cites.update(c)
    out["citations"] = cites
    return blocks


def reception_list_block(question, kind):
    """E2 / E4 / R8: reception questions about many films, answered in SQL over the reception cards."""
    import reception_cards as RC
    c = db()
    spec = clean_spec(question, {"genres": [], "year_from": None, "year_to": None, "people": []})
    genre = (spec.get("genres") or [None])[0]
    m = re.search(r"\b(19|20)(\d)0s\b", question or "")
    yf, yt = (int(m.group(1) + m.group(2) + "0"), int(m.group(1) + m.group(2) + "9")) if m else (None, None)
    m2 = re.search(r"\b(?:in|from|of) ((?:19|20)\d\d)\b", question or "")
    if m2 and not m:
        yf = yt = int(m2.group(1))
    if kind == "gaps":
        return RC.gap_block(question, limit=10, genre=genre, year_from=yf, year_to=yt, conn=c)
    where, args = ["coalesce(f.vote_count, 0) >= 200"], []
    if genre:
        where.append("f.genres_json like ?")
        args.append(f'%"{genre}"%')
    if yf:
        where += ["f.year >= ?", "f.year <= ?"]
        args += [yf, yt]
    w = " and ".join(where)
    if kind == "divisive":
        rows = c.execute(f"""select f.title, f.year, d.n, d.mean, d.sd, d.pct_low, d.pct_high, d.bc from ml_dist d
            join films f on f.tmdb_id = d.tmdb_id where d.n >= 1000 and d.pct_low >= 0.15 and d.pct_high >= 0.15 and {w}
            order by d.bc desc limit 10""", args).fetchall()
        if not rows:
            return None
        return ("Most polarised films by MovieLens ratings (two camps: many very low AND many very high ratings; bimodality "
                "> 0.555 suggests two camps): title | year | ratings | mean /5 | % rating <= 2 | % rating >= 4.5 | bimodality\n"
                + "\n".join(f"{t} | {y} | {n:,} | {mn:.2f} | {lo:.0%} | {hi:.0%} | {bc:.2f}" for t, y, n, mn, sd, lo, hi, bc in rows)
                + "\n[MovieLens 32M ratings to 2023, computed by Movie Buff; only films with >= 1,000 ratings]")
    if kind == "reappraised":
        rows = c.execute(f"""select f.title, f.year, r.trend_label, r.trend_delta, r.legacy_label, r.data_json
            from reception_card r join films f on f.tmdb_id = r.tmdb_id
            where (r.legacy_label is not null or r.trend_label = 'rose') and {w}
            order by (r.legacy_label is not null) desc, coalesce(r.trend_delta, 0) desc limit 10""", args).fetchall()
        if not rows:
            return None
        out = ["Films whose standing rose after release: title | year | Wikipedia says | MovieLens release years -> "
               "5+ years later"]
        for t, y, tl, td, lg, _dj in rows:
            out.append(f"{t} | {y} | {lg or '-'} | {('+' if (td or 0) >= 0 else '') + format(td, '.2f') if td is not None else '-'}")
        return "\n".join(out) + "\n[Wikipedia: Legacy; MovieLens 32M, computed by Movie Buff]"
    if kind == "mixed_money":
        rows = c.execute(f"""select f.title, f.year, r.critic_score, r.critic_score_src, m.budget, m.gross, m.roi
            from reception_card r join films f on f.tmdb_id = r.tmdb_id join money m on m.tmdb_id = r.tmdb_id
            where r.critic_score between 35 and 60 and m.roi >= {PROFIT_X} and m.budget >= 1000000 and {w}
            order by m.gross desc limit 10""", args).fetchall()
        if not rows:
            return None
        return (f"Films with mixed reviews (critics' score 35-60 of 100) that were profitable (gross >= {PROFIT_X}x budget): "
                "title | year | critics | budget | worldwide gross | gross/budget\n"
                + "\n".join(f"{t} | {y} | {cs:.0f} ({src}) | {usd(b)} | {usd(g)} | {roi:.1f}x" for t, y, cs, src, b, g, roi in rows)
                + "\n[critics: Wikipedia / Wikidata / OMDb; money: TMDB / Wikipedia / Wikidata; computed by Movie Buff]")
    return None


def citation_footer(answer, citations):
    """v7 R16: 'Sources' lines for the evidence numbers the answer cited, in the order cited, or ''."""
    if not citations or not answer:
        return ""
    cites = {int(k): v for k, v in citations.items()}
    used = []
    for m in re.finditer(r"\[(\d{1,2}(?:\s*[,–-]\s*\d{1,2})*)\]", answer):
        for part in re.split(r"\s*,\s*", m.group(1)):
            if re.match(r"^\d+\s*[–-]\s*\d+$", part):
                a_, b_ = [int(x) for x in re.split(r"\s*[–-]\s*", part)]
                nums = range(a_, b_ + 1) if 0 < b_ - a_ < 10 else []
            else:
                nums = [int(part)] if part.strip().isdigit() else []
            for n in nums:
                if n in cites and n not in used:
                    used.append(n)
    if not used:
        return ""
    lines = []
    for n in used:
        c = cites[n]
        lab = c.get("label") or "source"
        lines.append(f"[{n}] [{lab}]({c['url']})" if c.get("url") else f"[{n}] {lab}")
    return "\n\n*Sources: " + "; ".join(lines) + ". Wikipedia text is CC BY-SA; reviews link to their original pages.*"


def _hide_lines(blocks, hide, question):
    """21 Oct: the person turned money and/or critic lines off. Whole blocks that are only money (market,
    perf, success) or only reviews go; single lines are dropped from the rest. Nothing is hidden when the
    question asks about that very thing."""
    q = question or ""
    hide = set(hide)
    if "money" in hide and (R_MONEY.search(q) or R_WHY.search(q)):
        hide.discard("money")
    if "critics" in hide and (R_REVIEWS.search(q) or R_WHY.search(q)):
        hide.discard("critics")
    if not hide:
        return blocks
    out = []
    for kind, b in blocks:
        if "money" in hide and kind in ("market", "perf", "success", "boxoffice"):
            continue
        if "critics" in hide and kind == "reviews":
            continue
        lines = b.split("\n")
        keep = [lines[0]] + [l for l in lines[1:]
                             if not ("money" in hide and R_MONEY_LINE.search(l))
                             and not ("critics" in hide and R_CRITIC_LINE.search(l.strip()))]
        if len(keep) > 1:
            out.append((kind, "\n".join(keep)))
    return out


# ================================================================== build
def build(facts_path=None, movielens_zip="data/movielens/ml-latest-small.zip"):
    """money: one best figure per film (and the others when sources disagree by > 25%).
    Preference: films released before mid-2022 -> Wikipedia infobox, Wikidata, TMDB (the
    infobox is usually the cited figure); newer -> TMDB first (the 2023 snapshot predates
    their final gross). Only US-dollar amounts are used for numbers."""
    conn = F.connect(facts_path)
    conn.executescript("""drop table if exists money;
        create table money(tmdb_id integer primary key, budget real, budget_src text, budget_alt text,
          gross real, gross_src text, gross_alt text, roi real, conflict integer);""")
    films = {fid: rd for fid, rd in conn.execute("select tmdb_id, release_date from films")}
    cand = {}
    for fid, field, v, cur, src, vt in conn.execute("select tmdb_id, field, value_num, currency, source, value_text "
                                                    "from facts where field in ('budget','revenue','box_office')"):
        if cur != "USD" or not v or v < 1000:
            continue
        # 16 Oct: an empty infobox field holding only the editors' hint <!-- ... e.g. "$11.6 million" ... -->
        # is not a figure (12 US-dollar rows; the same hint in pounds is skipped by money_extra.py convert).
        if vt and "<!--" in vt and not re.search(r"\d", re.sub(r"<!--.*?(?:-->|$)", "", vt, flags=re.S)):
            continue
        key = "budget" if field == "budget" else "gross"
        cand.setdefault(fid, {}).setdefault(key, []).append((v, src))
    rows = []
    for fid, d in cand.items():
        new = (films.get(fid) or "") >= "2022-07-01"
        pref = ["tmdb", "wikipedia_infobox", "wikidata"] if new else ["wikipedia_infobox", "wikidata", "tmdb"]
        rec = {"budget": (None, None, "[]"), "gross": (None, None, "[]")}
        conflict = 0
        for i, key in enumerate(("budget", "gross")):
            vals = d.get(key) or []
            if not vals:
                continue
            vals = sorted(set(vals), key=lambda x: pref.index(x[1]) if x[1] in pref else 9)
            best = vals[0]
            nums = [v for v, _ in vals]
            if len(nums) > 1 and max(nums) / max(min(nums), 1) > 1.25:
                conflict |= (1 << i)
            rec[key] = (best[0], best[1], json.dumps(vals))
        b, g = rec["budget"][0], rec["gross"][0]
        roi = (g / b) if (b and g and b >= 1e5) else None
        rows.append((fid, *rec["budget"], *rec["gross"], roi, conflict))
    conn.executemany("insert into money values (?,?,?,?,?,?,?,?,?)", rows)
    print(f"money: {len(rows):,} films; with budget and gross: {sum(1 for r in rows if r[7]):,}; "
          f"sources disagree: {sum(1 for r in rows if r[8]):,}")
    # MovieLens audience ratings (the small set is already downloaded by build_movielens.py).
    if os.path.exists(movielens_zip):
        import csv
        import io
        import zipfile
        z = zipfile.ZipFile(movielens_zip)
        def read(name):
            member = next(n for n in z.namelist() if n.endswith("/" + name))
            return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"))
        to_tmdb = {r["movieId"]: int(r["tmdbId"]) for r in read("links.csv") if r.get("tmdbId", "").isdigit()}
        agg = {}
        for r in read("ratings.csv"):
            t = to_tmdb.get(r["movieId"])
            if t:
                s = agg.setdefault(t, [0, 0.0])
                s[0] += 1
                s[1] += float(r["rating"])
        conn.execute("delete from ml_stats")
        conn.executemany("insert into ml_stats values (?,?,?,?)",
                         [(t, n, tot / n, os.path.basename(movielens_zip)) for t, (n, tot) in agg.items()])
        print(f"MovieLens: {len(agg):,} films rated")
    else:
        print(f"(no {movielens_zip}: MovieLens stats skipped)")
    F.set_meta(conn, "money_built_at", F.now())
    conn.commit()


def _try(question):
    import retrieve as R
    hits = R.retrieve(question, k=3)
    plan = hits[0].get("_plan") if hits else None
    print("plan:", json.dumps(plan)[:400])
    print("hits:", [h["title"] for h in hits])
    res = run(question, hits, plan)
    print("route:", json.dumps(res["route"], default=str)[:600])
    print("ms:", res["ms"], "style:", res["style"], "replace_hits:", res["replace_hits"])
    for kind, b in res["blocks"]:
        print(f"\n--- {kind} ({len(b)} chars)\n{b}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
    elif len(sys.argv) > 2 and sys.argv[1] == "try":
        _try(" ".join(sys.argv[2:]))
    else:
        print(__doc__)
