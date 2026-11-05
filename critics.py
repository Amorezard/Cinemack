#!/usr/bin/env python3
"""
R2 (v7, 27 Oct): critic statements from Wikipedia's reception sections, each with the outlet it cites.

v7_data.py reception stored the wikitext (with its <ref> citations) of every film's Reception / Critical
response / Box office / Legacy sections. This turns each section into sentences and, for every sentence,
works out which critic and which outlet it reports - from the sentence's own words ("Peter Debruge of
Variety ...") and from the citation it carries ({{cite web |work=Variety |author=Peter Debruge}}). It is the
legal route to Variety, The Hollywood Reporter and RogerEbert.com (no API; Wikipedia quotes them with
citations, CC BY-SA).

    python critics.py run                 # every fetched page not yet extracted (or whose revision changed)
    python critics.py run --again         # everything again (new VERSION does this by itself)
    python critics.py show "Oppenheimer"  # one film's statements, outlet, critic, kind
    python critics.py sample 50           # results/critic_outlet_check.csv: 50 films x 3 critic statements to
                                          # check by hand (fill the 'right' column y / n)
    python critics.py score results/critic_outlet_check.csv     # share right (rule: >= 90%)
    python critics.py stats               # counts by kind, top outlets, outlet source (text / ref / domain)

Table (facts.db, raw source text is never edited; rows are rebuilt per page when its revision changes):
    critic_statements(stmt_id, tmdb_id, pageid, revid, section_kind, heading, para, pos, text, quote, critic,
        outlet, outlet_src, critic_src, ref_url, ref_title, ref_date, ref_outlet, kind, text_hash, version)
        kind: critic (a named critic or outlet's view), summary (critics in general), aggregator (Rotten
              Tomatoes, Metacritic, CinemaScore, PostTrak), box_office, audience, other
        outlet_src: text (named in the sentence), ref (the citation's work / website / newspaper / publisher),
              domain (from the cited URL), "" (none)
    critic_statements_done(pageid, revid, version, n, done_at)

Every row keeps the page's revision id, so a statement can be cited as "Wikipedia, revision N".
"""
import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import zlib

import facts_db as F

VERSION = "cs1"
HERE = os.path.dirname(os.path.abspath(__file__))

DDL = """
create table if not exists critic_statements(stmt_id integer primary key, tmdb_id integer, pageid integer,
  revid integer, section_kind text, heading text, para integer, pos integer, text text, quote text, critic text,
  outlet text, outlet_src text, critic_src text, ref_url text, ref_title text, ref_date text, ref_outlet text,
  kind text, text_hash text, version text);
create index if not exists critic_statements_film on critic_statements(tmdb_id, kind);
create index if not exists critic_statements_outlet on critic_statements(outlet);
create index if not exists critic_statements_page on critic_statements(pageid);
create table if not exists critic_statements_done(pageid integer primary key, revid integer, version text, n integer,
  done_at text);
"""

