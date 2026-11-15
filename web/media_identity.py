"""v8.1 slice 1.1: one identity per film / show, additive.

Two NEW tables in facts.db (nothing existing is changed, no existing id is renamed):
  media_identity(media_id primary key, media_type, tmdb_id, title, original_title, year, release_date, flags, built_at, version)
  media_external_ids(media_id, source, external_id, quality, note)   primary key (media_id, source)
media_id is "movie:<tmdb number>" or "tv:<tmdb number>": the round-42 audit found 1,072 TMDB numbers used by both a
film and a show, so a bare number is not an identity. quality says how far an outside id can be trusted:
  ok | wrong_type (IMDb lists it as the other kind of title) | stale (not in IMDb's title list) | shared (a film and a
  show carry the same IMDb id and IMDb's own type does not settle it) | year_gap (IMDb year differs from ours by more than 1)
Nothing imports this module yet. Switch MEDIA_IDENTITY (off by default) is reserved for the first reader.
Rollback: drop(conn) removes the two tables."""
import json
import os
import re
import time

VERSION = "v81.1"
ENABLED = os.environ.get("MEDIA_IDENTITY", "0") == "1"
TYPES = ("movie", "tv")
IMDB_TV = ("tvSeries", "tvMiniSeries", "tvEpisode", "tvSpecial", "tvShort", "tvPilot")
IMDB_FILM = ("movie", "tvMovie", "video", "short")
_ID = re.compile(r"^(movie|tv):([1-9][0-9]{0,9})$")


def fmt(media_type, tmdb_id):
    if media_type not in TYPES:
        raise ValueError("media type must be movie or tv")
    n = int(tmdb_id)
    if n <= 0:
        raise ValueError("tmdb id must be positive")
    return "%s:%d" % (media_type, n)


def parse(media_id):
    """'movie:603' -> ('movie', 603). Anything else raises ValueError (a bare number is refused on purpose)."""
    m = _ID.match(media_id) if isinstance(media_id, str) else None
    if not m:
        raise ValueError("not a media id: %r" % (media_id,))
    return m.group(1), int(m.group(2))


def _has(conn, table):
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def _cols(conn, table):
    return {r[1] for r in conn.execute("pragma table_info(%s)" % table)}


def drop(conn):
    conn.execute("drop table if exists media_external_ids")
    conn.execute("drop table if exists media_identity")
    conn.commit()


