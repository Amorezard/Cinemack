#!/usr/bin/env python3
"""v8.1 slice 6.1 (15 Nov 2026): what moves on the site, read from the stylesheet (no browser needed).

Reads web/v8/app.css and lists every transition / animation: its duration, whether the duration is one of the
kit's motion tokens (docs/UX_MOTION_SPEC.md: instant 100 ms, select 150, card 220, panel 300), which properties
it moves, and whether it loops.
Rule (written before the first run):
  1. the four tokens are defined with the kit's values
  2. one `prefers-reduced-motion: reduce` block shortens BOTH animations and transitions for every element
  3. no transition names a layout property (width, height, top, left, right, bottom, margin, padding) - the kit asks
     for opacity / transform where possible; `top` on the skip link is the one listed exception
Reported, not ruled on: durations written as plain numbers instead of tokens, and looping animations (loading
shimmer / spinners) - these are the list to convert in a later round, with screenshots.
Writes results/v81_motion_audit.txt and .json. Read-only on the stylesheet.
    python tools/v81_motion_audit.py [--css web/v8/app.css]
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = {"--motion-instant": 100, "--motion-select": 150, "--motion-card": 220, "--motion-panel": 300}
LAYOUT = ("width", "height", "top", "left", "right", "bottom", "margin", "padding")
ALLOWED_LAYOUT = {(".skip", "top")}
_TIME = re.compile(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b")


def ms(num, unit):
    return float(num) * (1000 if unit == "s" else 1)


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def rules(css):
    """(selector, declarations) for every innermost rule block."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", strip_comments(css)):
        sel = " ".join(m.group(1).split())
        if sel.startswith("@") or re.fullmatch(r"(from|to|\d+%)(\s*,\s*(from|to|\d+%))*", sel):
            continue
        out.append((sel, m.group(2)))
    return out


def audit(css):
    text = strip_comments(css)
    res = {"tokens": {}, "items": [], "reduced_motion": False, "layout": [], "literal": [], "loops": []}
    for name, want in TOKENS.items():
        m = re.search(re.escape(name) + r"\s*:\s*(\d*\.?\d+)(ms|s)\s*;", text)
        res["tokens"][name] = {"want_ms": want, "found_ms": ms(*m.groups()) if m else None}
    for m in re.finditer(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}", text):
        body = m.group(1)
        if re.search(r"(^|[\s,{])\*\s*(,|\{)", body) and "animation-duration" in body and "transition-duration" in body:
            res["reduced_motion"] = True
    for sel, decl in rules(css):
        for prop, val in re.findall(r"(?:^|;)\s*(transition|animation)\s*:\s*([^;]+)", decl):
            durs = [ms(*t) for t in _TIME.findall(val)]
            toks = re.findall(r"var\((--[\w-]+)\)", val)
            item = {"selector": sel[:80], "kind": prop, "value": " ".join(val.split())[:140], "literal_ms": durs, "tokens": toks}
            res["items"].append(item)
            if durs:
                res["literal"].append(item)
            if prop == "animation" and "infinite" in val:
                res["loops"].append(item)
            if prop == "transition":
                for part in val.split(","):
                    name = part.strip().split(" ")[0]
                    if any(name == p or name.startswith(p + "-") for p in LAYOUT) and not any(
                            s in sel and name == p for s, p in ALLOWED_LAYOUT):
                        res["layout"].append({"selector": sel[:80], "property": name})
    res["rule"] = {"1_tokens": all(v["found_ms"] == v["want_ms"] for v in res["tokens"].values()),
                   "2_reduced_motion": res["reduced_motion"], "3_no_layout_transitions": not res["layout"]}
    res["pass"] = all(res["rule"].values())
    return res


def report(res, path):
    L = [f"Motion audit (v8.1 slice 6.1) of {path}", "",
         "tokens: " + "; ".join(f"{k} {'%g ms' % v['found_ms'] if v['found_ms'] is not None else 'MISSING'} (kit {v['want_ms']} ms)"
                                for k, v in res["tokens"].items()),
         f"transitions / animations found: {len(res['items'])} "
         f"({sum(1 for i in res['items'] if i['tokens'] and not i['literal_ms'])} use only tokens, "
         f"{len(res['literal'])} carry a plain number, {len(res['loops'])} loop)",
         f"reduced-motion block covering animations and transitions for every element: {'yes' if res['reduced_motion'] else 'NO'}",
         f"transitions on a layout property: {len(res['layout'])}"]
    L += [f"    {x['selector']}: {x['property']}" for x in res["layout"]]
    L += ["", "plain-number durations (to convert to tokens later, with screenshots):"]
    L += [f"    {i['selector']}: {i['kind']} {i['value']}" for i in res["literal"]] or ["    none"]
    L += ["", "RULE 1 tokens %s | 2 reduced motion %s | 3 no layout transitions %s -> %s" % (
        *("ok" if res["rule"][k] else "FAIL" for k in ("1_tokens", "2_reduced_motion", "3_no_layout_transitions")),
        "PASS" if res["pass"] else "FAIL")]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--css", default=os.path.join(HERE, "web", "v8", "app.css"))
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v81_motion_audit"))
    a = ap.parse_args(argv)
    res = audit(open(a.css, encoding="utf-8").read())
    text = report(res, os.path.relpath(a.css, HERE))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out + ".txt", "w") as fh:
        fh.write(text)
    with open(a.out + ".json", "w") as fh:
        json.dump(res, fh, indent=1)
    print(text, end="")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