# ------------------------------------------------------------------ outlets
# canonical name -> names it goes by in prose and in citations (lower case, no leading "the")
OUTLETS = {
    "Variety": ["variety", "variety magazine", "daily variety"],
    "The Hollywood Reporter": ["hollywood reporter", "thr"],
    "RogerEbert.com": ["rogerebert.com", "roger ebert.com", "rogerebert"],
    "Chicago Sun-Times": ["chicago sun-times", "chicago sun times", "sun-times"],
    "The New York Times": ["new york times", "nytimes", "nyt", "new york times company"],
    "The Guardian": ["guardian", "theguardian", "guardian.co.uk", "theguardian.com", "the observer", "observer"],
    "Los Angeles Times": ["los angeles times", "la times", "l.a. times"],
    "The Washington Post": ["washington post"],
    "The Wall Street Journal": ["wall street journal", "wsj"],
    "USA Today": ["usa today"],
    "Chicago Tribune": ["chicago tribune"],
    "The Boston Globe": ["boston globe"],
    "San Francisco Chronicle": ["san francisco chronicle", "sfgate"],
    "Rolling Stone": ["rolling stone"],
    "Time": ["time", "time magazine"],
    "Newsweek": ["newsweek"],
    "The New Yorker": ["new yorker"],
    "Entertainment Weekly": ["entertainment weekly", "ew", "ew.com"],
    "IndieWire": ["indiewire", "indie wire"],
    "Empire": ["empire", "empire magazine", "empireonline", "empire online"],
    "Total Film": ["total film"],
    "Sight and Sound": ["sight and sound", "sight & sound"],
    "Slant Magazine": ["slant magazine", "slant"],
    "The A.V. Club": ["a.v. club", "av club", "the av club", "avclub"],
    "Vanity Fair": ["vanity fair"],
    "The Atlantic": ["atlantic", "the atlantic"],
    "Slate": ["slate"],
    "Salon": ["salon", "salon.com"],
    "Vox": ["vox"],
    "Vulture": ["vulture", "new york magazine", "new york (magazine)", "new york"],
    "The Village Voice": ["village voice"],
    "The Independent": ["independent", "the independent"],
    "The Daily Telegraph": ["daily telegraph", "telegraph", "the telegraph"],
    "The Times": ["the times", "times (london)", "the times (london)", "sunday times", "the sunday times"],
    "Financial Times": ["financial times", "ft"],
    "Evening Standard": ["evening standard", "london evening standard"],
    "The Daily Beast": ["daily beast"],
    "TheWrap": ["thewrap", "the wrap"],
    "Deadline Hollywood": ["deadline hollywood", "deadline", "deadline.com"],
    "Screen International": ["screen international", "screen daily", "screendaily"],
    "Collider": ["collider"],
    "IGN": ["ign"],
    "Polygon": ["polygon"],
    "Den of Geek": ["den of geek"],
    "Film Threat": ["film threat"],
    "Reverse Shot": ["reverse shot"],
    "Little White Lies": ["little white lies"],
    "The Film Stage": ["film stage", "the film stage"],
    "Paste": ["paste", "paste magazine"],
    "Consequence": ["consequence", "consequence of sound"],
    "The Ringer": ["ringer", "the ringer"],
    "BBC": ["bbc", "bbc news", "bbc online", "bbc culture"],
    "NPR": ["npr", "national public radio"],
    "CNN": ["cnn"],
    "The Globe and Mail": ["globe and mail"],
    "Toronto Star": ["toronto star"],
    "The Philadelphia Inquirer": ["philadelphia inquirer"],
    "The Austin Chronicle": ["austin chronicle"],
    "Chicago Reader": ["chicago reader"],
    "The Observer": [],
    "New York Post": ["new york post", "ny post"],
    "New York Daily News": ["new york daily news", "daily news"],
    "Detroit News": ["detroit news"],
    "Miami Herald": ["miami herald"],
    "The Seattle Times": ["seattle times"],
    "The Arizona Republic": ["arizona republic"],
    "Star Tribune": ["star tribune"],
    "St. Louis Post-Dispatch": ["st. louis post-dispatch", "post-dispatch"],
    "Associated Press": ["associated press", "ap"],
    "Reuters": ["reuters"],
    "Forbes": ["forbes"],
    "Wired": ["wired"],
    "Esquire": ["esquire"],
    "GQ": ["gq"],
    "Mashable": ["mashable"],
    "Bloody Disgusting": ["bloody disgusting"],
    "Dread Central": ["dread central"],
    "Common Sense Media": ["common sense media"],
    "Kermode and Mayo's Film Review": ["kermode and mayo", "kermode & mayo"],
    "At the Movies": ["at the movies", "siskel & ebert", "siskel and ebert", "ebert & roeper", "ebert and roeper"],
    "Le Monde": ["le monde"],
    "Cahiers du Cinéma": ["cahiers du cinéma", "cahiers du cinema"],
    "The Sydney Morning Herald": ["sydney morning herald"],
    "The Hindu": ["the hindu", "hindu"],
    "The Times of India": ["times of india"],
    "Hindustan Times": ["hindustan times"],
    "Film Comment": ["film comment"],
    "Box Office Mojo": ["box office mojo", "boxofficemojo"],
    "The Numbers": ["the numbers", "the-numbers", "nash information services"],
    "Rotten Tomatoes": ["rotten tomatoes", "rottentomatoes"],
    "Metacritic": ["metacritic"],
    "CinemaScore": ["cinemascore"],
    "PostTrak": ["posttrak"],
    "TV Guide": ["tv guide"],
    "Radio Times": ["radio times"],
    "Time Out": ["time out", "time out london", "time out new york"],
    "Movieline": ["movieline"],
    "Spin": ["spin"],
    "Pajiba": ["pajiba"],
    "MovieWeb": ["movieweb"],
    "Screen Rant": ["screen rant", "screenrant"],
    "CinemaBlend": ["cinemablend"],
    "The Playlist": ["the playlist", "playlist"],
    "Nerdist": ["nerdist"],
    "io9": ["io9", "gizmodo"],
}
AGGREGATORS = {"Rotten Tomatoes", "Metacritic", "CinemaScore", "PostTrak", "Box Office Mojo", "The Numbers"}
DOMAINS = {
    "variety.com": "Variety", "hollywoodreporter.com": "The Hollywood Reporter", "rogerebert.com": "RogerEbert.com",
    "suntimes.com": "Chicago Sun-Times", "nytimes.com": "The New York Times", "theguardian.com": "The Guardian",
    "guardian.co.uk": "The Guardian", "latimes.com": "Los Angeles Times", "washingtonpost.com": "The Washington Post",
    "wsj.com": "The Wall Street Journal", "usatoday.com": "USA Today", "chicagotribune.com": "Chicago Tribune",
    "bostonglobe.com": "The Boston Globe", "boston.com": "The Boston Globe", "sfgate.com": "San Francisco Chronicle",
    "sfchronicle.com": "San Francisco Chronicle", "rollingstone.com": "Rolling Stone", "time.com": "Time",
    "newsweek.com": "Newsweek", "newyorker.com": "The New Yorker", "ew.com": "Entertainment Weekly",
    "indiewire.com": "IndieWire", "empireonline.com": "Empire", "gamesradar.com": "Total Film",
    "totalfilm.com": "Total Film", "bfi.org.uk": "Sight and Sound", "slantmagazine.com": "Slant Magazine",
    "avclub.com": "The A.V. Club", "vanityfair.com": "Vanity Fair", "theatlantic.com": "The Atlantic",
    "slate.com": "Slate", "salon.com": "Salon", "vox.com": "Vox", "vulture.com": "Vulture", "nymag.com": "Vulture",
    "villagevoice.com": "The Village Voice", "independent.co.uk": "The Independent", "telegraph.co.uk":
    "The Daily Telegraph", "thetimes.co.uk": "The Times", "ft.com": "Financial Times", "standard.co.uk":
    "Evening Standard", "thedailybeast.com": "The Daily Beast", "thewrap.com": "TheWrap", "deadline.com":
    "Deadline Hollywood", "screendaily.com": "Screen International", "collider.com": "Collider", "ign.com": "IGN",
    "polygon.com": "Polygon", "denofgeek.com": "Den of Geek", "filmthreat.com": "Film Threat", "lwlies.com":
    "Little White Lies", "thefilmstage.com": "The Film Stage", "pastemagazine.com": "Paste",
    "consequence.net": "Consequence", "theringer.com": "The Ringer", "bbc.co.uk": "BBC", "bbc.com": "BBC",
    "npr.org": "NPR", "cnn.com": "CNN", "theglobeandmail.com": "The Globe and Mail", "thestar.com": "Toronto Star",
    "inquirer.com": "The Philadelphia Inquirer", "philly.com": "The Philadelphia Inquirer",
    "austinchronicle.com": "The Austin Chronicle", "chicagoreader.com": "Chicago Reader", "nypost.com":
    "New York Post", "nydailynews.com": "New York Daily News", "detroitnews.com": "Detroit News",
    "seattletimes.com": "The Seattle Times", "azcentral.com": "The Arizona Republic", "startribune.com":
    "Star Tribune", "stltoday.com": "St. Louis Post-Dispatch", "apnews.com": "Associated Press", "reuters.com":
    "Reuters", "forbes.com": "Forbes", "wired.com": "Wired", "esquire.com": "Esquire", "gq.com": "GQ",
    "mashable.com": "Mashable", "bloody-disgusting.com": "Bloody Disgusting", "dreadcentral.com": "Dread Central",
    "commonsensemedia.org": "Common Sense Media", "boxofficemojo.com": "Box Office Mojo", "the-numbers.com":
    "The Numbers", "rottentomatoes.com": "Rotten Tomatoes", "metacritic.com": "Metacritic", "cinemascore.com":
    "CinemaScore", "tvguide.com": "TV Guide", "radiotimes.com": "Radio Times", "timeout.com": "Time Out",
    "movieline.com": "Movieline", "spin.com": "Spin", "filmcomment.com": "Film Comment", "smh.com.au":
    "The Sydney Morning Herald", "thehindu.com": "The Hindu", "timesofindia.indiatimes.com": "The Times of India",
    "hindustantimes.com": "Hindustan Times", "lemonde.fr": "Le Monde", "screenrant.com": "Screen Rant",
    "cinemablend.com": "CinemaBlend", "theplaylist.net": "The Playlist", "nerdist.com": "Nerdist", "io9.com": "io9",
    "gizmodo.com": "io9", "movieweb.com": "MovieWeb", "pajiba.com": "Pajiba", "reelviews.net": "ReelViews",
    "ebertandroeper.com": "At the Movies", "web.archive.org": None,
}
_ALIAS = {}
for _c, _names in OUTLETS.items():
    for _n in [_c.lower()] + _names:
        _ALIAS[re.sub(r"^the\s+", "", _n.strip().lower())] = _c


