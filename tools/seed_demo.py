"""Local only: fills one browser id with a realistic library through the site's own API (mock server on 8765), so the
redesigned pages can be screenshotted and tested with a full watchlist. Never run against the live site."""
import json, sqlite3, sys, urllib.request
BASE, USER = sys.argv[1], sys.argv[2]
H = {"Content-Type": "application/json", "X-Access-Code": "movies", "X-MB-User": USER}
def call(path, body=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=H, method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(r).read() or b"{}")
f = sqlite3.connect(sys.argv[3])
ids = [r[0] for r in f.execute("select tmdb_id, title from films order by tmdb_id")]
titles = dict(f.execute("select tmdb_id, title from films"))
want = [i for i in ids if i >= 3000000][:34] + [496243, 329865, 157336, 693134]
for i in want:
    call("/api/film", {"id": i, "title": titles[i], "choice": "want"})
for i in [27205, 603, 949, 680, 129, 13, 419430, 120467, 550, 872585, 3000003, 3000005]:
    call("/api/v8/watched", {"id": i})
for i, r, t in [(27205, 5, "Still the best puzzle-box blockbuster."), (680, 4.5, ""), (129, 5, "Every frame a painting."), (949, 4, ""), (13, 3, ""), (550, 4, "Holds up."), (419430, 2, "")]:
    call("/api/v8/review", {"id": i, "rating": r, "text": t})
for sid in (4000001, 4000002, 4000004, 4000005):
    call("/api/v8/tv/follow", {"id": sid, "on": True})
call("/api/v8/tv/progress", {"id": 4000001, "season": 3, "episode": 6})
call("/api/v8/tv/progress", {"id": 4000004, "season": 3, "finished": True})
csv = "Title,Date\n\"Harbor Lights: Season 2: The Night Ferry\",11/7/26\n\"Harbor Lights: Season 2: Low Tide\",11/5/26\n\"Inception\",10/30/26\n"
print(call("/api/v8/import", {"kind": "netflix", "text": csv}))
call("/api/settings", {"settings": {"services": ["netflix", "max"], "decades": ["1990s", "2010s"], "never_genres": ["Horror"]}, "name": "Wes"})
s = call("/api/v8/me/summary")
print(json.dumps(s["stats"]), len(s["activity"]), [x["genre"] for x in s["taste"]["inferred"]][:5], s["favorites"]["label"])
print("continue:", [(x["title"], x["last"]["label"], x["next"] and x["next"]["label"]) for x in call("/api/v8/continue")["items"]])
print("upcoming:", len(call("/api/v8/watchlist/upcoming")["items"]))
