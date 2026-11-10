import json, sys
sys.path.insert(0, sys.argv[1])
import pref_phrases as P
d = json.load(open(sys.argv[2]))
real = traps = caught = false = 0
for q in d["questions"]:
    got = P.detect(q["text"]); exp = q["expect"]
    if exp:
        real += 1; norm = lambda d: {k: sorted(v) if isinstance(v, list) else v for k, v in d.items()}
        ok = norm(got) == norm(exp); caught += ok
    else:
        traps += 1; ok = not got; false += (not ok)
    if not ok: print("MISS" if exp else "FALSE", q["id"], q["text"], "expected", exp, "got", got)
print(f"real caught {caught}/{real}, traps triggered {false}/{traps}  ->  rule {'PASS' if caught == real and false <= 1 else 'FAIL'}")