def canon_outlet(name):
    """'[[Variety (magazine)|Variety]]' / 'The Hollywood Reporter' / 'variety.com' -> canonical name, or the
    cleaned name itself when it is not a known outlet (so new outlets still count), or None."""
    if not name:
        return None
    n = strip_markup(str(name)).strip(" .,'\"")
    n = re.sub(r"\s*\((magazine|newspaper|website|film magazine|publication|uk|us)\)\s*$", "", n, flags=re.I)
    if not n:
        return None
    key = re.sub(r"^the\s+", "", n.lower())
    if key in _ALIAS:
        return _ALIAS[key]
    host = key.replace("www.", "")
    if host in DOMAINS:
        return DOMAINS[host] or None
    return n if len(n) <= 80 else None


def outlet_of_url(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host == "web.archive.org":           # archived copy: the original URL is inside the path
        m = re.search(r"/web/\d+\w*/(https?://.+)$", url)
        return outlet_of_url(m.group(1)) if m else None
    parts = host.split(".")
    for i in range(len(parts) - 1):
        d = ".".join(parts[i:])
        if d in DOMAINS:
            return DOMAINS[d]
    return None


# ------------------------------------------------------------------ wikitext
def _innermost_templates(s):
    """Yield (start, end) of innermost {{...}} spans (no nested braces inside)."""
    return [(m.start(), m.end()) for m in re.finditer(r"\{\{(?:(?!\{\{|\}\}).)*\}\}", s, re.S)]


def _tparams(body):
    """'name|a|b=c' -> (name, [positional], {named})"""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    name = parts[0].strip().lower().replace("_", " ")
    pos, named = [], {}
    for p in parts[1:]:
        m = re.match(r"\s*([\w -]+?)\s*=(.*)$", p, re.S)
        if m:
            named[m.group(1).strip().lower()] = m.group(2).strip()
        else:
            pos.append(p.strip())
    return name, pos, named


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _template_text(body):
    """What a template shows as prose ('' for most). Handles the ones that carry text in reception sections."""
    name, pos, named = _tparams(body)
    if name in ("'", "' "):
        return "'"
    if name in ("nbsp", "snd", "spaced ndash", "spaced en dash"):
        return " – " if name != "nbsp" else " "
    if name in ("ndash", "en dash", "–"):
        return "–"
    if name in ("mdash", "em dash", "—"):
        return "—"
    if name in ("nowrap", "small", "smaller", "big", "noitalic", "nobold", "abbr", "lang", "transl", "keep together",
                "ill", "interlanguage link", "illm", "nobr", "visible anchor", "anchor+", "em", "strong", "sic",
                "text", "linktext", "proper name", "titlecase"):
        if name in ("lang", "transl") and len(pos) >= 2:
            return pos[1]
        return pos[0] if pos else ""
    if name in ("usd", "us$", "us dollar"):
        return "$" + (pos[0] if pos else "")
    if name in ("as of",):
        return "As of " + " ".join(p for p in pos if p)
    if name in ("rotten tomatoes prose", "rt prose", "rotten tomatoes data prose"):
        pct, avg, n = (pos + [None, None, None])[:3]
        s = f"On the review aggregator website Rotten Tomatoes, {pct}% of"
        s += f" {n} critics' reviews are positive" if n else " critics' reviews are positive"
        if avg:
            s += f", with an average rating of {avg}/10"
        return s + "."
    if name in ("metacritic film prose", "metacritic prose", "mc prose"):
        sc, n = (pos + [None, None])[:2]
        s = f"Metacritic, which uses a weighted average, assigned the film a score of {sc} out of 100"
        if n:
            s += f", based on {n} critics"
        return s + "."
    if name in ("cinemascore", "cinemascore prose"):
        return f"Audiences polled by CinemaScore gave the film an average grade of \"{pos[0] if pos else '?'}\"."
    if name in ("quote", "blockquote", "quotation", "cquote", "quote box", "rquote", "pull quote", "quote frame"):
        text = named.get("text") or named.get("quote") or (pos[0] if pos else "")
        who = named.get("author") or named.get("sign") or named.get("source") or (pos[1] if len(pos) > 1 else "")
        if not text:
            return ""
        return f"\n\n“{text}”" + (f" — {who}" if who else "") + "\n\n"
    if name.startswith("cite ") or name in ("citation", "harvnb", "sfn", "efn", "refn", "notelist", "reflist",
                                            "citation needed", "cn", "fact", "who", "when", "clarify",
                                            "better source needed", "failed verification", "dead link",
                                            "main", "see also", "further", "anchor", "clear", "multiple image",
                                            "listen", "col-begin", "col-end", "div col", "div col end", "reflist-talk"):
        return ""
    if name in ("film ratings", "film reviews", "rating", "album ratings", "video game reviews", "aggregate scores"):
        return ""
    return ""


def strip_markup(s):
    """Wikitext -> plain text. Ref markers (\\x00N\\x00) pass through untouched."""
    s = re.sub(r"<!--.*?(-->|$)", "", s or "", flags=re.S)
    for _ in range(12):                                   # nested templates: innermost first
        spans = _innermost_templates(s)
        if not spans:
            break
        out, last = [], 0
        for a, b in spans:
            out.append(s[last:a])
            out.append(_template_text(s[a + 2:b - 2]))
            last = b
        out.append(s[last:])
        s = "".join(out)
    s = re.sub(r"\{\|.*?\|\}", "", s, flags=re.S)       # tables
    s = re.sub(r"\[\[(?:File|Image|Category|Media):(?:[^\[\]]|\[\[[^\]]*\]\])*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\[\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://[^\s\]]+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://[^\s\]]+\]", "", s)
    s = re.sub(r"(?<!')''([^'\n]+?)'''(?=s\b)", r"\1'", s)     # ''RogerEbert.com'''s -> RogerEbert.com's
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"</?(small|big|sup|sub|span|div|blockquote|poem|center|i|b|u|s|abbr|nowiki|font|em|strong)[^>]*>",
               "", s, flags=re.I)
    s = re.sub(r"<gallery.*?</gallery>", "", s, flags=re.S | re.I)
    s = re.sub(r"__\w+__", "", s)
    s = html.unescape(s).replace(" ", " ")
    return s


