"""Local layout fixture only (never run on the server's data; tests/test_v8_r21.py runs it on a temp database): rebuilds the fixture facts.db's TV
tables with the SERVER's schema (v7_data.py tv + imdb, fetch_episodes.py) and invented shows, seasons, episodes, US
ratings, providers, cast, trailers and IMDb episode ratings, so round 21 (show page, TV picks, episodes in the
calendar, next episode in Continue Watching, Top Rated Shows by IMDb) can be tested offline. Names are placeholders."""
import datetime as dt, json, random, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
random.seed(21)
today = dt.date.today()
D = lambda n: (today + dt.timedelta(days=n)).isoformat()  # noqa: E731
c.executescript("""
drop table if exists tv_shows; drop table if exists tv_seasons; drop table if exists tv_credits; drop table if exists tv_keywords;
drop table if exists tv_videos; drop table if exists tv_ratings; drop table if exists tv_providers; drop table if exists tv_episodes;
drop table if exists tv_episodes_done; drop table if exists imdb_episodes; drop table if exists imdb_titles; drop table if exists film_logos;
create table tv_shows(tmdb_id integer primary key, imdb_id text, tvdb_id integer, wikidata_id text,
  name text, original_name text, first_air_date text, last_air_date text, first_year integer, end_year integer,
  status text, type text, in_production integer, n_seasons integer, n_episodes integer, episode_runtime integer,
  original_language text, origin_country_json text, genres_json text, networks_json text, companies_json text,
  created_by_json text, overview text, tagline text, popularity real, vote_average real, vote_count integer,
  poster_path text, backdrop_path text, fetched_at text);
create table tv_seasons(tmdb_id integer, season_number integer, name text, air_date text,
  episode_count integer, overview text, vote_average real, poster_path text, primary key (tmdb_id, season_number));
create table tv_credits(tmdb_id integer, person_id integer, name text, kind text, job text, character text, episodes integer, ord integer, profile_path text);
create table tv_keywords(tmdb_id integer, keyword_id integer, name text);
create table tv_videos(tmdb_id integer, video_key text, type text, name text, official integer, published_at text, primary key (tmdb_id, video_key));
create table tv_ratings(tmdb_id integer, region text, rating text, primary key (tmdb_id, region));
create table tv_providers(tmdb_id integer, region text, kind text, provider_id integer, provider_name text, priority integer, primary key (tmdb_id, region, kind, provider_id));
create table tv_episodes(tmdb_id integer, season_number integer, episode_number integer, name text, air_date text, overview text, runtime integer,
  still_path text, vote_average real, vote_count integer, episode_type text, fetched_at text, primary key (tmdb_id, season_number, episode_number));
create table imdb_titles(tconst text primary key, title_type text, primary_title text, original_title text, is_adult integer, start_year integer, end_year integer, runtime integer, genres text);
create table imdb_episodes(tconst text primary key, parent text, season integer, episode integer);
create table film_logos(tmdb_id integer primary key, logo_path text, aspect real, lang text, fetched_at text);
""")
cols = [r[1] for r in c.execute("pragma table_info(imdb_ratings)")]
if not cols:
    c.execute("create table imdb_ratings(imdb_id text primary key, rating real, votes integer, fetched_at text)")
A = ["Night", "River", "Copper", "Paper", "Open", "Dead", "Bright", "Other", "Far", "Low", "Glass", "Silver"]
B = ["Shift", "Line", "City", "Kings", "Water", "Hours", "Station", "Side", "Fields", "Light", "Harbor", "Orbit"]
GEN = [["Drama", "Crime"], ["Comedy"], ["Sci-Fi & Fantasy", "Drama"], ["Mystery", "Crime"], ["Animation", "Kids", "Family"],
       ["Action & Adventure", "Drama"], ["Comedy", "Drama"], ["Documentary"], ["Reality"], ["Drama"], ["Talk"], ["Family", "Comedy"]]
