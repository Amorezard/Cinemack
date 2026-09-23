import ast
import sqlite3
import pandas as pd
import glob
import os
import sys
from datetime import date, timedelta

CREDITS_FILE = "tmdb_5000_credits.csv"
TOP_CAST_COUNT = 5


def load_credits(path):
    if not os.path.exists(path):
        print(f"({path} not found -- skipping cast/director backfill)")
        return {}

    credits_df = pd.read_csv(path)
    lookup = {}
    for _, row in credits_df.iterrows():
        try:
            crew = ast.literal_eval(row["crew"]) if isinstance(row["crew"], str) else []
            cast = ast.literal_eval(row["cast"]) if isinstance(row["cast"], str) else []
        except (ValueError, SyntaxError):
            continue

        directors = [c["name"] for c in crew if c.get("job") == "Director"]
        director = ", ".join(directors) if directors else None

        cast_sorted = sorted(cast, key=lambda c: c.get("order", 999))
        top_cast = ", ".join(c["name"] for c in cast_sorted[:TOP_CAST_COUNT]) or None

        lookup[int(row["movie_id"])] = (director, top_cast)

    print(f"Loaded credits for {len(lookup):,} movies from {path}")
    return lookup


credits_lookup = load_credits(CREDITS_FILE)

candidates = sorted(glob.glob("TMDB_movie_dataset_v*.csv")) or sorted(glob.glob("TMDB_all_movies*.csv"))
if not candidates:
    print("Couldn't find a TMDB_movie_dataset_v*.csv file in this folder.")
    print("Download it from https://www.kaggle.com/datasets/asaniczka/tmdb-movies-dataset-2023-930k-movies")
    print("and unzip it here first.")
    sys.exit(1)

CSV_FILE = candidates[-1]
print(f"Using {CSV_FILE}")

DB_FILE = "movies.db"

USE_COLS = [
    "id", "title", "overview", "genres", "keywords", "tagline",
    "release_date", "vote_average", "vote_count", "popularity",
    "original_language", "status", "adult", "production_companies",
]

MIN_VOTE_COUNT = 50    # bar for older movies -- "well-known enough that someone would ask about it"
RECENT_YEARS = 2       # movies released within this many years count as "recent"
RECENT_TOP_N = 3000    # how many recent movies to keep, ranked by popularity (not vote_count)
MAX_ROWS = 60000       # hard cap on the OLDER bucket so embedding time stays reasonable

CUTOFF_DATE = (date.today() - timedelta(days=365 * RECENT_YEARS)).isoformat()
print(f"Treating anything released on/after {CUTOFF_DATE} as 'recent' -- ranked by popularity, no vote_count bar")

recent_chunks = []
older_chunks = []
total_seen = 0
for chunk in pd.read_csv(CSV_FILE, usecols=USE_COLS, chunksize=100_000, low_memory=False):
    total_seen += len(chunk)

    base_ok = (
        (chunk["status"] == "Released")
        & (chunk["adult"] != True)  # noqa: E712
        & chunk["overview"].notna()
        & (chunk["overview"].str.strip() != "")
    )
    release_date_str = chunk["release_date"].fillna("").astype(str)
    is_recent = release_date_str >= CUTOFF_DATE

    recent_part = chunk[base_ok & is_recent]
    older_part = chunk[base_ok & ~is_recent & (chunk["vote_count"] >= MIN_VOTE_COUNT)]

    if len(recent_part):
        recent_chunks.append(recent_part)
    if len(older_part):
        older_chunks.append(older_part)

    kept_so_far = sum(len(c) for c in recent_chunks) + sum(len(c) for c in older_chunks)
    print(f"  scanned {total_seen:,} rows so far, kept {kept_so_far:,}", end="\r")

print()
recent_df = pd.concat(recent_chunks, ignore_index=True) if recent_chunks else pd.DataFrame(columns=USE_COLS)
older_df = pd.concat(older_chunks, ignore_index=True) if older_chunks else pd.DataFrame(columns=USE_COLS)
print(f"Recent-eligible (Released, not adult, has overview, since {CUTOFF_DATE}): {len(recent_df):,}")
print(f"Older movies passing vote_count>={MIN_VOTE_COUNT}: {len(older_df):,}")

recent_df = recent_df.sort_values("popularity", ascending=False).head(RECENT_TOP_N).reset_index(drop=True)
older_df = older_df.sort_values("vote_count", ascending=False).head(MAX_ROWS).reset_index(drop=True)

df = pd.concat([recent_df, older_df], ignore_index=True)
print(f"Kept top {len(recent_df):,} recent movies by popularity + top {len(older_df):,} older movies by vote_count "
      f"= {len(df):,} total")


def release_year(date_str):
    if not isinstance(date_str, str) or len(date_str) < 4:
        return None
    return date_str[:4]


def clean_list_field(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    return ", ".join(part.strip() for part in value.split(",") if part.strip())


def build_embed_text(row, director, top_cast):
    year = release_year(row["release_date"]) or "unknown year"
    genres = clean_list_field(row["genres"])
    keywords = clean_list_field(row["keywords"])
    tagline = row["tagline"] if isinstance(row["tagline"], str) else ""
    overview = row["overview"] if isinstance(row["overview"], str) else ""

    parts = [f"{row['title']} ({year})"]
    if genres:
        parts.append(f"Genres: {genres}")
    if director:
        parts.append(f"Director: {director}")
    if top_cast:
        parts.append(f"Starring: {top_cast}")
    if tagline.strip():
        parts.append(f"Tagline: {tagline.strip()}")
    parts.append(f"Rating: {row['vote_average']}/10 from {int(row['vote_count'])} votes")
    if keywords:
        parts.append(f"Keywords: {keywords}")
    parts.append(f"Plot: {overview}")
    return "\n".join(parts)


conn = sqlite3.connect(DB_FILE)
conn.execute("DROP TABLE IF EXISTS movies")
conn.execute("""
    CREATE TABLE movies (
        id INTEGER PRIMARY KEY,
        title TEXT,
        overview TEXT,
        genres TEXT,
        director TEXT,
        top_cast TEXT,
        release_year TEXT,
        vote_average REAL,
        vote_count INTEGER,
        popularity REAL,
        embed_text TEXT
    )
""")

rows = []
matched = 0
for _, row in df.iterrows():
    director, top_cast = credits_lookup.get(int(row["id"]), (None, None))
    if director or top_cast:
        matched += 1
    rows.append((
        int(row["id"]),
        row["title"],
        row["overview"] if isinstance(row["overview"], str) else "",
        clean_list_field(row["genres"]),
        director,
        top_cast,
        release_year(row["release_date"]),
        float(row["vote_average"]) if pd.notna(row["vote_average"]) else None,
        int(row["vote_count"]) if pd.notna(row["vote_count"]) else 0,
        float(row["popularity"]) if pd.notna(row["popularity"]) else None,
        build_embed_text(row, director, top_cast),
    ))

conn.executemany(
    "INSERT INTO movies (id, title, overview, genres, director, top_cast, release_year, vote_average, vote_count, popularity, embed_text) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    rows,
)
conn.commit()
conn.close()

print(f"Wrote {len(rows):,} movies to {DB_FILE} ({matched:,} of them have cast/director from the overlap with {CREDITS_FILE})")
