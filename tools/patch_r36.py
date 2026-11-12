"""Round 36 (12 Nov): onboarding fixes, applied as exact anchored edits so unrelated server changes are kept.
1. web/user_prefs.py: hand-picked example posters per taste group (PICKS), looked up by title + year; the old
   keyword query only fills a group when a pick is missing. The query showed the most-voted films with the genre and
   ANY one keyword: John Wick under thrillers ("revenge"), The Wolf of Wall Street and Inside Out under dramas
   ("biography", "family relationships"), Forrest Gump under comedy ("friendship").
2. web/v8/app.css: onboarding posters get height: auto. The <img height="138"> attribute acts as a CSS height and
   beat aspect-ratio, so "Seen any of these?" posters were cropped to landscape.
3. GENRE_MAIN (web/genres.py, switch in site.env): Home genre chips, chat genre filters / Pick Tonight films,
   Pick Tonight shows and the Profile taste summary use each title's MAIN genres (TMDB and IMDb agree; IMDb for shows);
   "never recommend" uses TMDB + IMDb. Off = TMDB's lists, as before.
Writes nothing unless every anchor matches exactly once.   python tools/patch_r36.py [--check]"""
import sys
from pathlib import Path

PICKS_BLOCK = '''# 12 Nov (round 36): hand-picked examples, looked up by title and year (+-1). A pick that is missing from the
# catalogue (or has no poster) is skipped and the keyword query below fills the gap. Up to 4 per group; 3 are shown.
PICKS = {
    "scifi": [("Inception", 2010), ("The Matrix", 1999), ("Ex Machina", 2015), ("Eternal Sunshine of the Spotless Mind", 2004)],
    "space": [("Star Wars", 1977), ("Alien", 1979), ("The Martian", 2015), ("Arrival", 2016)],
    "heroes": [("The Dark Knight", 2008), ("The Avengers", 2012), ("Spider-Man: Into the Spider-Verse", 2018), ("Black Panther", 2018)],
    "thrill": [("Prisoners", 2013), ("The Silence of the Lambs", 1991), ("Nightcrawler", 2014), ("Se7en", 1995)],
    "mystery": [("Knives Out", 2019), ("Shutter Island", 2010), ("Gone Girl", 2014), ("Memento", 2000)],
    "comedy": [("Superbad", 2007), ("The Hangover", 2009), ("The Grand Budapest Hotel", 2014), ("Anchorman: The Legend of Ron Burgundy", 2004)],
    "drama": [("The Green Mile", 1999), ("Good Will Hunting", 1997), ("The Pursuit of Happyness", 2006), ("Manchester by the Sea", 2016)],
    "horror": [("The Shining", 1980), ("The Conjuring", 2013), ("Get Out", 2017), ("Hereditary", 2018)],
    "anim": [("Finding Nemo", 2003), ("The Lion King", 1994), ("Spirited Away", 2001), ("Up", 2009)],
    "family": [("Home Alone", 1990), ("Paddington 2", 2017), ("E.T. the Extra-Terrestrial", 1982), ("Jumanji", 1995)],
    "crime": [("The Godfather", 1972), ("Goodfellas", 1990), ("The Departed", 2006), ("Pulp Fiction", 1994)],
    "action": [("Mad Max: Fury Road", 2015), ("Die Hard", 1988), ("Raiders of the Lost Ark", 1981), ("John Wick", 2014)],
    "romance": [("The Notebook", 2004), ("Pride & Prejudice", 2005), ("Before Sunrise", 1995), ("Notting Hill", 1999)],
    "war": [("Saving Private Ryan", 1998), ("Schindler's List", 1993), ("Dunkirk", 2017), ("1917", 2019)],
    "western": [("The Good, the Bad and the Ugly", 1966), ("Unforgiven", 1992), ("True Grit", 2010), ("Once Upon a Time in the West", 1968)],
    "music": [("La La Land", 2016), ("Bohemian Rhapsody", 2018), ("Whiplash", 2014), ("The Greatest Showman", 2017)],
    "docs": [("Free Solo", 2018), ("March of the Penguins", 2005), ("Won't You Be My Neighbor?", 2018), ("Super Size Me", 2004)],
    "classic": [("Casablanca", 1942), ("Psycho", 1960), ("2001: A Space Odyssey", 1968), ("12 Angry Men", 1957)],
}


def _norm_title(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in t).split())


def _pick_rows(f, title, year):
    want = _norm_title(title)
    rows = _q(f, "select f.tmdb_id, f.title, f.year, i.poster_path from films f join film_images i on i.tmdb_id = f.tmdb_id "
                 "where f.in_catalog = 1 and i.poster_path is not null and f.year between ? and ? order by f.vote_count desc",
              (year - 1, year + 1))
    return [r for r in rows if _norm_title(r[1]) == want]


def _picked(f, key, used):
    """Up to 3 hand-picked examples for a group, skipping films already shown in another group."""
    out = []
    for title, year in PICKS.get(key, ()):
        for fid, t, y, p in _pick_rows(f, title, year):
            if fid not in used:
                used.add(fid)
                out.append({"id": fid, "title": t, "year": y, "poster": IMG + p})
                break
        if len(out) == 3:
            break
    return out


def missing_picks():
    """[(group, title, year)] for picks not found in the catalogue with a poster (the keyword query fills in)."""
    f = _facts()
    return [(k, t, y) for k, lst in PICKS.items() for t, y in lst if not _pick_rows(f, t, y)]


'''

