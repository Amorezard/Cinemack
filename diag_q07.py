import json, math, sqlite3, sys, urllib.request
from retrieve import QUERY_PREFIX

MODEL = "mxbai-embed-large"

TONE = {"hilarious","baffled","callous","dubious","farcical","mean spirited","straightforward",
        "urgent","critical","egotistical","complex","intense","desperate","satirical",
        "admiring","amused","angry","anxious","awestruck","bold","cautionary","compassionate",
        "defiant","dramatic","earnest","enchant","excited","exhilarated","frustrated","hopeful",
        "horrified","inspirational","melancholy","nostalgic","playful","provocative","romantic",
        "sad","suspenseful","sympathetic","tense","thoughtful","witty","absurd","aggressive",
        "ambiguous","cheerful","clinical","comforting","depressing","familiar","grim","harsh",
        "informative","joyful","loving","powerful","reflective","sincere","vibrant","whimsical"}

def embed(text):
    req = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=json.dumps({"model": MODEL, "input": text}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["embeddings"][0]

def dist(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    return 1 - dot / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

question = sys.argv[1]
title = sys.argv[2] if len(sys.argv) > 2 else "Parasite"

conn = sqlite3.connect("movies.db")
t, year, plot, full = conn.execute(
    "select title, release_year, overview, embed_text from movies "
    "where title = ? order by vote_count desc limit 1", (title,)).fetchone()

lines = full.split("\n")
head = lines[0]
kw_line = next((l for l in lines if l.startswith("Keywords:")), "Keywords:")
kws = [k.strip() for k in kw_line[len("Keywords:"):].split(",") if k.strip()]
clean_line = "Keywords: " + ", ".join(dict.fromkeys(k for k in kws if k.lower() not in TONE))

def without(*prefixes):
    return [l for l in lines[1:] if not l.startswith(prefixes)]

variants = {
    "stored (as is)":            full,
    "plot moved to 2nd line":    "\n".join([head, f"Plot: {plot}"] + without("Plot:")),
    "tone keywords removed":     "\n".join(clean_line if l.startswith("Keywords:") else l for l in lines),
    "plot 2nd + clean keywords": "\n".join([head, f"Plot: {plot}", clean_line] + without("Plot:", "Keywords:")),
    "no cast, no keywords":      "\n".join([head] + without("Starring:", "Keywords:")),
    "title + plot only":         f"{head}\nPlot: {plot}",
}

q = embed(QUERY_PREFIX + question)
print(f"\nQ: {question}\n   target: {t} ({year})\n")
for name, text in variants.items():
    print(f"  {dist(q, embed(text)):.4f}   {name:26s} ({len(text)} chars)")
print("\n  lower = closer\n")
