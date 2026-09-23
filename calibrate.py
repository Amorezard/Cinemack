"""You grade answers blind, then see the judge's verdict. Reports agreement."""
import json, random, sys
paths = sys.argv[1:] or ["results/judge-style-A.json", "results/judge-style-A2.json",
                         "results/judge-style-C.json", "results/judge-typed.json"]
rows = [r for p in paths for r in json.load(open(p)) if r.get("judge")]
random.seed(1); random.shuffle(rows)
agree = n = 0; out = []
for r in rows:
    print("\n" + "=" * 72)
    print(f"{r['id']}  {r['question']}\nNOTES: {r['notes']}\n\nCONTEXT:\n{r['context'][:1200]}\n\nANSWER:\n{r['answer']}")
    a = input("\nYour verdict  [p]ass / [f]ail / [s]kip / [q]uit: ").strip().lower()
    if a == "q": break
    if a not in ("p", "f"): continue
    mine, theirs = ("pass" if a == "p" else "fail"), r["judge"].get("verdict")
    n += 1; agree += mine == theirs
    out.append({"id": r["id"], "human": mine, "judge": theirs, "reason": r["judge"].get("reason")})
    print(f"  judge: {theirs}  -> {'agree' if mine == theirs else 'DISAGREE'}   ({r['judge'].get('reason')})")
print(f"\nagreement {agree}/{n} = {100 * agree / max(n, 1):.0f}%   (75%+ = trust the judge)")
json.dump(out, open("results/calibration.json", "w"), indent=2)