_REF = re.compile(r"<ref(?:\s[^>]*?)?/>|<ref(?:\s[^>]*)?>.*?</ref\s*>", re.S | re.I)
_REF_NAME = re.compile(r"name\s*=\s*\"?([^\"/>]+?)\"?\s*(?:/?>|\s)", re.I)


def ref_info(ref_wikitext):
    """One <ref> -> {url, title, outlet, author, date, kind}. Handles {{cite ...}}, {{citation}} and bare refs."""
    body = re.sub(r"^<ref[^>]*>|</ref\s*>$", "", ref_wikitext.strip(), flags=re.I | re.S)
    d = {"url": None, "title": None, "outlet": None, "author": None, "date": None}
    m = re.search(r"\{\{\s*(cite[^|}]*|citation|cite)\s*\|(.*)\}\}", body, re.S | re.I)
    if m:
        _n, pos, named = _tparams("x|" + m.group(2))
        d["url"] = named.get("url") or named.get("archive-url")
        if named.get("archive-url") and named.get("url"):
            d["url"] = named["url"]
        d["title"] = strip_markup(named.get("title") or named.get("script-title") or "").strip() or None
        for k in ("work", "website", "newspaper", "magazine", "journal", "periodical", "publisher", "agency",
                  "via", "network", "station"):
            if named.get(k):
                d["outlet"] = canon_outlet(named[k])
                if d["outlet"]:
                    break
        author = named.get("author") or named.get("author1") or named.get("authors")
        if not author:
            first = named.get("first") or named.get("first1") or ""
            last = named.get("last") or named.get("last1") or ""
            author = f"{first} {last}".strip()
        author = strip_markup(author or "").strip()
        if author and not re.search(r"(staff|editor|reporter|writers?|team|news|times|press|agency|admin)\b", author, re.I):
            d["author"] = author
        d["date"] = (named.get("date") or named.get("publication-date") or "").strip() or None
    else:
        u = re.search(r"https?://[^\s\]|<>]+", body)
        if u:
            d["url"] = u.group(0)
        t = re.search(r"\[https?://\S+\s+([^\]]+)\]", body)
        if t:
            d["title"] = strip_markup(t.group(1)).strip()
        tail = strip_markup(re.sub(r"\[https?://[^\]]*\]", "", body)).strip(" .,")
        if tail:
            parts = [p.strip() for p in re.split(r"[.,;]", tail) if p.strip()]
            for p in parts:
                o = canon_outlet(p)
                if o and o in OUTLETS:
                    d["outlet"] = o
                    break
    if not d["outlet"] and d["url"]:
        d["outlet"] = outlet_of_url(d["url"])
    return d


