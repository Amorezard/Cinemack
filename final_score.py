#!/usr/bin/env python3
"""v8 sealed run: scores that do NOT depend on the judge (9 Nov audit: gemma3 passes fluent answers about the wrong
same-name title, and "That sounds like X" for invented films). Read with the judge's numbers, not instead of them.
  sn   expected title named in the first two sentences; and with the expected year there too (or no year at all)
  inv  invented: answers that PRESENT a film as the one described (fewer is better);
       real_vague: expected film named in the first two sentences
    python final_score.py            # every results/v3eval_sealed8_{sn,inv}_*.json -> results/final_v8_scores.txt
"""
import glob
import json
import re

from ident_score import named, presents, sentences


def load_set(p):
    d = json.load(open(p))
    return {q["id"]: q for q in d["questions"]}


def year_ok(ans, y):
    first = sentences(ans)
    years = re.findall(r"\b(19\d\d|20\d\d)\b", first)
    return not years or (y and str(y) in years)


def main():
    out = []
    sn, inv = load_set("sets/sealed_v8_sn.json"), load_set("sets/sealed_v8_inv.json")
    for p in sorted(glob.glob("results/v3eval_sealed8_sn_*.json")):
        rows = {r["id"]: r for r in json.load(open(p))["rows"]}
        nm = [i for i, q in sn.items() if i in rows and named(rows[i].get("answer"), q["expect_films"][0])]
        ny = [i for i in nm if year_ok(rows[i].get("answer"), sn[i].get("expect_year"))]
        out.append(f"sn  {p.split('sealed8_sn_')[1][:-5]:22s} named first {len(nm):2d}/{len(sn)}   "
                   f"named + right year {len(ny):2d}/{len(sn)}   missed: {sorted(set(sn) - set(ny))}")
    for p in sorted(glob.glob("results/v3eval_sealed8_inv_*.json")):
        rows = {r["id"]: r for r in json.load(open(p))["rows"]}
        invented = [i for i, q in inv.items() if q["category"] == "invented"]
        real = [i for i, q in inv.items() if q["category"] == "real_vague"]
        pres = [i for i in invented if i in rows and presents(rows[i].get("answer"))]
        got = [i for i in real if i in rows and named(rows[i].get("answer"), inv[i]["expect_retrieved"][0])]
        out.append(f"inv {p.split('sealed8_inv_')[1][:-5]:22s} invented presenting a film {len(pres):2d}/{len(invented)} {pres}   "
                   f"real vague named first {len(got):2d}/{len(real)}")
    open("results/final_v8_scores.txt", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
