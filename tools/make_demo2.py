"""Local layout fixture only (never shipped, never run on the server): extends make_demo.py's facts.db with ~70 made-up
films, TV shows with seasons, a month of US release dates, providers and certifications, so the redesigned pages can be
laid out and tested offline. Titles are invented placeholders, not real releases."""
import datetime as dt, json, random, sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(p)
random.seed(7)
today = dt.date.today()
A = ["Northern", "Silent", "Glass", "Paper", "Midnight", "Iron", "Hollow", "Crimson", "Quiet", "Last", "Distant", "Velvet", "Broken", "Golden", "Second", "Lost"]
B = ["Signal", "Harbor", "Orchard", "Frontier", "Ledger", "Lantern", "Echo", "Passage", "Meridian", "Tide", "Archive", "Summit", "Crossing", "Garden", "Circuit", "Canyon"]
G = ["Thriller", "Drama", "Comedy", "Science Fiction", "Action", "Mystery", "Horror", "Romance", "Animation", "Family", "Crime", "Documentary"]
CERT = ["G", "PG", "PG-13", "PG-13", "R", "R"]
PROV = ["Netflix", "Max", "Disney Plus", "Hulu", "Amazon Prime Video", "Apple TV Plus"]
fid = 3000000
films = []
for i in range(70):
    fid += 1
    t = f"{random.choice(A)} {random.choice(B)}" + ("" if i % 5 else " II")
    y = random.choice([2019, 2021, 2023, 2024, 2025, 2026, 2026])
    rt = random.choice([84, 88, 92, 97, 104, 112, 118, 126, 133, 141, 152])
    gs = random.sample(G, 2)
    rel = today + dt.timedelta(days=random.randint(-600, -40)) if i < 40 else today + dt.timedelta(days=random.randint(-10, 50))
    films.append((fid, t, y, rt, gs, rel, i))
    c.execute("insert or replace into films(tmdb_id,title,original_title,year,release_date,us_release_date,vote_count,vote_average,runtime,genres_json,imdb_id,popularity,overview,original_language) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (fid, t, t, rel.year, rel.isoformat(), rel.isoformat(), random.randint(300, 9000), round(random.uniform(5.9, 8.4), 1), rt, json.dumps(gs), None, random.uniform(20, 200),
               "Fixture synopsis for layout testing: a short description of the story that runs to two or three lines on a card.", "en"))
    c.execute("insert or replace into film_images values (?,?,?)", (fid, f"/fx{fid}.jpg", f"/fxb{fid}.jpg"))
    c.execute("insert into certifications values (?, 'US', ?, ?, ?)", (fid, random.choice(CERT), 3 if i % 3 else 4, rel.isoformat() + "T00:00:00.000Z"))
    if i % 3 == 0 and i >= 40:
        d2 = rel + dt.timedelta(days=random.randint(1, 25))
        c.execute("insert into certifications values (?, 'US', ?, 4, ?)", (fid, random.choice(CERT), d2.isoformat() + "T00:00:00.000Z"))
    if i < 40 and i % 2 == 0:
        for pn in random.sample(PROV, random.randint(1, 2)):
            c.execute("insert into watch_providers values (?, 'US', 'flatrate', ?, ?, 1)", (fid, PROV.index(pn) + 1, pn))
    if 40 <= i < 52:
        c.execute("insert into lists values ('now_playing','US',?,?,?)", (fid, i, today.isoformat() + "T06:00:00"))
    if i >= 52:
        c.execute("insert into lists values ('upcoming','US',?,?,?)", (fid, i, today.isoformat() + "T06:00:00"))
c.execute("insert into watch_providers values (496243,'US','flatrate',1,'Hulu',1)")
c.execute("insert into watch_providers values (329865,'US','flatrate',2,'Max',1)")
for sid, name, nets, seasons in ((4000001, "The Long Shift", ["HBO"], [(1, -900), (2, -520), (3, -120), (4, 30)]),
                                 (4000002, "Harbor Lights", ["Netflix"], [(1, -400), (2, -60)]),
                                 (4000003, "Second Orbit", ["Apple TV+"], [(1, -700), (2, -300), (3, None)]),
                                 (4000004, "Kitchen Rules", ["FX"], [(1, -800), (2, -420), (3, -40), (4, 3)]),
                                 (4000005, "Field Notes", ["Hulu"], [(1, -200), (2, 12)])):
    c.execute("insert or replace into tv_shows values (?,?,?,?,?,?,?)", (sid, name, name, 2020, 3000, json.dumps(nets), f"/tv{sid}.jpg"))
    for sn, off in seasons:
        c.execute("insert into tv_seasons values (?,?,?,null)", (sid, sn, (today + dt.timedelta(days=off)).isoformat() if off is not None else None))
c.commit()
print("films", c.execute("select count(*) from films").fetchone()[0], "shows", c.execute("select count(*) from tv_shows").fetchone()[0])