EDITS = [
    ("web/user_prefs.py", "_GCACHE = {}\n", PICKS_BLOCK + "_GCACHE = {}\n"),
    ("web/user_prefs.py",
     "        films = []\n        for fid, t, y, p in rows:\n",
     "        films = _picked(f, key, used)                # 12 Nov: hand-picked examples first, query fills gaps\n"
     "        for fid, t, y, p in rows:\n            if len(films) >= 3:\n                break\n"),
    ("web/v8/app.css", ".tg-pp img { width: 100%; aspect-ratio: 2 / 3;", ".tg-pp img { width: 100%; height: auto; aspect-ratio: 2 / 3;"),
    (
        "web/v8/app.css", ".tf img { width: 100%; aspect-ratio: 2 / 3;", ".tf img { width: 100%; height: auto; aspect-ratio: 2 / 3;"),
    # ---- GENRE_MAIN (web/genres.py): main genres for filters; every block is a no-op while the switch is off
    ("web/user_prefs.py", '    never = set(s.get("never_genres") or [])\n',
     '    never = set(s.get("never_genres") or [])\n'
     '    gm_all = {}                                         # 12 Nov GENRE_MAIN: main genres for "only", broad for "never"\n'
     '    try:\n'
     '        import genres as _G\n'
     '        if _G.ON:\n'
     '            gm_all = _G.films([h.get("id") for h in hits if h.get("id") is not None])\n'
     '    except ImportError:\n'
     '        pass\n'),
    ("web/user_prefs.py", '            gs = {g.strip() for g in str(h["genres"]).split(",")}\n',
     '            gs = {g.strip() for g in str(h["genres"]).split(",")}\n'
     '        gm, gb = (set(gm_all[fid][0]), set(gm_all[fid][1])) if gm_all.get(fid) and gm_all[fid][0] else (gs, gs)\n'),
    ("web/user_prefs.py", "        if (never & gs) or", "        if (never & gb) or"),
    ("web/user_prefs.py", "        elif only and gs and not (only & gs):", "        elif only and gm and not (only & gm):"),
    ("web/discovery.py", "def _genres_of(ids):\n    out = {}\n",
     "def _genres_of(ids):\n"
     "    try:                                                # 12 Nov GENRE_MAIN: main genres (web/genres.py)\n"
     "        import genres as _G\n"
     "        if _G.ON:\n"
     "            return {k: set(v[0]) for k, v in _G.films(ids).items()}\n"
     "    except ImportError:\n"
     "        pass\n"
     "    out = {}\n"),
    ("web/discovery.py", "    return [x[0] for x in rows][:limit], len(rows)\n",
     "    ids = [x[0] for x in rows]\n"
     "    if sel:                                             # 12 Nov: the SQL test is TMDB's list; keep main-genre films\n"
     "        ids = match_genres(ids, list(sel), mode)\n"
     "    return ids[:limit], len(ids)\n"),
    ("web/tvdata.py", "    horror = set()\n",
     "    tvm = {}\n"
     "    try:                                                # 12 Nov GENRE_MAIN: IMDb's main genres for each show\n"
     "        import genres as _G\n"
     "        if _G.ON and (genre or never):\n"
     "            tvm = _G.shows(ids)\n"
     "    except ImportError:\n"
     "        pass\n"
     "    horror = set()\n"),
    ("web/tvdata.py", "        if genre:\n            want = MOOD_TV.get(genre)\n",
     "        if genre and tvm.get(sid) and tvm[sid][0]:\n"
     "            if not _G.tv_mood_ok(tvm[sid][0], genre, sid in horror) and not drop(\"not the mood you picked\"):\n"
     "                continue\n"
     "        elif genre:\n            want = MOOD_TV.get(genre)\n"),
    ("web/tvdata.py", "        if never and set(site_genres(list(tg))) & never and not drop",
     "        if never and (set(site_genres(list(tg))) | (tvm[sid][1] if sid in tvm else set())) & never and not drop"),
    ("web/v8extra.py", "    gcount, total = {}, 0\n",
     "    gmain = {}\n"
     "    try:                                                # 12 Nov GENRE_MAIN: taste summary from main genres\n"
     "        import genres as _G\n"
     "        if _G.ON:\n"
     "            gmain = {k: list(v[2]) for k, v in _G.films(basis_ids).items()}\n"
     "    except ImportError:\n"
     "        pass\n"
     "    gcount, total = {}, 0\n"),
    ("web/v8extra.py", '        for g in (meta.get(i) or {}).get("genres", []):\n',
     '        for g in (gmain.get(i) or (meta.get(i) or {}).get("genres", [])):\n'),
]


def main():
    check = "--check" in sys.argv
    text, bad = {}, 0
    for path, old, new in EDITS:
        if path not in text:
            text[path] = Path(path).read_text(encoding="utf-8")
        n = text[path].count(old)
        if n != 1:
            print(f"ANCHOR {path}: found {n} times (want 1): {old.strip()[:70]!r}")
            bad = 1
            continue
        text[path] = text[path].replace(old, new)
    if bad:
        print("nothing written")
        return 1
    if "PICKS = {" in Path("web/user_prefs.py").read_text(encoding="utf-8") or "GENRE_MAIN" in Path("web/discovery.py").read_text(encoding="utf-8"):
        print("already patched; nothing written")
        return 1
    if check:
        print("all anchors match (check only)")
        return 0
    for path, t in text.items():
        Path(path).write_text(t, encoding="utf-8")
        print(f"patched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
