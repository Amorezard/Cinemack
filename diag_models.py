import json, math, sqlite3, sys, urllib.request
from embed_layout import clean_keywords

Q = sys.argv[1]
FILMS = [("Parasite", "2019"), ("The Family That Preys", "2008"),
         ("The Family", "2013"), ("Family for Rent", "2015")]
MODELS = [("mxbai-embed-large", "Represent this sentence for searching relevant passages: "),
          ("bge-m3", "")]

def embed(model, text):
    req = urllib.request.Request("http://localhost:11434/api/embed",
        data=json.dumps({"model": model, "input": text}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["embeddings"][0]

def dist(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    return 1 - dot / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

def relayout(full, plot):
    lines = full.split("\n")
    kw = next((l[len("Keywords:"):] for l in lines if l.startswith("Keywords:")), "")
    rest = [l for l in lines[1:] if not l.startswith(("Plot:", "Keywords:", "Rating:"))]
    out = [lines[0]] + ([f"Plot: {plot}"] if plot else [])
    ck = clean_keywords(kw)
    return "\n".join(out + ([f"Keywords: {ck}"] if ck else []) + rest)

conn = sqlite3.connect("movies.db")
docs = {}
for t, y in FILMS:
    full, plot = conn.execute("select embed_text, overview from movies where title=? "
                              "and release_year like ? order by vote_count desc limit 1",
                              (t, y + "%")).fetchone()
    docs[f"{t} ({y})"] = {"now": full, "new layout": relayout(full, plot)}

print(f"\nQ: {Q}\n")
for model, prefix in MODELS:
    q = embed(model, prefix + Q)
    for layout in ("now", "new layout"):
        scored = sorted((dist(q, embed(model, d[layout])), name) for name, d in docs.items())
        rank = [n for _, n in scored].index("Parasite (2019)") + 1
        print(f"  {model:18s} {layout:11s}  Parasite rank {rank}/4")
        for s, n in scored:
            print(f"      {s:.4f}  {n}")
    print()
