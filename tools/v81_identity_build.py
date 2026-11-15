"""v8.1 slice 1.1: build media_identity / media_external_ids in the v8.1 COPY of facts.db and check them.
    python tools/v81_identity_build.py [--db facts.db] [--drop]
Refuses any database inside the live folder ~/movie-buff/rag. Rule (written before the run), all must hold:
  R1 identities = films + shows, media ids unique      R2 every identity has exactly one tmdb id and it round-trips
  R3 no film and show share a media id                 R4 films / tv_shows row counts and contents untouched
  R5 a trusted IMDb lookup never returns two titles
Compared with the round-42 audit (reported as MATCH / DIFFERS, not part of the rule because the audit's own
definition of a 'TV title' may differ): films with a TV IMDb id 519, stale IMDb ids 22, shared film-show ids 8."""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
import media_identity as M  # noqa: E402


def digest(conn, table):
    h = hashlib.md5()
    cols = "tmdb_id, imdb_id, wikidata_id"
    for r in conn.execute("select %s from %s order by tmdb_id" % (cols, table)):
        h.update(repr(r).encode())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="facts.db")
    ap.add_argument("--drop", action="store_true")
    a = ap.parse_args()
    db = os.path.realpath(a.db)
    live = os.path.realpath(os.path.expanduser("~/movie-buff/rag")) + os.sep
    if db.startswith(live):
        print("REFUSED: %s is inside the live folder" % db)
        return 2
    conn = sqlite3.connect(db, timeout=60)
    if a.drop:
        M.drop(conn)
        print("dropped media_identity and media_external_ids")
        return 0
    t = time.time()
    before = {x: (conn.execute("select count(*) from %s" % x).fetchone()[0], digest(conn, x)) for x in ("films", "tv_shows")}
    c = M.build(conn)
    after = {x: (conn.execute("select count(*) from %s" % x).fetchone()[0], digest(conn, x)) for x in ("films", "tv_shows")}
    print("Identity build (v8.1 slice 1.1) on %s, version %s" % (db, c["version"]))
    print("  films %s | shows %s | identities %s | outside ids %s | %.0f s" % (
        format(c["films"], ","), format(c["shows"], ","), format(c["identities"], ","), format(c["external_ids"], ","), time.time() - t))
    print("  outside ids by source: " + ", ".join("%s %s" % (s, format(n, ",")) for s, n in conn.execute(
        "select source, count(*) from media_external_ids group by 1 order by 2 desc")))
    for mt in M.TYPES:
        q = dict(conn.execute("select e.quality, count(*) from media_external_ids e join media_identity i using (media_id) "
                              "where e.source='imdb' and i.media_type=? group by 1", (mt,)).fetchall())
        print("  IMDb id quality, %s: %s" % (mt, ", ".join("%s %s" % (k, format(v, ",")) for k, v in sorted(q.items())) or "none"))
    uniq = conn.execute("select count(distinct media_id) from media_identity").fetchone()[0]
    r1 = c["identities"] == c["films"] + c["shows"] == uniq
    one = conn.execute("select count(*) from media_identity i where (select count(*) from media_external_ids e "
                       "where e.media_id=i.media_id and e.source='tmdb' and e.external_id=cast(i.tmdb_id as text))<>1").fetchone()[0]
    bad_rt = 0
    for mid, mt, tid in conn.execute("select media_id, media_type, tmdb_id from media_identity"):
        if M.parse(mid) != (mt, tid) or M.fmt(mt, tid) != mid:
            bad_rt += 1
    r2 = one == 0 and bad_rt == 0
    r3 = conn.execute("select count(*) from (select media_id from media_identity group by media_id having count(distinct media_type)>1)").fetchone()[0] == 0
    r4 = before == after
    multi = 0
    for (x,) in conn.execute("select external_id from media_external_ids where source='imdb' group by 1 having count(*)>1"):
        if len(M.resolve(conn, "imdb", x)) > 1:
            multi += 1
    r5 = multi == 0
    for name, ok, why in (("R1 identities = films + shows, unique", r1, ""), ("R2 one tmdb id each, round-trips", r2, "%d / %d wrong" % (one, bad_rt)),
                          ("R3 no film-show media id clash", r3, ""), ("R4 films / tv_shows untouched", r4, ""),
                          ("R5 trusted IMDb lookup is single-valued", r5, "%d ids return two titles" % multi)):
        print("  %-42s %s%s" % (name, "PASS" if ok else "FAIL", "" if ok or not why else " (" + why + ")"))
    print("  RULE (R1-R5): %s" % ("PASS" if all((r1, r2, r3, r4, r5)) else "FAIL"))
    fq = dict(conn.execute("select e.quality, count(*) from media_external_ids e where e.source='imdb' and e.media_id like 'movie:%' group by 1").fetchall())
    for label, got, audit in (("films whose IMDb id is a TV title", fq.get("wrong_type", 0), 519),
                              ("films with a stale IMDb id", fq.get("stale", 0), 22),
                              ("IMDb ids shared by a film and a show", c["imdb_shared_ids"], 8)):
        print("  audit check: %-38s %s vs audit %s -> %s" % (label, format(got, ","), format(audit, ","), "MATCH" if got == audit else "DIFFERS"))
    print("  (a shared id is labelled shared only when IMDb's own type does not say which title it belongs to)")
    return 0 if all((r1, r2, r3, r4, r5)) else 1


if __name__ == "__main__":
    sys.exit(main())