NETS = [("HBO", "Max"), ("Netflix", "Netflix"), ("FX", "Hulu"), ("Apple TV+", "Apple TV Plus"), ("AMC", "AMC+"), ("Prime Video", "Amazon Prime Video")]
CERT = ["TV-MA", "TV-14", "TV-PG", "TV-G", "TV-Y7", None]
ids = [4000001, 4000002, 4000003, 4000004, 4000005] + list(range(6000001, 6000036))
LEGACY = {4000001: [-900, -520, -120, 30], 4000002: [-400, -60], 4000003: [-700, -300, None], 4000004: [-800, -420, -40, 3], 4000005: [-200, 12]}
names = {4000001: "The Long Shift", 4000002: "Harbor Lights", 4000003: "Second Orbit", 4000004: "Kitchen Rules", 4000005: "Field Notes"}
n_ep = 0
for k, sid in enumerate(ids):
    name = names.get(sid) or f"{A[k % 12]} {B[(k * 5) % 12]}" + ("" if k < 12 else f" {k}")
    g = GEN[k % len(GEN)]
    net, prov = NETS[k % len(NETS)]
    ns = 1 + k % 5
    imdb = f"tt9{sid}"
    running = k % 3 != 0
    rt = random.choice([22, 28, 45, 52, 58, 64])
    c.execute("insert into tv_shows values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (sid, imdb, None, None, name, name, D(-400 * ns), None, today.year - ns, None if running else today.year - 1,
               "Returning Series" if running else "Ended", "Scripted", int(running), ns, ns * 8, rt, "en", '["US"]', json.dumps(g),
               json.dumps([net]), "[]", json.dumps([{"id": 1, "name": "A. Creator"}]), "Fixture synopsis of the show: a short paragraph about the premise.",
               "", random.uniform(5, 300), round(random.uniform(6.8, 8.9), 1), random.randint(60, 9000), f"/tv{sid}.jpg", f"/tvb{sid}.jpg", D(0)))
    c.execute("insert into tv_ratings values (?, 'US', ?)", (sid, CERT[k % len(CERT)])) if CERT[k % len(CERT)] else None
    c.execute("insert into tv_providers values (?, 'US', 'flatrate', ?, ?, 1)", (sid, k % 6 + 1, prov))
    c.execute("insert or replace into imdb_ratings(imdb_id, rating, votes) values (?,?,?)", (imdb, round(random.uniform(6.5, 9.3), 1),
                                                                                        random.choice([800, 4000, 25000, 60000, 180000, 900000])))
    for j in range(5):
        c.execute("insert into tv_credits values (?,?,?,'cast','Actor',?,?,?,?)", (sid, 900000 + k * 10 + j, f"Actor {k}-{j}", f"Role {j}", 8 * ns, j, None))
    c.execute("insert into tv_videos values (?, ?, 'Trailer', 'Official Trailer', 1, ?)", (sid, f"yt{sid}", D(-30)))
    if k % 12 == 5:
        c.execute("insert into tv_keywords values (?, 1, 'horror')", (sid,))
    legacy = LEGACY.get(sid)                     # the five shows make_demo2.py made: same seasons and dates as before
    if legacy:
        ns = len(legacy)
        c.execute("update tv_shows set n_seasons = ?, status = 'Returning Series', in_production = 1 where tmdb_id = ?", (ns, sid))
    for s in range(1, ns + 1):
        # the latest season of a running show: half aired, half upcoming (weekly)
        last = s == ns
        start = -400 * (ns - s) - (21 if last and running else 300)
        if legacy:
            start = legacy[s - 1]
            if start is None:
                c.execute("insert into tv_seasons values (?,?,?,?,?,?,?,?)", (sid, s, f"Season {s}", None, None, "", None, None))
                continue
        c.execute("insert into tv_seasons values (?,?,?,?,?,?,?,?)", (sid, s, f"Season {s}", D(start), 8, "", None, None))
        for e in range(1, 9):
            n_ep += 1
            d = D(start + 7 * (e - 1))
            typ = "finale" if e == 8 else "standard"
            c.execute("insert into tv_episodes values (?,?,?,?,?,?,?,?,?,?,?,?)", (sid, s, e, f"Episode {e} of {name}", d, "What happens in this episode.",
                                                                                rt, f"/st{sid}{s}{e}.jpg", round(random.uniform(6.5, 9.0), 1), random.randint(0, 40), typ, D(0)))
            t = f"tt8{sid}{s:02d}{e:02d}"
            c.execute("insert into imdb_episodes values (?,?,?,?)", (t, imdb, s, e))
            c.execute("insert into imdb_titles values (?, 'tvEpisode', ?, ?, 0, ?, null, ?, '')", (t, f"Episode {e} of {name}", f"Episode {e}", int(d[:4]), rt))
            if d <= D(0):
                c.execute("insert or replace into imdb_ratings(imdb_id, rating, votes) values (?,?,?)", (t, round(random.uniform(6.8, 9.7), 1), random.randint(50, 30000)))
for fid, in c.execute("select tmdb_id from films limit 60").fetchall():
    c.execute("insert or replace into film_logos values (?,?,?,?,?)", (fid, f"/logo{fid}.png", 3.0, "en", D(0)))
c.commit()
print("TV fixture:", len(ids), "shows,", n_ep, "episodes")
