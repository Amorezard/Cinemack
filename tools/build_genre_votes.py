"""Round 39 (12 Nov 2026): a third genre vote from the MovieLens tag genome, for GENRE_MAIN variant 4.
   python tools/build_genre_votes.py            (read-only on facts.db and data/genome/genome.npz)
   -> logs/genre_votes.db: genome_genre(tmdb_id, genre, rel), genre_cut(genre, tag, cut, n_agree, n_films)

The genome (MovieLens 25M; Vig, Sen and Riedl) scores ~1,100 tags 0..1 for ~13,800 films from what people wrote and
rated. For each BROAD genre (the ones TMDB adds loosely: Drama, Comedy, Action, Adventure, Family, Fantasy, Thriller,
Romance) the vote is the highest relevance among that genre's tag names that exist in the genome.
Cut-off per genre, fixed by the data and NOT by the labelled films: the 25th percentile of the genre's relevance among
films where TMDB and IMDb both list the genre (films everyone agrees on). Variant 4 keeps a broad genre IMDb did not
list when the genome scores it at or above that cut-off. Films the genome does not cover behave as variant 2."""
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "web"), str(ROOT)]
import genres  # noqa: E402

TAGS = {  # genre -> genome tag names to try (the first ones found are used; the vote is their maximum)
    "Drama": ["drama"],
    "Comedy": ["comedy", "funny"],
    "Action": ["action", "action packed"],
    "Adventure": ["adventure"],
    "Family": ["family", "family film", "kids and family"],
    "Fantasy": ["fantasy", "fantasy world"],
    "Thriller": ["thriller", "suspense", "suspenseful"],
    "Romance": ["romance", "romantic"],
}
CANDIDATES = [Path(os.environ["GENOME_NPZ"])] if os.environ.get("GENOME_NPZ") else [ROOT / "data" / "genome" / "genome.npz", ROOT.parent / "data" / "genome" / "genome.npz",
              Path.home() / "movie-buff" / "data" / "genome" / "genome.npz"]
OUT = Path(os.environ.get("GENRE_VOTES_DB", str(ROOT / "logs" / "genre_votes.db")))
PCT = 25


def main():
    import numpy as np
    path = next((p for p in CANDIDATES if p.exists()), None)
    if path is None:
        print("no genome.npz found in", [str(p) for p in CANDIDATES])
        return 1
    z = np.load(path, allow_pickle=False)
    ids, tags, M = z["tmdb_ids"], [str(t) for t in z["tags"]], z["relevance"]
    col = {t.lower(): i for i, t in enumerate(tags)}
    print(f"genome: {len(ids):,} films x {len(tags):,} tags ({path})")
    use = {}
    for g, names in TAGS.items():
        found = [n for n in names if n in col]
        use[g] = found
        print(f"  {g:10} tags {found or 'NONE'}")
    rows_of = {int(t): i for i, t in enumerate(ids)}
    facts = sqlite3.connect(f"file:{os.environ.get('FACTS_DB', str(ROOT / 'facts.db'))}?mode=ro", uri=True)
    films = facts.execute("select f.tmdb_id, f.genres_json, t.genres from films f left join imdb_titles t on t.tconst = f.imdb_id "
                          "where f.tmdb_id in (" + ",".join(str(i) for i in rows_of) + ")").fetchall() if rows_of else []
    votes, agree = [], {g: [] for g in TAGS}
    for fid, gj, ig in films:
        i = rows_of.get(fid)
        if i is None:
            continue
        t, im = json.loads(gj or "[]"), genres.imdb_genres(ig)
        for g, names in use.items():
            if not names:
                continue
            rel = float(max(M[i][col[n]] for n in names))
            votes.append((fid, g, round(rel, 4)))
            if g in t and g in im:
                agree[g].append(rel)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    c = sqlite3.connect(tmp)
    c.executescript("create table genome_genre(tmdb_id integer, genre text, rel real, primary key (tmdb_id, genre));"
                    "create table genre_cut(genre text primary key, tag text, cut real, n_agree integer, n_films integer);")
    c.executemany("insert into genome_genre values (?,?,?)", votes)
    for g, rels in agree.items():
        if not use[g] or len(rels) < 30:
            print(f"  {g:10} no cut-off (agreed films: {len(rels)})")
            continue
        cut = float(np.percentile(rels, PCT))
        c.execute("insert into genre_cut values (?,?,?,?,?)", (g, ",".join(use[g]), round(cut, 4), len(rels), len(films)))
        print(f"  {g:10} cut {cut:.3f} (p{PCT} of {len(rels):,} films TMDB and IMDb agree on)")
    c.commit()
    c.close()
    os.replace(tmp, OUT)
    print(f"wrote {len(votes):,} votes for {len(films):,} catalogue films -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
