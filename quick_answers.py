"""Full answers for a handful of questions - for reading answer quality."""
import sys, time, ollama
from chat import build_prompt
QS = sys.argv[1:] or [
    "A movie about a poor family who each get hired by a wealthy household while hiding that they're related?",
    "a film about a giant shark attacking swimmers at a beach resort town",
    "Recommend me some science fiction movies from the 1980s.",
    "Who directed Mulholland Dr.?",
    "Is Fight Club worth watching?",
]
for q in QS:
    t0 = time.time()
    prompt, hits = build_prompt(q)
    r = ollama.chat(model="movie-buff", think=False, messages=[{"role": "user", "content": prompt}])
    print(f"\n=== {q}\n[context: {', '.join(h['title'] for h in hits)}]  ({time.time() - t0:.1f}s)")
    print(r["message"]["content"].strip())
