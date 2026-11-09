"""Local layout fixture only (never shipped): a small facts.db so the v8 page has something to lay out offline."""
import json, sqlite3, sys, datetime as dt
p = sys.argv[1]
c = sqlite3.connect(p)
c.executescript("""
drop table if exists films; drop table if exists film_images; drop table if exists lists; drop table if exists certifications;
drop table if exists watch_providers; drop table if exists tv_shows; drop table if exists tv_seasons; drop table if exists imdb_ratings;
drop table if exists critic_scores; drop table if exists people; drop table if exists credits; drop table if exists money;
create table films(tmdb_id integer primary key, title text, original_title text, year integer, release_date text, us_release_date text,
  vote_count integer, vote_average real, runtime integer, genres_json text, imdb_id text, popularity real, overview text, tagline text,
  collection_name text, original_language text);
create table film_images(tmdb_id integer primary key, poster_path text, backdrop_path text);
create table lists(list_name text, region text, tmdb_id integer, rank integer, fetched_at text);
create table certifications(tmdb_id integer, region text, certification text, release_type integer, release_date text);
create table watch_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer);
create table tv_shows(tmdb_id integer primary key, name text, original_name text, first_year integer, vote_count integer, networks_json text, poster_path text);
create table tv_seasons(tmdb_id integer, season_number integer, air_date text, poster_path text);
create table imdb_ratings(imdb_id text primary key, rating real, votes integer);
create table critic_scores(tmdb_id integer primary key, rt_pct integer, rt_n integer, mc_score integer, cinemascore text);
create table people(person_id integer primary key, name text);
create table credits(tmdb_id integer, person_id integer, job text, kind text, character text, ord integer);
create table money(tmdb_id integer primary key, budget real, budget_src text, gross real, gross_src text);
""")
F = [(496243, "Parasite", 2019, 132, ["Comedy", "Thriller", "Drama"], 8.5, 18000, "tt6751668"),
     (329865, "Arrival", 2016, 116, ["Drama", "Science Fiction", "Mystery"], 7.6, 17000, "tt2543164"),
     (666277, "Past Lives", 2023, 106, ["Drama", "Romance"], 7.7, 1800, "tt13238346"),
     (545611, "Everything Everywhere All at Once", 2022, 140, ["Action", "Adventure", "Science Fiction"], 7.8, 6000, "tt6710474"),
     (27205, "Inception", 2010, 148, ["Action", "Science Fiction", "Adventure"], 8.4, 36000, "tt1375666"),
     (603, "The Matrix", 1999, 136, ["Action", "Science Fiction"], 8.2, 25000, "tt0133093"),
     (949, "Heat", 1995, 170, ["Crime", "Drama", "Action"], 7.9, 7000, "tt0113277"),
     (680, "Pulp Fiction", 1994, 154, ["Thriller", "Crime"], 8.5, 27000, "tt0110912"),
     (129, "Spirited Away", 2001, 125, ["Animation", "Family", "Fantasy"], 8.5, 16000, "tt0245429"),
     (13, "Forrest Gump", 1994, 142, ["Comedy", "Drama", "Romance"], 8.5, 27000, "tt0109830"),
     (419430, "Get Out", 2017, 104, ["Mystery", "Thriller", "Horror"], 7.6, 17000, "tt5052448"),
     (120467, "The Grand Budapest Hotel", 2014, 100, ["Comedy", "Drama"], 8.0, 15000, "tt2278388"),
     (157336, "Interstellar", 2014, 169, ["Adventure", "Drama", "Science Fiction"], 8.4, 35000, "tt0816692"),
     (550, "Fight Club", 1999, 139, ["Drama"], 8.4, 29000, "tt0137523"),
     (872585, "Oppenheimer", 2023, 181, ["Drama", "History"], 8.1, 9000, "tt15398776"),
     (346698, "Barbie", 2023, 114, ["Comedy", "Adventure"], 7.0, 9000, "tt1517268"),
     (502356, "The Super Mario Bros. Movie", 2023, 92, ["Animation", "Family", "Comedy"], 7.6, 9000, "tt6718170"),
     (1022789, "Inside Out 2", 2024, 97, ["Animation", "Family", "Comedy"], 7.6, 5000, "tt22022452"),
     (693134, "Dune: Part Two", 2024, 167, ["Science Fiction", "Adventure"], 8.2, 6000, "tt15239678"),
     (940721, "Godzilla Minus One", 2023, 125, ["Science Fiction", "Horror", "Action"], 7.6, 2000, "tt23289160"),
     (2000001, "Upcoming Film A", 2026, 110, ["Drama"], None, 0, None), (2000002, "Upcoming Film B", 2026, 98, ["Comedy"], None, 0, None),
     (2000003, "Upcoming Film C", 2026, 124, ["Thriller"], None, 0, None), (2000004, "Upcoming Film D", 2027, 101, ["Animation", "Family"], None, 0, None)]
today = dt.date(2026, 11, 9)
for i, (fid, t, y, rt, g, va, vc, imdb) in enumerate(F):
    c.execute("insert into films values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (fid, t, t, y, f"{y}-06-01", f"{y}-06-01", vc, va, rt,
              json.dumps(g), imdb, 100 - i, "Fixture overview for layout testing only. " * (6 if i == 0 else 2), None, None, "en"))
    c.execute("insert into film_images values (?,?,?)", (fid, f"/demo{fid}.jpg", f"/bd{fid}.jpg"))
    if imdb:
        c.execute("insert into imdb_ratings values (?,?,?)", (imdb, va, vc * 50))
        c.execute("insert into critic_scores values (?,?,?,?,?)", (fid, 90 - i, 300, 80 - i, "A"))
now = today.isoformat() + "T06:00:00"
for r, fid in enumerate([693134, 940721, 1022789, 346698, 872585, 502356]):
    c.execute("insert into lists values ('now_playing','US',?,?,?)", (fid, r + 1, now))
for r, fid in enumerate([2000001, 2000002, 2000003, 2000004]):
    c.execute("insert into lists values ('upcoming','US',?,?,?)", (fid, r + 1, now))
    d = today + dt.timedelta(days=4 + r * 9)
    c.execute("insert into certifications values (?, 'US', 'PG-13', 3, ?)", (fid, d.isoformat() + "T00:00:00.000Z"))
    c.execute("insert into certifications values (?, 'US', '', 4, ?)", (fid, (d + dt.timedelta(days=45)).isoformat() + "T00:00:00.000Z"))
for fid in (496243, 329865, 27205, 603):
    c.execute("insert into watch_providers values (?, 'US', 'flatrate', 8, 'Netflix', 1)", (fid,))
    c.execute("insert into watch_providers values (?, 'US', 'rent', 2, 'Apple TV', 2)", (fid,))
c.execute("insert into tv_shows values (66732, 'Stranger Things', 'Stranger Things', 2016, 9000, '[\"Netflix\"]', '/st.jpg')")
c.execute("insert into tv_seasons values (66732, 6, ?, null)", ((today + dt.timedelta(days=20)).isoformat(),))
c.execute("insert into people values (1, 'Bong Joon Ho'), (2, 'Song Kang-ho'), (3, 'Choi Woo-shik')")
c.execute("insert into credits values (496243, 1, 'Director', 'crew', null, 0), (496243, 2, null, 'cast', 'Ki-taek', 0), (496243, 3, null, 'cast', 'Ki-woo', 1)")
c.execute("insert into money values (496243, 11400000, 'fixture', 258000000, 'fixture')")
c.commit()
print("demo rows", c.execute("select count(*) from films").fetchone())
