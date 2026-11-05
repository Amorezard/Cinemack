"""Count reception summaries that write a Metacritic score as a percentage, by summary version (29 Oct). Read-only."""
import re, sqlite3, sys
from collections import Counter
db = sqlite3.connect("file:facts.db?mode=ro", uri=True)
lvl = sys.argv[1] if len(sys.argv) > 1 else "3"
R1 = re.compile(r"\d+(?:\.\d+)?\s*%[^.;,]{0,60}?\b(?:on|from|at|by)\s+Metacritic", re.I)
R2 = re.compile(r"\bMetacritic\b(?:'s)?[^.;,%]{0,30}?\d+(?:\.\d+)?\s*%", re.I)
SKIP = re.compile(r"Rotten|Tomatometer|\band\b|\bwhile\b", re.I)
n, bad, ex = Counter(), Counter(), {}
for fid, text, key in db.execute("select tmdb_id, text, key from summaries where kind = 'reception' and level = ?", (lvl,)):
    v = key.rsplit("|", 1)[-1]
    n[v] += 1
    for s in re.split(r"(?<=[.!?])\s+", text or ""):
        if "metacritic" in s.lower() and any(not SKIP.search(m.group(0)) for m in list(R1.finditer(s)) + list(R2.finditer(s))):
            bad[v] += 1
            ex.setdefault(v, []).append((fid, s[:170]))
            break
for v in sorted(n):
    print(f"level-{lvl} summaries version {v}: {n[v]:,}; Metacritic score written as a percentage: {bad[v]:,} "
          f"({bad[v] / max(1, n[v]):.1%})")
    for fid, s in ex.get(v, [])[:4]:
        print(f"  film {fid}: {s}")
