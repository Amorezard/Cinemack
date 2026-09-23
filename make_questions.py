"""Training question bank from movies.db. Films in test_set.json are excluded."""
import json, random, sqlite3, unicodedata, ollama
random.seed(7)
PARAPHRASE_MODEL = "gemma3:27b"

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch for ch in s.casefold() if ch.isalnum() and not unicodedata.combining(ch))

tests = json.load(open("test_set.json")); tests = tests if isinstance(tests, list) else tests["questions"]
test_text = norm(" ".join(" ".join(q["turns"]) + " " + " ".join(q.get("expect_retrieved", [])) for q in tests))
def in_tests(t):
    n = norm(t); return len(n) >= 4 and n in test_text

c = sqlite3.connect("movies.db")
all_titles = {norm(t) for (t,) in c.execute("select title from movies")}
films = [f for f in c.execute(
    "select id, title, release_year, genres, director, top_cast, overview, vote_count from movies "
    "where vote_count >= 500 and coalesce(director,'') != '' and coalesce(overview,'') != '' "
    "order by vote_count desc").fetchall() if not in_tests(f[1])]
random.shuffle(films)
pool = iter(films)
take = lambda n: [next(pool) for _ in range(n)]
yr = lambda y: (y or "")[:4]
Q = []
def add(cat, q, notes):
    Q.append({"id": f"t{len(Q)+1:04d}", "category": cat, "turns": [q], "expect_behavior": notes})

for i, (_, t, y, g, d, cast, o, v) in enumerate(take(60)):
    if i % 2 == 0:
        add("credits", f"Who directed {t}?", f"PASS = names {d} as director, from the context. No review.")
    else:
        names = [p.split(" as ")[0].strip() for p in (cast or "").split(", ")][:3]
        add("credits", f"Who stars in {t}?", f"PASS = names cast from the context, e.g. {', '.join(names)}. No review.")
for _, t, y, *_ in take(50):
    add("general", f"Tell me about {t}.", f"PASS = accurate summary of {t} ({yr(y)}) from the context; no invented facts.")
for _, t, y, *_ in take(40):
    add("opinion", f"Is {t} worth watching?",
        "PASS = clear verdict first and who would enjoy it, based on the rating and plot in the context; no invented facts.")
for _, t, y, *_ in take(20):
    add("recommend", f"I just watched {t} and loved it. What should I watch next?",
        f"PASS = recommends two or three films from the context (not {t}), each with a one-line reason; no films outside the context.")
genres = sorted({g.strip() for (gs,) in c.execute("select genres from movies where genres is not null")
                 for g in gs.split(",") if g.strip()})
for _ in range(30):
    g, dec = random.choice(genres), random.choice([1970, 1980, 1990, 2000, 2010])
    add("recommend", f"Recommend me some {g.lower()} movies from the {dec}s.",
        "PASS = recommends two or three films from the context, each with a one-line reason; no films outside the context.")

print("paraphrasing plots into 'which movie' questions with", PARAPHRASE_MODEL, "...")
made = 0
for _, t, y, g, d, cast, o, v in take(80):
    if made == 60: break
    r = ollama.chat(model=PARAPHRASE_MODEL, options={"temperature": 0.7}, messages=[{"role": "user", "content":
        f"Film plot: {o}\n\nWrite ONE question someone might ask when they remember this story but not the "
        f"title, like \"What's the movie where ...?\". Do not use the title, character names or actor names. "
        f"Reply with the question only."}])
    q = r["message"]["content"].strip().strip('"')
    if not q or norm(t) in norm(q) or len(q) > 250: continue
    add("identify", q, f"PASS = identifies {t} ({yr(y)}) and describes it from the context.")
    made += 1

credited = [norm(t) for (t,) in c.execute("select title from movies where coalesce(director,'') != ''")]
n = 0
for t, y in c.execute("select title, release_year from movies where coalesce(director,'') = '' "
                      "and vote_count >= 100 order by vote_count desc limit 300"):
    if n == 20: break
    nt = norm(t)
    if len(t.split()) < 2 or in_tests(t) or any(o.startswith(nt) or (len(o) >= 4 and nt.startswith(o)) for o in credited):
        continue
    add("credits_missing", f"Who directed {t}?",
        f"{t} has NO director in the database. PASS = says it doesn't have the director. FAIL = names anyone.")
    n += 1

adjs = ["Cobalt", "Silent", "Hollow", "Crimson", "Paper", "Glass", "Northern", "Velvet", "Distant", "Amber", "Salt", "Winter"]
nouns = ["Orchard", "Harbor", "Lantern", "Meridian", "Tide", "Ledger", "Signal", "Garden", "Frontier", "Mirror", "Station", "Choir"]
n = 0
while n < 20:
    title = f"The {random.choice(adjs)} {random.choice(nouns)}"
    if norm(title) in all_titles: continue
    add("no_match", f"What can you tell me about the {random.randint(1965, 2015)} film {title}?",
        "This film does not exist. PASS = says it has nothing on it. FAIL = any plot, cast or director.")
    all_titles.add(norm(title)); n += 1

json.dump(Q, open("train_questions.json", "w"), indent=2, ensure_ascii=False)
from collections import Counter
print(f"\n{len(Q)} questions:", dict(Counter(q["category"] for q in Q)))
for q in random.sample(Q, 6): print(f"  [{q['category']}] {q['turns'][0]}")
