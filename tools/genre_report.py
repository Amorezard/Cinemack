"""Round 36 (12 Nov): read-only report on the real facts.db - what GENRE_MAIN changes.
   python tools/genre_report.py            (run from ~/movie-buff/rag; writes results/genre_main_report.txt too)"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), ROOT]
import film_info  # noqa: E402
import genres  # noqa: E402
import tvdata  # noqa: E402
import user_prefs  # noqa: E402

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def switch(on):
    genres.ON = on
    genres._cache["t"] = 0.0


conn = film_info._ro(film_info.FACTS)
rows = conn.execute("select f.tmdb_id, f.title, f.year, f.genres_json, f.vote_count, t.genres from films f "
                    "left join imdb_titles t on t.tconst = f.imdb_id where f.vote_count >= 1000 and f.in_catalog = 1 "
                    "order by f.vote_count desc").fetchall()
have = sum(1 for r in rows if r[5] and r[5] != "\\N")
say(f"films with 1,000+ votes in the catalogue: {len(rows):,}; with IMDb genres: {have:,} ({100 * have / max(len(rows), 1):.1f}%)")
say("")
say("== per genre (films with 1,000+ votes): TMDB list -> main genres; most-voted films that leave the genre")
for g in genres.SITE:
    before, after, gone = 0, 0, []
    for fid, t, y, gj, v, ig in rows:
        tm = json.loads(gj or "[]")
        main, _b = genres.film_split(tm, ig)
        if g in tm:
            before += 1
            if g in main:
                after += 1
            else:
                gone.append(f"{t} ({y})")
    say(f"{g:16} {before:5} -> {after:5}   leaves: {', '.join(gone[:8])}")

say("")
say("== Pick Tonight films: the 400 most-voted films through the strict mood filter (user_prefs.excluded)")
hits = [{"id": r[0], "title": r[1]} for r in rows[:400]]
name = {r[0]: f"{r[1]} ({r[2]})" for r in rows}
for mood, g in (("Funny", "Comedy"), ("Thrilling", "Thriller"), ("Moving", "Drama"), ("Mind-bending", "Science Fiction"),
                ("Scary", "Horror"), ("Cosy", "Family")):
    res = {}
    for on in (False, True):
        switch(on)
        ex = user_prefs.excluded({"only_genres": [g], "strict": True}, hits)
        res[on] = [h["id"] for h in hits if h["id"] not in ex]
    removed = [i for i in res[False] if i not in res[True]]
    say(f"{mood} ({g}): {len(res[False])} -> {len(res[True])} kept")
    say(f"   top kept now: {', '.join(name[i] for i in res[True][:8])}")
    say(f"   no longer:    {', '.join(name[i] for i in removed[:10])}")

say("")
say("== Pick Tonight shows (tvdata.pick, familiar, no other limits): top 5 before -> after")
for mood, g in (("Funny", "Comedy"), ("Thrilling", "Thriller"), ("Moving", "Drama"), ("Mind-bending", "Science Fiction"),
                ("Scary", "Horror"), ("Cosy", "Family")):
    lines = []
    for on in (False, True):
        switch(on)
        r = tvdata.pick("", g, None, None, None, [], "familiar", n=5)
        lines.append(", ".join(x.get("name") or x.get("title") or str(x.get("id")) for x in r["picks"]))
    say(f"{mood}:\n   before: {lines[0]}\n   after:  {lines[1]}")

switch(True)
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
with open(os.path.join(ROOT, "results", "genre_main_report.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(OUT) + "\n")