_ABBR = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|No|Vol|Inc|Ltd|Co|Corp|U\.S|U\.K|D\.C|Ph\.D|e\.g|i\.e|approx|"
                   r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mt|Ft|Gen|Lt|Col|Sgt|Capt|Rev|Prof)\.|"
                   r"\b[A-Z]\.)$")
_BOUND = re.compile(r"(?<=[.!?])[\"”’')\]]*\s+(?=[\"“‘(\[]?[A-Z0-9])")


def _split_sentences(text):
    out, last = [], 0
    for m in _BOUND.finditer(text):
        head = text[last:m.start()]
        if _ABBR.search(head.rstrip("\"”’')]")):
            continue
        out.append(text[last:m.end()])
        last = m.end()
    out.append(text[last:])
    return out


_MARK = re.compile(r"\x00(\d+)\x00")


def sentences_with_refs(par):
    """'Text.\\x000\\x00 More text\\x001\\x00.' -> [(sentence, [ref indices])]. A marker belongs to the
    sentence it follows (refs come after the full stop on Wikipedia, or mid-sentence after a claim)."""
    sents = []                                        # [text, refs]
    pieces = _MARK.split(par)
    for i, piece in enumerate(pieces):
        if i % 2 == 1:                                # a marker
            if sents:
                sents[-1][1].append(int(piece))
            else:
                sents.append(["", [int(piece)]])
            continue
        if not piece:
            continue
        segs = _split_sentences(piece)
        for j, seg in enumerate(segs):
            if j == 0 and sents:
                prev = sents[-1][0]
                ended = bool(re.search(r"[.!?][\"”’')\]]*\s*$", prev))
                starts_new = bool(re.match(r"\s+[\"“‘(\[]?[A-Z0-9]", seg))
                if not (ended and starts_new and not _ABBR.search(prev.rstrip().rstrip("\"”’')]"))):
                    sents[-1][0] += seg
                    continue
            sents.append([seg, []])
    out = []
    for t, refs in sents:
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) >= 20 or (t and refs):
            out.append((t, refs))
    return out


# ------------------------------------------------------------------ classification
R_AGG = re.compile(r"\b(rotten tomatoes|metacritic|cinemascore|posttrak|review aggregat\w*|weighted average|"
                   r"approval rating|tomatometer|aggregator|(website|site)'s (critical |critics )?consensus|"
                   r"critics consensus)\b", re.I)
R_BOX = re.compile(r"(\$\s?\d|\bgross(ed|ing)?\b|\bbox office\b|\bopening weekend\b|\bticket sales\b|\bbudget\b|"
                   r"\bdebuted (at|to)\b|\bmillion\b.{0,40}\b(weekend|worldwide|domestic)\b)", re.I)
R_AUDIENCE = re.compile(r"\b(audiences?|viewers|fans|moviegoers|opening[- ]night)\b.{0,60}\b(polled|surveyed|gave|"
                        r"rated|grade|score)\b", re.I)
R_VERB = re.compile(r"\b(wrote|writes|writing|called|calls|said|says|praised|praises|criticized|criticised|"
                    r"described|describes|gave|gives|awarded|noted|notes|commented|opined|felt|found|concluded|"
                    r"lauded|panned|dismissed|declared|named|ranked|listed|compared|argued|observed|summed|"
                    r"labeled|labelled|deemed|considered|hailed|complained|remarked|added|stated|singled out)\b", re.I)
R_CRITICS_GENERAL = re.compile(r"\b(critics|reviewers|reviews|critical (response|reception|consensus)|"
                               r"(critical|universal|widespread|international) acclaim|acclaimed|"
                               r"was (well|poorly|positively|negatively) received|received (generally |mostly |"
                               r"largely |widely |overwhelmingly )?(positive|negative|mixed|favorable|unfavorable|"
                               r"favourable|unfavourable|polarized|polarised))\b", re.I)
R_QUOTE = re.compile(r"[\"“]([^\"“”]{8,400})[\"”]")
_NAME = r"[A-Z][a-zà-ÿ'’.-]+(?:\s(?:[A-Z]\.|[A-Z][a-zà-ÿ'’-]+|de|van|von|la|le|del|da|di)){1,3}"
R_NAME_OF = re.compile(rf"(?P<name>{_NAME})(?:,| of| from| for| at| writing for| writing in)\s+(?:the\s+)?"
                       rf"(?P<outlet>[A-Z][\w.&'’ -]{{1,40}})")
R_OUTLET_POSS = re.compile(rf"(?P<outlet>[A-Z][\w.&'’ -]{{1,40}})['’]s\s+(?:film critic\s+|critic\s+|reviewer\s+)?"
                           rf"(?P<name>{_NAME})")
