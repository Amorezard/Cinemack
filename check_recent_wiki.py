"""Read-only check of the Wikipedia pages linked to recent films (29 Oct). Network: Wikipedia + Wikidata APIs only."""
import random, sqlite3, time, urllib.parse
from collections import Counter, defaultdict
import add_recent as R

c = sqlite3.connect("file:facts.db?mode=ro", uri=True)
rows = c.execute("""select r.tmdb_id, r.pageid, r.title, r.method, r.status, f.title, f.year, f.wikidata_id, f.vote_count
                    from recent_wiki r join films f using(tmdb_id)""").fetchall()
owner = {}
for t, q in c.execute("select tmdb_id, wikidata_id from films where wikidata_id like 'Q%'"):
    owner.setdefault(q, t)
linked = [r for r in rows if r[1]]
pids = sorted({r[1] for r in linked})
item = {}
for s in range(0, len(pids), 50):
    d = R._get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "pageids": "|".join(map(str, pids[s:s + 50])),
         "format": "json", "formatversion": 2}))
    for p in ((d or {}).get("query") or {}).get("pages") or []:
        item[p.get("pageid")] = (p.get("pageprops") or {}).get("wikibase_item")
    time.sleep(0.5)
cls, bad, by_page = Counter(), [], defaultdict(list)
for fid, pid, wt, method, status, title, year, qid, votes in linked:
    by_page[pid].append(fid)
    it = item.get(pid)
    if not it:
        k = "page has no Wikidata item"
    elif qid and it == qid:
        k = "right (page item = film's id)"
    elif owner.get(it) not in (None, fid):
        k = "WRONG: page belongs to another film"
    elif qid:
        k = "MISMATCH: film has a different Wikidata id"
    else:
        k = "unverified (film has no Wikidata id)"
    cls[(method, k)] += 1
    if k.startswith(("WRONG", "MISMATCH")):
        bad.append((fid, title, year, votes, method, wt, it, owner.get(it), qid))
print(f"recent_wiki: {len(rows):,} rows, {len(linked):,} with a page; page items fetched: {len(item):,}")
for (m, k), n in sorted(cls.items()):
    print(f"  {m:9} {k}: {n:,}")
print("pages linked to more than one film:", sum(1 for v in by_page.values() if len(v) > 1))
for fid, title, year, votes, m, wt, it, own, qid in bad[:40]:
    print(f"  {fid} {title} ({year}, {votes} votes, {m}) -> '{wt}' item {it} (owner {own}; film's own id {qid or '-'})")
miss = [r for r in rows if not r[1] and r[3] == "wikidata"]
print(f"\nfilms with a Wikidata sitelink but no page stored: {len(miss):,}; dry run on 25 (Nolan's Odyssey included):")
random.seed(1)
samp = [r for r in miss if r[0] == 1368337] + random.sample([r for r in miss if r[0] != 1368337], min(24, len(miss) - 1))
titles = R.enwiki_titles([r[7] for r in samp if r[7]])
why = Counter()
for fid, pid, wt, method, status, title, year, qid, votes in samp:
    t = titles.get(qid)
    if not t:
        k = "no enwiki sitelink now"
    else:
        p = R.page_extract(t)
        k = ("page fetch failed" if not p else "lead fails the film/year test"
             if not R.looks_like_the_film(p["extract"], year or 0) else "would be OK now")
    why[k] += 1
    if fid == 1368337 or k != "would be OK now":
        print(f"  {fid} {title} ({year}): {k}" + (f" ['{t}']" if t else ""))
    time.sleep(0.3)
for k, n in why.most_common():
    print(f"  {k}: {n}")
