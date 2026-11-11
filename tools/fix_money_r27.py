#!/usr/bin/env python3
"""Round 27 (11 Nov 2026): re-read infobox money written with a decimal comma.

Skiptrace's infobox says "$136,6 million"; wiki_sections.parse_money read it as $1,366 million, and the money table
(which prefers the infobox for pre-2022 films) said the film grossed $1.37 billion. parse_money now treats
"<1-3 digits>,<1-2 digits> million/billion" as a decimal. This script fixes the rows already in facts.db without
re-reading every Wikipedia page:

    python tools/fix_money_r27.py            # list what would change (nothing written)
    python tools/fix_money_r27.py --apply    # update those rows; the old values go to archive/pre_r27_money_rows.json

Afterwards rebuild the derived tables: python money_extra.py convert && python money_extra.py money &&
python money_extra.py real (todo.sh does this).
"""
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
from wiki_sections import parse_money  # noqa: E402

R_COMMA = re.compile(r"\d{1,3},\d{1,2}\s*(?:million|billion|crore|lakh|m\b|bn\b)", re.I)


def main():
    apply = "--apply" in sys.argv
    c = sqlite3.connect("facts.db" if apply else "file:facts.db?mode=ro", uri=not apply)
    rows = c.execute("select rowid, tmdb_id, field, value_num, currency, value_text from facts "
                     "where source = 'wikipedia_infobox' and field in ('budget', 'box_office') "
                     "and value_text like '%,%'").fetchall()
    change = []
    for rid, fid, field, v, cur, vt in rows:
        if not R_COMMA.search(re.sub(r"<ref.*", "", vt or "")):
            continue
        p = parse_money(vt)
        if p and v and abs(p[0] / v - 1) > 0.01:
            change.append({"rowid": rid, "tmdb_id": fid, "field": field, "old": v, "new": p[0], "currency": cur,
                           "text": (vt or "")[:80]})
    titles = {}
    for x in change:
        r = c.execute("select title, year from films where tmdb_id = ?", (x["tmdb_id"],)).fetchone()
        titles[x["tmdb_id"]] = f"{r[0]} ({r[1]})" if r else x["tmdb_id"]
    print(f"infobox rows with a decimal comma read wrongly: {len(change)}")
    for x in change[:40]:
        print(f"  {titles.get(x['tmdb_id'], x['tmdb_id'])} | {x['field']} | {x['old']:,.0f} -> {x['new']:,.0f} {x['currency']}"
              f" | {x['text']!r}")
    if apply and change:
        os.makedirs("archive", exist_ok=True)
        json.dump(change, open("archive/pre_r27_money_rows.json", "w"), indent=1)
        c.executemany("update facts set value_num = ? where rowid = ?", [(x["new"], x["rowid"]) for x in change])
        c.commit()
        print(f"updated {len(change)} rows (old values in archive/pre_r27_money_rows.json)")


if __name__ == "__main__":
    main()
