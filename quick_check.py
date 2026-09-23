"""Retrieval-only check of every auto-graded test question. No answers generated."""
import json, time, unicodedata
from retrieve import retrieve

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s.casefold() if c.isalnum() and not unicodedata.combining(c))

d = json.load(open("test_set.json")); qs = d if isinstance(d, list) else d["questions"]
passed = total = 0; t0 = time.time()
for q in qs:
    if not q.get("expect_retrieved") or len(q["turns"]) != 1:
        continue
    total += 1
    hits = retrieve(q["turns"][0], k=3)
    got = [(norm(h["title"]), str(h.get("release_year") or "")[:4]) for h in hits]
    ok = all(any(t == norm(e) and (not q.get("expect_year") or y == q["expect_year"]) for t, y in got)
             for e in q["expect_retrieved"])
    passed += ok
    print(f"{'PASS' if ok else 'FAIL'}  {q['id']:4s} {q['category']:18s} "
          f"{', '.join(h['title'] for h in hits)[:70]}")
print(f"\n{passed}/{total} retrieval   ({time.time() - t0:.0f}s)")
