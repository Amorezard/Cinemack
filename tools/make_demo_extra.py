"""Local layout fixture only (never shipped, never run on the server): a made-up logs/tmdb_extra.db (TV ratings, new
seasons, missing film artwork) and, in the fixture facts.db, a 7-minute short on the lists and a busy release day, so
round 18's Home TV rows and the calendar's full day lists can be checked offline. Names are invented placeholders."""
import datetime as dt, json, random, sqlite3, sys
facts, out = sys.argv[1], sys.argv[2]
random.seed(18)
today = dt.date.today()
f = sqlite3.connect(facts)
# a short film on now-playing and in the calendar (must not appear in either)
f.execute("insert or replace into films(tmdb_id,title,original_title,year,release_date,us_release_date,vote_count,vote_average,runtime,genres_json,popularity,overview,original_language) "
          "values (3900001,'ee','ee',2026,?,?,40,7.9,7,'[\"Animation\"]',999,'A seven-minute short.','en')", (today.isoformat(), today.isoformat()))
f.execute("delete from lists where tmdb_id = 3900001"); f.execute("delete from certifications where tmdb_id = 3900001")
f.execute("insert into lists values ('now_playing','US',3900001,0,(select max(fetched_at) from lists where list_name='now_playing'))")
f.execute("insert into certifications values (3900001,'US','',3,?)", ((today + dt.timedelta(days=3)).isoformat() + "T00:00:00.000Z",))
# a busy day: 11 more releases three days from now (theatrical, digital), some without artwork
busy = (today + dt.timedelta(days=3)).isoformat()
W = ["Amber", "Static", "Cobalt", "Winter", "Neon", "Marble", "Feral", "Lunar", "Signal", "Hollow", "Fable"]
V = ["Road", "Season", "Witness", "Engine", "Choir", "Harvest", "Kingdom", "Line", "House", "Coast", "Pact"]
for i in range(11):
    fid = 3900100 + i
    f.execute("delete from certifications where tmdb_id = ?", (fid,)); f.execute("delete from watch_providers where tmdb_id = ?", (fid,))
    f.execute("insert or replace into films(tmdb_id,title,original_title,year,release_date,us_release_date,vote_count,vote_average,runtime,genres_json,popularity,overview,original_language) "
              "values (?,?,?,2026,?,?,?,?,?,?,?,?,'en')", (fid, f"{W[i]} {V[i]}", f"{W[i]} {V[i]}", busy, busy, random.randint(30, 900), round(random.uniform(5.8, 8.1), 1),
                                                          random.choice([92, 104, 118, 131]), json.dumps(random.sample(["Drama", "Thriller", "Comedy", "Horror", "Action"], 2)), 50 + i, "Fixture synopsis.", ))
    if i % 4 != 3:
        f.execute("insert or replace into film_images values (?,?,?)", (fid, f"/fx{fid}.jpg", f"/fxb{fid}.jpg" if i % 3 else None))
    else:
        f.execute("delete from film_images where tmdb_id = ?", (fid,))
    f.execute("insert into certifications values (?, 'US', 'PG-13', ?, ?)", (fid, 4 if i < 5 else 3, busy + "T00:00:00.000Z"))
    if i < 3:
        f.execute("insert into watch_providers values (?, 'US', 'flatrate', 8, 'Netflix', 1)", (fid,))
f.commit()
x = sqlite3.connect(out)
x.executescript("""drop table if exists meta; drop table if exists tv_show; drop table if exists tv_season; drop table if exists film_art;
create table meta(key text primary key, value text);
create table tv_show(tmdb_id integer primary key, name text, first_year integer, vote_average real, vote_count integer, popularity real, genres_json text, tv_genres_json text, poster_path text, backdrop_path text, networks_json text, original_language text, top_rank integer);
create table tv_season(tmdb_id integer, season_number integer, air_date text, episode_count integer, poster_path text, primary key (tmdb_id, season_number));
create table film_art(tmdb_id integer primary key, poster_path text, backdrop_path text, runtime integer);""")
TG = [("Drama", ["Drama"]), ("Comedy", ["Comedy"]), ("Crime", ["Crime"]), ("Sci-Fi & Fantasy", ["Science Fiction", "Fantasy"]), ("Animation", ["Animation"]),
      ("Mystery", ["Mystery"]), ("Action & Adventure", ["Action", "Adventure"]), ("Documentary", ["Documentary"])]
A = ["Night", "River", "Copper", "Paper", "Open", "Dead", "Bright", "Other", "Far", "Low"]
B = ["Shift", "Line", "City", "Kings", "Water", "Hours", "Station", "Side", "Fields", "Light"]
sid = 5000000
for i in range(60):
    sid += 1
    g = random.sample(TG, 2)
    tvg = [a for a, _ in g] + (["Talk"] if i == 7 else [])
    mapped = [m for _, ms in g for m in ms]
    top = i + 1 if i < 45 else None
    vc = random.randint(350, 25000) if top else random.randint(25, 900)
    x.execute("insert into tv_show values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, f"{A[i % 10]} {B[(i * 3) % 10]}" + ("" if i < 10 else f" {i}"), random.randint(2008, 2025),
              round(random.uniform(7.4, 8.9) if top else random.uniform(6.2, 8.0), 1), vc, random.uniform(10, 400), json.dumps(mapped), json.dumps(tvg),
              f"/tv{sid}.jpg", f"/tvb{sid}.jpg", json.dumps([random.choice(["HBO", "Netflix", "FX", "Apple TV+", "Hulu", "AMC"])]), "en", top))
    if i >= 30:                                   # 30 shows with seasons; about half premiered in the last 30 days
        n = random.randint(1, 5)
        for s in range(1, n + 1):
            d = today - dt.timedelta(days=random.randint(0, 28)) if s == n and i % 2 == 0 else today - dt.timedelta(days=400 * (n - s + 1)) if s < n else today + dt.timedelta(days=random.randint(2, 40))
            x.execute("insert into tv_season values (?,?,?,?,?)", (sid, s, d.isoformat(), 8, None))
for fid in (3900103, 3900107):                    # artwork for two busy-day films that had none
    x.execute("insert into film_art values (?,?,?,?)", (fid, f"/fx{fid}.jpg", f"/fxb{fid}.jpg", 112))
x.execute("insert into meta values ('fetched_at', ?)", (dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),))
x.commit()
print("fixture ok", busy)