def build(conn, now=None):
    """(Re)builds both tables from films / tv_shows / imdb_titles inside one transaction. Returns the counts."""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    imdb, have_imdb = {}, False
    if _has(conn, "imdb_titles"):
        have_imdb = conn.execute("select 1 from imdb_titles limit 1").fetchone() is not None
        for t in ("films", "tv_shows"):          # only the ids we hold: IMDb's list has millions of rows
            if _has(conn, t):
                imdb.update({r[0]: (r[1], r[2]) for r in conn.execute(
                    "select tconst, title_type, start_year from imdb_titles where tconst in "
                    "(select imdb_id from %s where imdb_id is not null)" % t)})
    ident, ext = [], []
    film_imdb, show_imdb = {}, {}

    def outside(mid, kind, row, year, wrong):
        iid = (row.get("imdb_id") or "").strip()
        flags = []
        ext.append((mid, "tmdb", str(row["tmdb_id"]), "ok", None))
        if iid:
            q, note = "ok", None
            if have_imdb:
                t = imdb.get(iid)
                if t is None:
                    q, note = "stale", "not in IMDb's title list"
                elif t[0] in wrong:
                    q, note = "wrong_type", "IMDb type %s" % t[0]
                elif year and t[1] and abs(int(t[1]) - int(year)) > 1:
                    q, note = "year_gap", "IMDb year %s, ours %s" % (t[1], year)
            if q != "ok":
                flags.append("imdb_" + q)
            ext.append([mid, "imdb", iid, q, note])
            (film_imdb if kind == "movie" else show_imdb).setdefault(iid, []).append(len(ext) - 1)
        else:
            flags.append("no_imdb")
        wid = (row.get("wikidata_id") or "").strip()
        if wid:
            ext.append((mid, "wikidata", wid, "ok", None))
        else:
            flags.append("no_wikidata")
        if row.get("tvdb_id"):
            ext.append((mid, "tvdb", str(row["tvdb_id"]), "ok", None))
        if row.get("wiki_doc_id"):
            ext.append((mid, "wiki_doc", str(row["wiki_doc_id"]), "ok", None))
        return flags

    n = {"movie": 0, "tv": 0}
    if _has(conn, "films"):
        have = _cols(conn, "films")
        want = [c for c in ("tmdb_id", "imdb_id", "wikidata_id", "wiki_doc_id", "title", "original_title",
                            "release_date", "year") if c in have]
        for r in conn.execute("select %s from films" % ", ".join(want)):
            row = dict(zip(want, r))
            mid = fmt("movie", row["tmdb_id"])
            flags = outside(mid, "movie", row, row.get("year"), IMDB_TV)
            if not row.get("release_date"):
                flags.append("no_release_date")
            ident.append([mid, "movie", row["tmdb_id"], row.get("title"), row.get("original_title"), row.get("year"),
                          row.get("release_date"), flags])
            n["movie"] += 1
    if _has(conn, "tv_shows"):
        have = _cols(conn, "tv_shows")
        want = [c for c in ("tmdb_id", "imdb_id", "tvdb_id", "wikidata_id", "name", "original_name", "first_air_date",
                            "first_year") if c in have]
        for r in conn.execute("select %s from tv_shows" % ", ".join(want)):
            row = dict(zip(want, r))
            mid = fmt("tv", row["tmdb_id"])
            flags = outside(mid, "tv", row, None, IMDB_FILM)     # a show's IMDb year is its first year; not compared
            ident.append([mid, "tv", row["tmdb_id"], row.get("name"), row.get("original_name"), row.get("first_year"),
                          row.get("first_air_date"), flags])
            n["tv"] += 1
    by_mid = {i[0]: i for i in ident}
    shared = 0
    for iid in set(film_imdb) & set(show_imdb):
        both = film_imdb[iid] + show_imdb[iid]
        for k in both:
            f = by_mid[ext[k][0]][7]
            if "imdb_shared" not in f:
                f.append("imdb_shared")
        ok = [k for k in both if ext[k][3] == "ok"]
        if len(ok) > 1:                  # IMDb's own type did not settle which of the two it belongs to
            for k in ok:
                ext[k][3], ext[k][4] = "shared", "IMDb id on a film and a show"
        shared += 1
    with conn:
        conn.execute("drop table if exists media_external_ids")
        conn.execute("drop table if exists media_identity")
        conn.execute("""create table media_identity(media_id text primary key, media_type text not null,
            tmdb_id integer not null, title text, original_title text, year integer, release_date text,
            flags text not null, built_at text not null, version text not null)""")
        conn.execute("""create table media_external_ids(media_id text not null, source text not null,
            external_id text not null, quality text not null, note text, primary key (media_id, source))""")
        conn.executemany("insert into media_identity values (?,?,?,?,?,?,?,?,?,?)",
                         [(i[0], i[1], i[2], i[3], i[4], i[5], i[6], json.dumps(sorted(i[7])), now, VERSION) for i in ident])
        conn.executemany("insert into media_external_ids values (?,?,?,?,?)", [tuple(e) for e in ext])
        conn.execute("create index media_external_lookup on media_external_ids(source, external_id)")
    q = dict(conn.execute("select quality, count(*) from media_external_ids where source='imdb' group by 1").fetchall())
    return {"films": n["movie"], "shows": n["tv"], "identities": len(ident), "external_ids": len(ext),
            "imdb_quality": q, "imdb_shared_ids": shared, "built_at": now, "version": VERSION}


def resolve(conn, source, external_id, media_type=None, trusted_only=True):
    """Outside id -> list of media ids. With trusted_only an IMDb id marked wrong_type / stale is not returned;
    a shared one is returned only when media_type says which of the two is meant."""
    rows = conn.execute("select e.media_id, e.quality, i.media_type from media_external_ids e "
                        "join media_identity i using (media_id) where e.source=? and e.external_id=?",
                        (source, str(external_id))).fetchall()
    out = []
    for mid, q, mt in rows:
        if media_type and mt != media_type:
            continue
        if trusted_only and q in ("wrong_type", "stale"):
            continue
        if trusted_only and q == "shared" and not media_type:
            continue
        out.append(mid)
    return sorted(out)


def external(conn, media_id):
    parse(media_id)
    return {s: {"id": x, "quality": q} for s, x, q in conn.execute(
        "select source, external_id, quality from media_external_ids where media_id=?", (media_id,))}
