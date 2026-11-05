"""Repair recent films linked to the wrong Wikipedia page (29 Oct).

    python fix_recent_wiki.py plan     read-only: what apply would change
    python fix_recent_wiki.py apply    changes wiki.db + facts.db (back both up first)

The 14 films were found by check_recent_wiki.py: all were matched by title search, and the page's Wikidata item
belongs to another of our films (5, winner given) or differs from the film's own Wikidata id (9, no winner).
After apply: add_recent.py passages, wiki_embed, wiki_enrich, wiki_sections embed, mb.py build-cache,
v7_data.py reception, critics, money_extra.py critics, reception_cards build + summaries, evidence.py build.
"""
import sqlite3, sys
import add_recent as R
import facts_db as F

# wrong film -> film the page belongs to (None: the page belongs to a film we don't have)
LOSERS = {1698863: 1368337, 1001089: 616446, 1188150: 975511, 1208808: 829038, 931628: 1284186,
          914206: None, 981063: None, 1142518: None, 1167366: None, 1175038: None, 1261924: None,
          1309396: None, 1623256: None, 1662317: None}


def has(conn, t):
    return bool(conn.execute("select 1 from sqlite_master where name = ?", (t,)).fetchone())


def one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else None


def main(apply):
    c = R.fconn()
    c.execute("pragma busy_timeout = 600000")
    w = R._vec_conn(R.WIKI) if apply else sqlite3.connect(f"file:{R.WIKI}?mode=ro", uri=True)
    w.execute("pragma busy_timeout = 600000")
    for loser, winner in LOSERS.items():
        row = c.execute("select pageid, title, url, method, status, extract from recent_wiki where tmdb_id = ?",
                        (loser,)).fetchone()
        name = one(c, "select title || ' (' || year || ')' from films where tmdb_id = ?", (loser,))
        if not row or not row[0]:
            print(f"{loser} {name}: no page in recent_wiki (status {row[4] if row else '-'}) - already done?")
            continue
        pid, wtitle, url, method, status, extract = row
        did = f"live:{pid}"
        holder = one(w, "select film_id from links where doc_id = ?", (did,))
        n_ch = one(w, "select count(*) from chunks where doc_id = ?", (did,))
        n_q = one(w, "select count(*) from chunks where kind = 'question' and film_id = ?", (loser,))
        n_sec = one(c, "select count(*) from sections where doc_id = ? and tmdb_id = ?", (did, loser)) if holder != winner or winner is None else 0
        n_rec = one(c, "select count(*) from wiki_reception where pageid = ? and tmdb_id = ?", (pid, loser)) if has(c, "wiki_reception") else 0
        n_cs = one(c, "select count(*) from critic_statements where pageid = ? and tmdb_id = ?", (pid, loser)) if has(c, "critic_statements") else 0
        n_sc = one(c, "select count(*) from critic_scores where tmdb_id = ? and url in (?, ?)",
                   (loser, url, f"https://en.wikipedia.org/?curid={pid}"))
        wrow = c.execute("select pageid, status from recent_wiki where tmdb_id = ?", (winner,)).fetchone() if winner else None
        wname = one(c, "select title || ' (' || year || ')' from films where tmdb_id = ?", (winner,)) if winner else None
        drop_doc = holder is None or holder == loser
        print(f"{loser} {name} -> '{wtitle}' ({did}); wiki.db holder {holder}; "
              f"{'REMOVE page rows (' + str(n_ch) + ' chunks)' if drop_doc else 'keep page rows (held by the right film)'}; "
              f"loser questions {n_q}, sections {n_sec}, reception rows {n_rec}, critic statements {n_cs}, critic score rows {n_sc}"
              + (f"; page goes to {winner} {wname} (its recent_wiki: page {wrow[0] if wrow else '-'}, {wrow[1] if wrow else '-'})"
                 if winner else "; no right film in our data"))
        if not apply:
            continue
        if drop_doc:
            cids = [r[0] for r in w.execute("select chunk_id from chunks where doc_id = ?", (did,))]
            for s in range(0, len(cids), 500):
                part = cids[s:s + 500]
                q = ",".join("?" * len(part))
                w.execute(f"delete from vec_chunks where chunk_id in ({q})", part)
                if has(w, "chunk_ctx"):
                    w.execute(f"delete from chunk_ctx where chunk_id in ({q})", part)
            w.execute("delete from chunks where doc_id = ?", (did,))
            w.execute("delete from pages where doc_id = ?", (did,))
            w.execute("delete from links where doc_id = ?", (did,))
        qids = [r[0] for r in w.execute("select chunk_id from chunks where kind = 'question' and film_id = ?", (loser,))]
        if qids:
            q = ",".join("?" * len(qids))
            w.execute(f"delete from vec_chunks where chunk_id in ({q})", qids)
            w.execute("delete from chunks where kind = 'question' and film_id = ?", (loser,))
        for sql in ("delete from gen_questions where film_id = ?", "delete from wiki_fts where rowid = ?",
                    "delete from links where film_id = ?"):
            try:
                w.execute(sql, (loser,))
            except sqlite3.OperationalError:
                pass
        if drop_doc or winner is None:
            secs = [r[0] for r in c.execute("select sec_id from sections where doc_id = ?", (did,))]
            if secs and has(c, "sec_vec"):
                c.executemany("delete from sec_vec where sec_id = ?", [(x,) for x in secs])
            c.execute("delete from sections where doc_id = ?", (did,))
        if has(c, "wiki_reception"):
            c.execute("delete from wiki_reception where pageid = ? and tmdb_id = ?", (pid, loser))
            c.execute("delete from wiki_reception_done where pageid = ? and tmdb_id = ?", (pid, loser))
        if n_cs:
            c.execute("delete from critic_statements where pageid = ? and tmdb_id = ?", (pid, loser))
            c.execute("delete from critic_statements_done where pageid = ?", (pid,))
        c.execute("delete from critic_scores where tmdb_id = ? and url in (?, ?)",
                  (loser, url, f"https://en.wikipedia.org/?curid={pid}"))
        c.execute("delete from reception_card where tmdb_id = ?", (loser,))
        c.execute("delete from summaries where tmdb_id = ? and kind = 'reception'", (loser,))
        c.execute("update recent_wiki set pageid = null, title = null, url = null, extract = null, status = 'wrong', "
                  "fetched_at = ? where tmdb_id = ?", (F.now(), loser))
        c.execute("update recent_added set has_wiki = 0 where tmdb_id = ?", (loser,))
        if winner and (not wrow or not wrow[0]):
            c.execute("insert or replace into recent_wiki values (?,?,?,?,?,?,?,?)",
                      (winner, pid, wtitle, url, "wikidata", extract, F.now(), "ok"))
            c.execute("update recent_added set has_wiki = 1 where tmdb_id = ?", (winner,))
        if winner:
            c.execute("delete from reception_card where tmdb_id = ?", (winner,))
        w.commit()
        c.commit()
    print("applied" if apply else "plan only: nothing changed")