R_WRITING_FOR = re.compile(rf"(?:writing|in (?:his|her|their|a) review) (?:for|in) (?:the\s+)?(?P<outlet>[A-Z][\w.&'’ -]{{1,40}}),"
                           rf"\s+(?P<name>{_NAME})")
NOT_PEOPLE = {"Rotten Tomatoes", "The Film", "The Movie", "Box Office", "New York", "Los Angeles", "United States",
              "United Kingdom", "North America", "Academy Award", "Academy Awards", "Golden Globe", "In The"}


def outlets_in_text(t):
    """Known outlets named in the sentence, in order: [(canonical, position)]."""
    found = []
    low = t.lower()
    for alias, canon in _ALIAS.items():
        if len(alias) < 3 and alias not in ("ew", "ft", "ap", "gq"):
            continue
        for m in re.finditer(rf"(?<![\w.]){re.escape(alias)}(?![\w])", low):
            if alias in ("time", "slate", "salon", "vox", "spin", "empire", "variety", "paste", "polygon", "observer",
                         "independent", "slant", "wired", "deadline", "ringer", "playlist", "consequence", "new york",
                         "the times", "ft", "ap", "ew", "gq", "hindu", "telegraph", "atlantic", "guardian"):
                # common words: only when capitalised in the original text
                if not t[m.start()].isupper():
                    continue
                if alias == "new york" and re.match(r"new york (times|post|daily|magazine)", low[m.start():]):
                    continue
                if alias in ("time",) and not re.match(r"Time\b(?! (to|for|and|in|of|when|the))", t[m.start():]):
                    continue
            found.append((canon, m.start()))
    found.sort(key=lambda x: x[1])
    out, seen = [], set()
    for c, p in found:
        if c not in seen:
            seen.add(c)
            out.append((c, p))
    return out


def _person_ok(name):
    if not name or name in NOT_PEOPLE:
        return False
    w = name.split()
    if len(w) < 2 or len(w) > 4:
        return False
    if any(x.lower() in ("the", "film", "movie", "review", "critics", "critic", "reviews", "magazine") for x in w):
        return False
    return canon_outlet(name) not in OUTLETS


_ALIAS_LONGEST = sorted(_ALIAS, key=len, reverse=True)


def _starts_with(low, alias):
    return low.startswith(alias) and (len(low) == len(alias) or not low[len(alias)].isalnum())


def outlet_prefix(txt, cited=()):
    """The outlet the text starts with: one of `cited` (the sentence's citations) or a known outlet, else None.
    'Variety called it' -> Variety; 'the Los Angeles Times was less impressed' -> Los Angeles Times."""
    low = re.sub(r"^the\s+", "", (txt or "").strip().lower())
    for e in cited:
        el = re.sub(r"^the\s+", "", (e or "").lower())
        if el and _starts_with(low, el):
            return e
    for alias in _ALIAS_LONGEST:
        if (len(alias) >= 3 or alias in ("ew", "ft", "gq")) and _starts_with(low, alias):
            return _ALIAS[alias]
    return None


def outlet_suffix(txt, cited=()):
    """The outlet the text ENDS with ("... and Variety" before "'s Peter Debruge"), else None."""
    words = (txt or "").strip().split(" ")
    for k in range(len(words)):
        tail = " ".join(words[k:])
        o = outlet_prefix(tail, cited)
        if o and len(re.sub(r"^the\s+", "", tail.lower())) <= len(re.sub(r"^the\s+", "", o.lower())) + 1:
            return o
        tl = re.sub(r"^the\s+", "", tail.lower())
        if tl in _ALIAS:
            return _ALIAS[tl]
    return None


def critic_in_text(t, cited=()):
    """(name, outlet) when the sentence names a critic with their outlet, else (None, None)."""
    for rx in (R_WRITING_FOR, R_NAME_OF):
        for m in rx.finditer(t):
            name = m.group("name").strip()
            o = outlet_prefix(m.group("outlet"), cited)
            if _person_ok(name) and o:
                return name, o
    for m in R_OUTLET_POSS.finditer(t):
        name = m.group("name").strip()
        o = outlet_suffix(m.group("outlet"), cited)
        if _person_ok(name) and o:
            return name, o
    return None, None


def classify(t, section_kind, critic, outlet):
    if R_AGG.search(t):
        return "aggregator"
    if section_kind == "box_office" or (R_BOX.search(t) and not (critic or R_VERB.search(t))):
        return "box_office"
    if R_AUDIENCE.search(t):
        return "audience"
    if critic or (outlet and outlet not in AGGREGATORS and (R_VERB.search(t) or R_QUOTE.search(t))):
        return "critic"
    if R_CRITICS_GENERAL.search(t):
        return "summary"
    if R_QUOTE.search(t) and R_VERB.search(t):
        return "critic"
    return "other"


