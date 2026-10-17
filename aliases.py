#!/usr/bin/env python3
"""
v4 (14-15 Oct): nicknames, abbreviations and alternative titles (ALIASES=1 in config.yaml).

"Who directed T2?", "is LOTR worth watching", "the Shawshank one", "Rocky 4" - titles people
actually type are often not the catalogue's title, and titles_in() only matches exact titles.
With ALIASES=1, retrieve.py (and chat.py, for the prompt) rewrite the question first:

    "who directed T2?"  ->  "who directed T2 (Terminator 2: Judgment Day, 1991)?"

The user's own words stay; the catalogue title is added next to them, so the planner, title
matching and keyword search all see it. An alias that fits several films lists them all
("Dark Knight (The Dark Knight, 2008 / The Dark Knight Rises, 2012)") and the planner's
"ambiguous" rule then asks which one.

Sources (table aliases in facts.db, rebuilt by `build`):
  article    a catalogue title without its leading The / A / An, if 2+ words remain
             ("Shawshank Redemption", "Dark Knight")
  numeral    Roman <-> Arabic sequel numbers ("Rocky 4" <-> "Rocky IV", "Godfather Part 2")
  wikidata   English "also known as" labels from Wikidata (films with a Wikidata id)
  curated    a hand list of common abbreviations and nicknames (CURATED below)
Guards: an alias that is itself another film's exact title is dropped; one-word aliases only from
the curated list; acronyms (all capitals / digits) match only as typed (case-sensitive).

    python aliases.py build            # ~10 min (Wikidata in batches of 50 ids)
    python aliases.py try "who directed T2 and is LOTR better"
    python aliases.py stats
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

MOVIES = os.path.join(HERE, "movies.db")
UA = "MovieBuff/4 (student research project; Merrimack College) python-urllib"
ROMAN = {"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
ARABIC = {v: k for k, v in ROMAN.items()}
# alias -> exact catalogue title (year optional, "title|year"). Acronyms are matched case-sensitively.
CURATED = {
    "LOTR": "The Lord of the Rings: The Fellowship of the Ring|2001",
    "Fellowship of the Ring": "The Lord of the Rings: The Fellowship of the Ring|2001",
    "Two Towers": "The Lord of the Rings: The Two Towers|2002",
    "Return of the King": "The Lord of the Rings: The Return of the King|2003",
    "ROTK": "The Lord of the Rings: The Return of the King|2003",
    "T1": "The Terminator|1984", "T2": "Terminator 2: Judgment Day|1991", "T3": "Terminator 3: Rise of the Machines|2003",
    "BTTF": "Back to the Future|1985", "BTTF2": "Back to the Future Part II|1989", "BTTF3": "Back to the Future Part III|1990",
    "ESB": "The Empire Strikes Back|1980", "Empire Strikes Back": "The Empire Strikes Back|1980",
    "ROTJ": "Return of the Jedi|1983", "ANH": "Star Wars|1977", "A New Hope": "Star Wars|1977",
    "TPM": "Star Wars: Episode I - The Phantom Menace|1999", "Phantom Menace": "Star Wars: Episode I - The Phantom Menace|1999",
    "AOTC": "Star Wars: Episode II - Attack of the Clones|2002", "Attack of the Clones": "Star Wars: Episode II - Attack of the Clones|2002",
    "ROTS": "Star Wars: Episode III - Revenge of the Sith|2005", "Revenge of the Sith": "Star Wars: Episode III - Revenge of the Sith|2005",
    "TFA": "Star Wars: The Force Awakens|2015", "Force Awakens": "Star Wars: The Force Awakens|2015",
    "TLJ": "Star Wars: The Last Jedi|2017", "Last Jedi": "Star Wars: The Last Jedi|2017",
    "Rise of Skywalker": "Star Wars: The Rise of Skywalker|2019",
    "GOTG": "Guardians of the Galaxy|2014", "Endgame": "Avengers: Endgame|2019", "Avengers 4": "Avengers: Endgame|2019",
    "Infinity War": "Avengers: Infinity War|2018", "Avengers 3": "Avengers: Infinity War|2018",
    "Fury Road": "Mad Max: Fury Road|2015", "Road Warrior": "Mad Max 2|1981",
    "EEAAO": "Everything Everywhere All at Once|2022", "Shawshank": "The Shawshank Redemption|1994",
    "GBU": "The Good, the Bad and the Ugly|1966", "Good Bad and Ugly": "The Good, the Bad and the Ugly|1966",
    "OUATIH": "Once Upon a Time... in Hollywood|2019", "OUATITW": "Once Upon a Time in the West|1968",
    "Kill Bill 1": "Kill Bill: Vol. 1|2003", "Kill Bill 2": "Kill Bill: Vol. 2|2004",
    "Godfather 2": "The Godfather Part II|1974", "Godfather 3": "The Godfather Part III|1990",
    "Alien 3": "Alien³|1992", "Aliens 2": "Aliens|1986", "Raiders": "Raiders of the Lost Ark|1981",
    "Temple of Doom": "Indiana Jones and the Temple of Doom|1984", "Last Crusade": "Indiana Jones and the Last Crusade|1989",
    "Philosopher's Stone": "Harry Potter and the Philosopher's Stone|2001",
    "Sorcerer's Stone": "Harry Potter and the Philosopher's Stone|2001",
    "Prisoner of Azkaban": "Harry Potter and the Prisoner of Azkaban|2004",
    "2001 A Space Odyssey": "2001: A Space Odyssey|1968", "Dr Strangelove": "Dr. Strangelove or: How I Learned to Stop Worrying and Love the Bomb|1964",
    "Strangelove": "Dr. Strangelove or: How I Learned to Stop Worrying and Love the Bomb|1964",
    "Spirited Away": "Spirited Away|2001", "Totoro": "My Neighbor Totoro|1988", "Mononoke": "Princess Mononoke|1997",
    "Eternal Sunshine": "Eternal Sunshine of the Spotless Mind|2004", "Birdman": "Birdman or (The Unexpected Virtue of Ignorance)|2014",
    "Borat": "Borat: Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan|2006",
    "Pirates of the Caribbean": "Pirates of the Caribbean: The Curse of the Black Pearl|2003",
    "POTC": "Pirates of the Caribbean: The Curse of the Black Pearl|2003",
    "Close Encounters": "Close Encounters of the Third Kind|1977", "Spinal Tap": "This Is Spinal Tap|1984",
    "Wrath of Khan": "Star Trek II: The Wrath of Khan|1982", "Tokyo Story": "Tokyo Story|1953",
}
DDL = "create table if not exists aliases(alias text, norm text, tmdb_id integer, title text, year text, source text);" \
      "create index if not exists aliases_norm on aliases(norm);"


def norm(s):
    s = str(s or "").casefold().replace("&", "and")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def is_acronym(a):
    return bool(re.fullmatch(r"[A-Z][A-Z0-9]{1,6}", a))


def catalogue():
    m = sqlite3.connect(f"file:{MOVIES}?mode=ro", uri=True)
    rows = m.execute("select id, title, release_year, vote_count from movies where title is not null").fetchall()
    m.close()
    return rows


def _get(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print(f"  {str(e)[:80]}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


def build(skip_wikidata=False):
    rows = catalogue()
    titles = {}
    for mid, t, y, vc in rows:
        titles.setdefault(norm(t), []).append((mid, t, y, vc or 0))
    out = []

    def add(alias, mid, t, y, src):
        n = norm(alias)
        if not n or n == norm(t):
            return
        if n in titles and all(x[0] != mid for x in titles[n]):    # another film's real title
            return
        if " " not in n and src != "curated":                       # one word only from the hand list
            return
        out.append((alias, n, mid, t, str(y or "")[:4], src))

    for mid, t, y, vc in rows:
        forms = [t.strip()]
        m = re.match(r"^(the|a|an) (.+)$", t.strip(), re.I)
        if m and len(m.group(2).split()) >= 2:
            add(m.group(2), mid, t, y, "article")
            forms.append(m.group(2))
        for f in forms:                    # number variants of the title and of its article-less form
            m = re.search(r"\b(II|III|IV|V|VI|VII|VIII|IX|X)$", f)
            if m:
                add(f[:m.start()] + str(ROMAN[m.group(1)]), mid, t, y, "numeral")
            m = re.search(r"\b([2-9]|10)$", f)
            if m and int(m.group(1)) in ARABIC:
                add(f[:m.start()] + ARABIC[int(m.group(1))], mid, t, y, "numeral")
            m = re.search(r"\bPart (II|III|IV|V)\b", f)
            if m:
                add(f.replace(f"Part {m.group(1)}", str(ROMAN[m.group(1)])), mid, t, y, "numeral")
                add(f.replace(f"Part {m.group(1)}", f"Part {ROMAN[m.group(1)]}"), mid, t, y, "numeral")
    for alias, target in CURATED.items():
        t, _, y = target.partition("|")
        cands = titles.get(norm(t), [])
        if y:
            cands = [c for c in cands if str(c[2] or "")[:4] == y] or cands
        if cands:
            mid, tt, yy, _vc = max(cands, key=lambda c: c[3])
            add(alias, mid, tt, yy, "curated")
    if not skip_wikidata:
        c = F.connect(readonly=True)
        qids = {}
        if c is not None:
            ids = {r[0] for r in rows}
            for fid, q in c.execute("select tmdb_id, wikidata_id from films where wikidata_id like 'Q%'"):
                if fid in ids:
                    qids[q] = fid
        by_id = {mid: (t, y) for mid, t, y, _ in rows}
        ql = list(qids)
        print(f"Wikidata aliases for {len(ql):,} films ...")
        for s in range(0, len(ql), 50):
            d = _get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "wbgetentities", "ids": "|".join(ql[s:s + 50]), "props": "aliases",
                 "languages": "en", "format": "json"}))
            for q, ent in ((d or {}).get("entities") or {}).items():
                fid = qids.get(q)
                for al in ((ent.get("aliases") or {}).get("en") or []):
                    v = al.get("value", "")
                    if 3 <= len(v) <= 80 and fid in by_id:
                        add(v, fid, *by_id[fid], "wikidata")
            if (s // 50) % 40 == 0:
                print(f"  {s + 50:,}/{len(ql):,}", flush=True)
            time.sleep(0.3)
    c = F.connect()
    c.executescript(DDL)
    c.execute("delete from aliases")
    c.executemany("insert into aliases values (?,?,?,?,?,?)", list(dict.fromkeys(out)))
    c.commit()
    stats()


def stats():
    c = F.connect(readonly=True)
    for src, n in c.execute("select source, count(*) from aliases group by source"):
        print(f"  {src:9s} {n:,}")
    amb = c.execute("select count(*) from (select norm from aliases group by norm having count(distinct tmdb_id) > 1)").fetchone()[0]
    print(f"  aliases naming 2+ films: {amb:,}")


_IDX = {}


def _index():
    if "map" not in _IDX:
        c = F.connect(readonly=True)
        m = {}
        try:
            for alias, n, mid, t, y, src in c.execute("select alias, norm, tmdb_id, title, year, source from aliases"):
                m.setdefault(n, {})[mid] = (alias, t, y, src)
        except (sqlite3.Error, AttributeError):
            pass
        _IDX["map"] = m
        _IDX["max_words"] = max((len(k.split()) for k in m), default=0)
    return _IDX["map"], _IDX["max_words"]


def expand(question):
    """The question with each alias followed by its catalogue title(s) in brackets. Idempotent."""
    amap, maxw = _index()
    if not amap or not question:
        return question
    toks = list(re.finditer(r"[A-Za-z0-9][A-Za-z0-9'’.]*", question))
    nq = f" {norm(question)} "
    inserts, i = [], 0
    while i < len(toks):
        hit = None
        for w in range(min(maxw, len(toks) - i), 0, -1):
            span = question[toks[i].start():toks[i + w - 1].end()]
            films = amap.get(norm(span))
            if not films:
                continue
            if all(is_acronym(a) for a, *_ in films.values()) and span not in {a for a, *_ in films.values()}:
                continue                          # acronyms only as typed: "it" is not "IT"
            hit = (w, toks[i + w - 1].end(), films)
            break
        if hit:
            w, end, films = hit
            names = [(t, y) for _a, t, y, _s in films.values()]
            if not any(f" {norm(t)} " in nq for t, _y in names):          # already named: leave it
                inserts.append((end, " (" + " / ".join(f"{t}, {y}" if y else t for t, y in names[:3]) + ")"))
            i += w
        else:
            i += 1
    for pos, text in sorted(inserts, reverse=True):
        question = question[:pos] + text + question[pos:]
    return question


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "build":
        build(skip_wikidata="--no-wikidata" in a)
    elif a and a[0] == "try":
        print(expand(" ".join(a[1:])))
    elif a and a[0] == "stats":
        stats()
    else:
        print(__doc__)
