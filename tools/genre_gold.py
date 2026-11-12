"""Round 37 (12 Nov 2026): score the GENRE_MAIN variants on hand-labelled films, on the real facts.db (read-only).
   python tools/genre_gold.py          -> results/genre_gold.txt; last line "BEST=<variant>"

Labels (Claude, 12 Nov; NOT blind - written after round 36's report listed films that left genres): for each film,
genres it clearly IS ("in") and genres TMDB gives it that it clearly is NOT, as a main genre ("out"). Only clear cases
are labelled; everything else is left out of the score.
Score per variant = "in" labels the filter keeps + "out" labels it removes (a label whose genre TMDB does not list at
all counts as kept only for "out"). Variant 0 = TMDB's list (no filter).
Decision rule (written before any run): the highest score wins; a tie keeps the variant now live (1); otherwise the
lower number. todo.sh writes GENRE_MAIN=<winner> to site.env (0 = off)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "web"), ROOT]
import film_info  # noqa: E402
import genres  # noqa: E402

SF, AD, AC, DR, CO, FA, FY, TH, MY, CR, HO, RO, WA, WE, HI, MU, AN, DO = (
    "Science Fiction", "Adventure", "Action", "Drama", "Comedy", "Family", "Fantasy", "Thriller", "Mystery", "Crime",
    "Horror", "Romance", "War", "Western", "History", "Music", "Animation", "Documentary")
GOLD = [  # title, year, in, out
    ("Inception", 2010, [AC, SF], []),
    ("The Dark Knight", 2008, [AC, CR], []),
    ("The Lord of the Rings: The Fellowship of the Ring", 2001, [FY, AD], []),
    ("Star Wars", 1977, [SF, AD], []),
    ("Avatar", 2009, [SF], []),
    ("Guardians of the Galaxy", 2014, [SF], []),
    ("WALL·E", 2008, [AN, SF, FA], []),
    ("Knives Out", 2019, [MY, CO], []),
    ("Se7en", 1995, [TH, CR], []),
    ("The Silence of the Lambs", 1991, [TH, CR], []),
    ("The Shining", 1980, [HO], []),
    ("Gone Girl", 2014, [TH, MY], []),
    ("Get Out", 2017, [HO], []),
    ("Alien", 1979, [HO, SF], []),
    ("Inside Out", 2015, [AN, FA, CO], [DR]),
    ("The Wolf of Wall Street", 2013, [CR, CO], [DR]),
    ("Forrest Gump", 1994, [DR, RO], [CO]),
    ("Despicable Me 3", 2017, [AN, FA, CO], [AC]),
    ("Pulp Fiction", 1994, [CR], [TH]),
    ("Inglourious Basterds", 2009, [WA], [TH]),
    ("Jurassic World", 2015, [SF, AD], [TH]),
    ("The Hunger Games", 2012, [SF], [TH]),
    ("John Wick", 2014, [AC], []),
    ("Toy Story", 1995, [AN, FA, CO], []),
    ("Shrek", 2001, [AN, CO, FA], []),
    ("Zootopia", 2016, [AN, CO, FA], []),
    ("Coco", 2017, [AN, FA, MU], []),
    ("Up", 2009, [AN, FA, AD], []),
    ("Soul", 2020, [AN, FA], [DR]),
    ("Saving Private Ryan", 1998, [WA, DR], []),
    ("Dunkirk", 2017, [WA], []),
    ("The Shawshank Redemption", 1994, [DR], []),
    ("Titanic", 1997, [RO, DR], []),
    ("La La Land", 2016, [RO, MU], []),
    ("The Good, the Bad and the Ugly", 1966, [WE], []),
    ("Unforgiven", 1992, [WE], []),
    ("Django Unchained", 2012, [WE], []),
    ("Home Alone", 1990, [CO, FA], []),
    ("Harry Potter and the Philosopher's Stone", 2001, [FY, AD, FA], []),
    ("Mad Max: Fury Road", 2015, [AC], []),
    ("The Godfather", 1972, [CR, DR], []),
    ("Spirited Away", 2001, [AN, FY], []),
    ("Interstellar", 2014, [SF], []),
    ("The Matrix", 1999, [AC, SF], []),
    ("Free Solo", 2018, [DO], []),
    ("Mamma Mia!", 2008, [MU, CO], []),
]


def main():
    conn = film_info._ro(film_info.FACTS)
    out, best = [], None
    rows = {}
    for title, year, _i, _o in GOLD:
        r = conn.execute("select f.tmdb_id, f.title, f.year, f.genres_json, t.genres from films f left join imdb_titles t "
                         "on t.tconst = f.imdb_id where f.year between ? and ? and f.title = ? order by f.vote_count desc limit 1",
                         (year - 1, year + 1, title)).fetchone()
        rows[title] = r
    found = [g for g in GOLD if rows[g[0]]]
    out.append(f"gold films found: {len(found)} of {len(GOLD)}; missing: {[g[0] for g in GOLD if not rows[g[0]]]}")
    out.append("")
    out.append("film | TMDB | IMDb")
    for title, year, gin, gout in found:
        r = rows[title]
        out.append(f"  {title} ({r[2]}) | {', '.join(json.loads(r[3] or '[]'))} | {r[4]}")
    out.append("")
    scores = {}
    for v in ("0", "1", "2", "3"):
        kept = removed = n_in = n_out = 0
        misses = []
        for title, year, gin, gout in found:
            r = rows[title]
            t = json.loads(r[3] or "[]")
            main_ = t if v == "0" else genres.film_split(t, r[4], v)[0]
            for g in gin:
                n_in += 1
                if g in main_:
                    kept += 1
                else:
                    misses.append(f"{title}: lost {g}" if g in t else f"{title}: {g} not in TMDB")
            for g in gout:
                n_out += 1
                if g not in main_:
                    removed += 1
                else:
                    misses.append(f"{title}: still {g}")
        scores[v] = kept + removed
        out.append(f"variant {v}: in kept {kept}/{n_in}, out removed {removed}/{n_out}, score {kept + removed}")
        out.append("   misses: " + "; ".join(misses))
    top = max(scores.values())
    tied = [v for v in ("0", "1", "2", "3") if scores[v] == top]
    best = "1" if "1" in tied else tied[0]
    out.append("")
    out.append(f"BEST={best}")
    text = "\n".join(out)
    print(text)
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    with open(os.path.join(ROOT, "results", "genre_gold.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