def statements(wikitext, refs_named, section_kind):
    """One section's wikitext -> [dict] statements with critic / outlet / ref."""
    ref_list = []
    named_defs = dict(refs_named or {})
    for m in _REF.finditer(wikitext):          # local definitions too
        nm = _REF_NAME.search(m.group(0))
        if nm and not m.group(0).rstrip().endswith("/>"):
            named_defs.setdefault(nm.group(1).strip(), m.group(0))

    def mark(m):
        raw = m.group(0)
        if raw.rstrip().endswith("/>"):
            nm = _REF_NAME.search(raw)
            raw = named_defs.get(nm.group(1).strip(), "") if nm else ""
        ref_list.append(raw)
        return f"\x00{len(ref_list) - 1}\x00"

    text = _REF.sub(mark, wikitext)
    text = strip_markup(text)
    out = []
    paras = [p for p in re.split(r"\n\s*\n|\n(?=[*#:;])|\n(?==)", text) if p.strip()]
    for pi, par in enumerate(paras):
        par = re.sub(r"^=+.*?=+\s*$", "", par.strip(), flags=re.M).strip()
        par = re.sub(r"^[*#:;]+\s*", "", par)
        if not par:
            continue
        for si, (sent, ridx) in enumerate(sentences_with_refs(par)):
            infos = [ref_info(ref_list[i]) for i in ridx if 0 <= i < len(ref_list) and ref_list[i]]
            clean = _MARK.sub("", sent).strip()
            if len(clean) < 20:
                continue
            tc, to = critic_in_text(clean, [d["outlet"] for d in infos if d.get("outlet")])
            named = outlets_in_text(clean)
            agg_sentence = bool(R_AGG.search(clean))
            ref_main = None
            for d in infos:                         # the citation that best fits the sentence
                if d.get("outlet") and (agg_sentence or d["outlet"] not in AGGREGATORS):
                    ref_main = d
                    break
            if ref_main is None and infos:
                ref_main = infos[0]
            outlet, osrc = None, ""
            if to:
                outlet, osrc = to, "text"
            elif named:
                pick = [c for c, _p in named if (c not in AGGREGATORS) or agg_sentence]
                if pick:
                    outlet, osrc = pick[0], "text"
            if not outlet and ref_main and ref_main.get("outlet"):
                outlet, osrc = ref_main["outlet"], "ref"
            if not outlet and ref_main and ref_main.get("url"):
                o = outlet_of_url(ref_main["url"])
                if o:
                    outlet, osrc = o, "domain"
            critic, csrc = None, ""
            if tc:
                critic, csrc = tc, "text"
            for d in infos:
                a = d.get("author")
                if a and not critic:
                    last = a.split()[-1] if a.split() else a
                    if last and re.search(rf"\b{re.escape(last)}\b", clean):
                        critic, csrc = a, "text+ref"
            if not critic and ref_main and ref_main.get("author") and outlet and outlet not in AGGREGATORS \
                    and (R_VERB.search(clean) or R_QUOTE.search(clean)) and not R_CRITICS_GENERAL.search(clean[:60]):
                critic, csrc = ref_main["author"], "ref"
            q = R_QUOTE.search(clean)
            kind = classify(clean, section_kind, critic, outlet)
            out.append({"para": pi, "pos": si, "text": clean, "quote": q.group(1).strip() if q else None,
                        "critic": critic, "critic_src": csrc, "outlet": outlet, "outlet_src": osrc,
                        "ref_url": (ref_main or {}).get("url"), "ref_title": (ref_main or {}).get("title"),
                        "ref_date": (ref_main or {}).get("date"), "ref_outlet": (ref_main or {}).get("outlet"),
                        "kind": kind})
    return out


# ------------------------------------------------------------------ commands
def connect(path=None):
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


def _sections(conn, pageid):
    return [(k, h, zlib.decompress(t).decode("utf-8", "replace"), json.loads(zlib.decompress(r).decode() or "{}"),
             fid, rev)
            for k, h, t, r, fid, rev in conn.execute(
                "select kind, heading, wikitext_z, refs_z, tmdb_id, revid from wiki_reception where pageid = ? "
                "order by id", (pageid,))]