def retry():
    """Films whose Wikidata item has an English Wikipedia page but no page stored (the 15 Oct fetch hit 429s).
    add_recent.py wiki skips them because they are in movies.db now. Same film/year test; a page already
    linked to another recent film is skipped and listed."""
    import time
    from collections import Counter
    c = R.fconn()
    c.execute("pragma busy_timeout = 600000")
    rows = c.execute("""select r.tmdb_id, f.title, f.year, f.wikidata_id from recent_wiki r join films f using(tmdb_id)
                        where r.status = 'none' and r.method = 'wikidata' and f.wikidata_id like 'Q%'""").fetchall()
    print(f"films with a Wikidata sitelink but no page stored: {len(rows):,}", flush=True)
    titles = R.enwiki_titles([r[3] for r in rows])
    held = {r[0]: r[1] for r in c.execute("select pageid, tmdb_id from recent_wiki where pageid is not null")}
    st, clash = Counter(), []
    for i, (fid, title, year, qid) in enumerate(rows, 1):
        t = titles.get(qid)
        p = R.page_extract(t) if t else None
        if not t:
            k = "no enwiki sitelink"
        elif not p:
            k = "fetch failed"
        elif not R.looks_like_the_film(p["extract"], year or 0):
            k = "lead fails the film/year test"
        elif p["pageid"] in held:
            k = "page already linked to another film"
            clash.append((fid, title, year, p["title"], held[p["pageid"]]))
        else:
            k = "ok"
            held[p["pageid"]] = fid
            c.execute("insert or replace into recent_wiki values (?,?,?,?,?,?,?,?)",
                      (fid, p["pageid"], p["title"], p["url"], "wikidata", p["extract"], F.now(), "ok"))
            c.execute("update recent_added set has_wiki = 1 where tmdb_id = ?", (fid,))
        st[k] += 1
        if i % 25 == 0:
            c.commit()
            print(f"  {i}/{len(rows)} {dict(st)}", flush=True)
        time.sleep(1.0)
    c.commit()
    print("retry:", dict(st))
    for fid, title, year, wt, other in clash:
        print(f"  clash: {fid} {title} ({year}) -> '{wt}' already linked to {other}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("plan", "apply", "retry"):
        sys.exit(__doc__)
    retry() if sys.argv[1] == "retry" else main(sys.argv[1] == "apply")