def cmd_run(a, conn):
    try:
        pages = conn.execute("select pageid, revid from wiki_reception_done where status = 'ok' and n_sections > 0"
                             ).fetchall()
    except sqlite3.Error:
        sys.exit("no wiki_reception_done table: run `python v7_data.py reception` first")
    done = {p: (r, v) for p, r, v in conn.execute("select pageid, revid, version from critic_statements_done")}
    todo = [(p, r) for p, r in pages if a.again or done.get(p) != (r, VERSION)]
    if a.limit:
        todo = todo[:a.limit]
    print(f"critics: {len(pages):,} pages with reception sections; {len(todo):,} to extract (version {VERSION})",
          flush=True)
    t0, n_st, kinds = time.time(), 0, {}
    for i, (pid, rev) in enumerate(todo, 1):
        rows = []
        for sk, head, wt, refs, fid, srev in _sections(conn, pid):
            for s in statements(wt, refs, sk):
                h = hashlib.sha1(f"{fid}|{s['text']}".encode()).hexdigest()[:16]
                rows.append((fid, pid, srev, sk, head, s["para"], s["pos"], s["text"], s["quote"], s["critic"],
                             s["outlet"], s["outlet_src"], s["critic_src"], s["ref_url"], s["ref_title"],
                             s["ref_date"], s["ref_outlet"], s["kind"], h, VERSION))
                kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        conn.execute("delete from critic_statements where pageid = ?", (pid,))
        conn.executemany("insert into critic_statements(tmdb_id, pageid, revid, section_kind, heading, para, pos, "
                         "text, quote, critic, outlet, outlet_src, critic_src, ref_url, ref_title, ref_date, "
                         "ref_outlet, kind, text_hash, version) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("insert or replace into critic_statements_done values (?,?,?,?,?)",
                     (pid, rev, VERSION, len(rows), F.now()))
        n_st += len(rows)
        if i % 500 == 0 or i == len(todo):
            conn.commit()
            print(f"  {i:,}/{len(todo):,} pages, {n_st:,} statements ({time.time() - t0:.0f}s)  "
                  + ", ".join(f"{k} {v:,}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])), flush=True)
    conn.commit()
    F.set_meta(conn, "critic_statements_at", F.now())
    conn.commit()
    cmd_stats(a, conn)


def cmd_stats(a, conn):
    one = lambda sql, *p: (conn.execute(sql, p).fetchone() or [0])[0] or 0  # noqa: E731
    print(f"critic statements: {one('select count(*) from critic_statements'):,} from "
          f"{one('select count(distinct tmdb_id) from critic_statements'):,} films")
    print("  by kind: " + ", ".join(f"{k} {n:,}" for k, n in conn.execute(
        "select kind, count(*) from critic_statements group by kind order by 2 desc")))
    print("  critic statements with an outlet: "
          f"{one('select count(*) from critic_statements where kind = ? and outlet is not null', 'critic'):,} of "
          f"{one('select count(*) from critic_statements where kind = ?', 'critic'):,}; with a named critic "
          f"{one('select count(*) from critic_statements where kind = ? and critic is not null', 'critic'):,}")
    print("  outlet from: " + ", ".join(f"{k or 'none'} {n:,}" for k, n in conn.execute(
        "select outlet_src, count(*) from critic_statements where kind = 'critic' group by outlet_src order by 2 desc")))
    print("  films with a critic statement from Variety / THR / RogerEbert.com: " + ", ".join(
        f"{o} {one('select count(distinct tmdb_id) from critic_statements where outlet = ? and kind = ?', o, 'critic'):,}"
        for o in ("Variety", "The Hollywood Reporter", "RogerEbert.com", "Chicago Sun-Times")))
    print("  top outlets: " + ", ".join(f"{o} {n:,}" for o, n in conn.execute(
        "select outlet, count(*) from critic_statements where kind = 'critic' and outlet is not null "
        "group by outlet order by 2 desc limit 15")))


def cmd_show(a, conn):
    row = conn.execute("select tmdb_id, title, year from films where title = ? collate nocase order by vote_count desc "
                       "limit 1", (a.title,)).fetchone()
    if not row:
        sys.exit(f"no film {a.title!r}")
    print(f"{row[1]} ({row[2]})")
    for k, t, c, o, osrc, url in conn.execute("select kind, text, critic, outlet, outlet_src, ref_url from "
                                              "critic_statements where tmdb_id = ? order by stmt_id", (row[0],)):
        print(f"  [{k:10s}] {o or '-'} ({osrc or '-'}) / {c or '-'}\n      {t[:220]}\n      {url or ''}")


def cmd_sample(a, conn):
    films = [r[0] for r in conn.execute("select distinct tmdb_id from critic_statements where kind = 'critic'")]
    random.seed(a.seed)
    pick = random.sample(films, min(a.n, len(films)))
    path = os.path.join(HERE, "results", "critic_outlet_check.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["stmt_id", "film", "statement", "outlet_found", "critic_found", "outlet_src", "cited_url",
                    "right (y/n)", "correct outlet if wrong"])
        for fid in pick:
            title = (conn.execute("select title || ' (' || coalesce(year, '?') || ')' from films where tmdb_id = ?",
                                  (fid,)).fetchone() or [str(fid)])[0]
            rows = conn.execute("select stmt_id, text, outlet, critic, outlet_src, ref_url from critic_statements "
                                "where tmdb_id = ? and kind = 'critic' order by random() limit 3", (fid,)).fetchall()
            for sid, t, o, c, osrc, url in rows:
                w.writerow([sid, title, t, o or "", c or "", osrc, url or "", "", ""])
                n += 1
    print(f"{n} statements from {len(pick)} films -> {path}\nFill 'right' with y or n (is outlet_found the outlet "
          f"this statement reports?), then: python critics.py score {os.path.relpath(path, HERE)}")


def cmd_score(a, conn):
    rows = list(csv.DictReader(open(a.path, encoding="utf-8-sig")))
    marked = [r for r in rows if (r.get("right (y/n)") or "").strip().lower()[:1] in ("y", "n")]
    if not marked:
        sys.exit("no rows marked y / n yet")
    ok = sum(1 for r in marked if r["right (y/n)"].strip().lower().startswith("y"))
    films = {r["film"] for r in marked}
    share = ok / len(marked)
    print(f"outlet right: {ok}/{len(marked)} = {share:.1%} over {len(films)} films  (rule: >= 90%) -> "
          f"{'PASS' if share >= 0.9 else 'FAIL'}")
    by = {}
    for r in marked:
        by.setdefault(r.get("outlet_src") or "-", [0, 0])
        by[r.get("outlet_src") or "-"][0] += r["right (y/n)"].strip().lower().startswith("y")
        by[r.get("outlet_src") or "-"][1] += 1
    print("  by where the outlet came from: " + ", ".join(f"{k} {a_}/{b}" for k, (a_, b) in by.items()))
    for r in marked:
        if not r["right (y/n)"].strip().lower().startswith("y"):
            print(f"  wrong: {r['film']}: found {r['outlet_found']!r}, should be {r.get('correct outlet if wrong')!r}: "
                  f"{r['statement'][:120]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "show", "sample", "score", "stats"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    conn = connect(a.facts)
    if a.cmd == "show":
        a.title = a.arg
    elif a.cmd == "sample":
        a.n = int(a.arg or 50)
    elif a.cmd == "score":
        a.path = a.arg or os.path.join(HERE, "results", "critic_outlet_check.csv")
    {"run": cmd_run, "show": cmd_show, "sample": cmd_sample, "score": cmd_score, "stats": cmd_stats}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
