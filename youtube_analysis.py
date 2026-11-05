#!/usr/bin/env python3
"""
R11 (v7, 27 Oct): what film-analysis and review channels on YouTube say about a film - from an allow-list of
channels chosen by stated criteria, using only what the YouTube Data API allows: titles, descriptions and the
chapter list a channel writes into its description; since 4 Nov also like / comment counts and the top comments under
matched videos (shown and used as evidence; their sentiment only with SENT_YTC=1). No captions (owner-only), and views
or likes never rank anything. Stored text is refreshed or deleted within 30 days (YouTube API policy).

    export YOUTUBE_API_KEY=...
    python youtube_analysis.py channels          # resolve the allow-list (1 unit per channel)
    python youtube_analysis.py fetch             # every allow-listed channel's uploads + details (~2 units / 50 videos)
    python youtube_analysis.py match             # which film each video is about; chapters -> evidence units
    python youtube_analysis.py refresh           # re-fetch what is >= 25 days old; delete what is > 30 days old
    python youtube_analysis.py sample 50         # results/video_match_check.csv (rule: right film >= 90%)
    python youtube_analysis.py score results/video_match_check.csv
    python youtube_analysis.py stats
    python youtube_analysis.py restats          # 4 Nov: like / comment counts for stored videos (~1 unit / 50)
    python youtube_analysis.py comments         # 4 Nov: top comments under matched videos (1 unit / video)

Allow-list criteria (stated before the list was made; none of them is views or likes):
    1. most of the channel's videos analyse or review specific films (not news, trailers, recaps or reactions);
    2. the channel names the film it discusses in the video title;
    3. established: several years of uploads and a body of work cited by the film press or film schools;
    4. kind is recorded: "analysis" (video essays on craft: shots, editing, writing, sound) or "review"
       (a critic's verdict on a film). Answers call the first "analysts" and the second "YouTube critics".
A channel that fails a criterion later is removed by deleting it from ALLOW and running `refresh`.

Tables (facts.db): yt_channels, yt_analysis (one row per video), yt_analysis_film (video -> film, score, how),
yt_analysis_units (evidence units: one per chapter, or one per video without chapters; read by evidence.py).
"""
import argparse
import csv
import datetime
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse

import facts_db as F

YT = "https://www.googleapis.com/youtube/v3/"
HERE = os.path.dirname(os.path.abspath(__file__))
REFRESH_DAYS, DELETE_DAYS = 25, 30

# handle, kind, why (criterion notes)
ALLOW = [
    ("@everyframeapainting", "analysis", "film-form essays (Zhou & Ramos); widely taught"),
    ("@LessonsfromtheScreenplay", "analysis", "screenwriting analysis of named films"),
    ("@Nerdwriter1", "analysis", "video essays on films and directors"),
    ("@StudioBinder", "analysis", "filmmaking-craft breakdowns of named scenes"),
    ("@ThomasFlight", "analysis", "essays on directors and films"),
    ("@patrickhwillems", "analysis", "essays on film craft and genre"),
    ("@LikeStoriesofOld", "analysis", "essays on themes in named films"),
    ("@NowYouSeeIt", "analysis", "essays on film technique"),
    ("@FoldingIdeas", "analysis", "film criticism essays"),
    ("@JustWrite", "analysis", "screenwriting essays"),
    ("@TheRoyalOceanFilmSociety", "analysis", "essays on directors and film form"),
    ("@kaptainkristian", "analysis", "essays on animation and film craft"),
    ("@CineFix", "analysis", "craft rankings and breakdowns"),
    ("@ScreenPrism", "analysis", "character and theme analysis of named films"),
    ("@Filmento", "analysis", "story analysis of named films"),
    ("@EntertaintheElk", "analysis", "essays on filmmaking craft"),
    ("@AccentedCinema", "analysis", "essays on East Asian films"),
    ("@CinemaTyler", "analysis", "deep dives on named films"),
    ("@ChrisStuckmann", "review", "long-running film reviewer"),
    ("@RedLetterMedia", "review", "film reviews (Half in the Bag, Best of the Worst)"),
    ("@JeremyJahns", "review", "film reviews"),
    ("@kermodeandmayo", "review", "Mark Kermode's reviews (BBC film critic)"),
    ("@MoviesWithMikey", "analysis", "appreciations of named films"),
]

TECH = {
    "cinematography": r"\b(cinematograph\w*|camera|shots?|lens|lighting|framing|composition|long take|one[- ]take|tracking shot)\b",
    "editing": r"\b(edit(ing|ed)?|cuts?|montage|pacing|transitions?)\b",
    "sound and music": r"\b(score|soundtrack|sound design|music|silence)\b",
    "screenwriting": r"\b(script|screenplay|dialogue|story structure|structure|character arcs?|plot holes?|ending|twist|"
                     r"three[- ]act|writing)\b",
    "directing": r"\b(direct(ing|or|ion)|auteur|filmmaker)\b",
    "acting": r"\b(performances?|acting|actor|actress)\b",
    "colour": r"\b(colou?r( grading| palette)?|palette)\b",
    "visual effects": r"\b(cgi|vfx|visual effects|practical effects|special effects)\b",
    "production design": r"\b(production design|sets?|costumes?|world[- ]building)\b",
    "themes": r"\b(themes?|symbol\w*|meaning|motifs?|allegor\w*|metaphor)\b",
}

DDL = """
create table if not exists yt_channels(channel_id text primary key, handle text, title text, kind text, why text,
  uploads text, subscribers integer, fetched_at text);
create table if not exists yt_analysis(video_key text primary key, channel_id text, channel text, kind text, title text,
  description text, published_at text, duration text, views integer, fetched_at text);
create table if not exists yt_analysis_film(video_key text, tmdb_id integer, score real, how text,
  primary key (video_key, tmdb_id));
create table if not exists yt_analysis_units(video_key text, tmdb_id integer, chapter integer, start_s integer,
  chapter_title text, text text, channel text, published_at text, match_ok integer, techniques text,
  primary key (video_key, tmdb_id, chapter));
create index if not exists yt_analysis_units_film on yt_analysis_units(tmdb_id);
create table if not exists yt_analysis_comments(comment_id text primary key, video_key text, text text, likes integer,
  published_at text, fetched_at text);
create index if not exists yt_analysis_comments_video on yt_analysis_comments(video_key, likes);
create table if not exists yt_analysis_comments_done(video_key text primary key, n integer, status text, fetched_at text);
"""
# 4 Nov: like and comment counts (statistics) are stored with the views; comment text (top comments by relevance under
# each matched video) in yt_analysis_comments. API policy: text data refreshed or deleted within 30 days (refresh does both);
# statistics and our own derived sentiment may be kept up to 36 months under the Analytics & Reporting use case
# (https://developers.google.com/youtube/terms/derived-metrics-policy). Comment sentiment is ours, labelled as ours,
# aggregated per film, and never used to profile viewers.
COMMENTS_N = int(os.environ.get("YT_COMMENTS_N", "10"))


def connect(path=None):
    c = F.connect(path)
    c.executescript(DDL)
    have = {r[1] for r in c.execute("pragma table_info(yt_analysis)")}
    for col in ("likes", "comment_count"):           # 4 Nov: statistics beside the views
        if col not in have:
            c.execute(f"alter table yt_analysis add column {col} integer")
    c.commit()
    return c


class Quota(Exception):
    pass


def make_call(key, units):
    import fetch_free_data as FF

    def call(path, **p):
        p["key"] = key
        d = FF.api_get(f"{YT}{path}?{urllib.parse.urlencode(p)}", retries=3, label="YouTube")
        units["n"] += 1
        if FF.denied(d):
            if "quota" in json.dumps(d).lower():
                raise Quota()
            return None
        return None if d is FF.V.FAILED else d
    return call


def cmd_channels(a, conn, call):
    for handle, kind, why in ALLOW:
        d = call("channels", part="snippet,contentDetails,statistics", forHandle=handle)
        it = ((d or {}).get("items") or [None])[0]
        if not it:
            print(f"  {handle}: not found (check the handle)")
            continue
        up = ((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        subs = int((it.get("statistics") or {}).get("subscriberCount", 0) or 0)
        conn.execute("insert or replace into yt_channels values (?,?,?,?,?,?,?,?)",
                     (it["id"], handle, it["snippet"]["title"], kind, why, up, subs, F.now()))
        print(f"  {handle}: {it['snippet']['title']} ({kind})")
    conn.commit()


def _details(call, conn, ids, chan):
    ts = F.now()
    for s in range(0, len(ids), 50):
        d = call("videos", part="snippet,contentDetails,statistics", id=",".join(ids[s:s + 50]), maxResults=50)
        rows = []
        for it in (d or {}).get("items") or []:
            sn = it.get("snippet") or {}
            ch = chan.get(sn.get("channelId")) or (None, None)
            rows.append((it["id"], sn.get("channelId"), sn.get("channelTitle"), ch[1], sn.get("title"),
                         (sn.get("description") or "")[:5000], sn.get("publishedAt"),
                         (it.get("contentDetails") or {}).get("duration"),
                         int((it.get("statistics") or {}).get("viewCount", 0) or 0), ts))
        conn.executemany("insert or replace into yt_analysis(video_key, channel_id, channel, kind, title, description, "
                         "published_at, duration, views, fetched_at) values (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.executemany("update yt_analysis set likes = ?, comment_count = ? where video_key = ?",
                         [(int((it.get("statistics") or {}).get("likeCount", 0) or 0),
                           int((it.get("statistics") or {}).get("commentCount", 0) or 0), it["id"])
                          for it in (d or {}).get("items") or []])
        conn.commit()
        got = {r[0] for r in rows}
        gone = [v for v in ids[s:s + 50] if v not in got]
        if gone:                                 # deleted / private on YouTube: remove ours too (API policy)
            conn.executemany("delete from yt_analysis where video_key = ?", [(v,) for v in gone])
            conn.executemany("delete from yt_analysis_units where video_key = ?", [(v,) for v in gone])
            conn.executemany("delete from yt_analysis_film where video_key = ?", [(v,) for v in gone])
            conn.commit()


def cmd_fetch(a, conn, call):
    chans = conn.execute("select channel_id, handle, kind, uploads from yt_channels").fetchall()
    chan = {c: (h, k) for c, h, k, _u in chans}
    have = {r[0] for r in conn.execute("select video_key from yt_analysis")}
    for cid, handle, kind, up in chans:
        new, page = [], None
        for _ in range(a.max_pages):
            p = {"part": "contentDetails", "playlistId": up, "maxResults": 50}
            if page:
                p["pageToken"] = page
            d = call("playlistItems", **p)
            if not d:
                break
            ids = [(it.get("contentDetails") or {}).get("videoId") for it in d.get("items") or []]
            fresh = [v for v in ids if v and v not in have]
            new += fresh
            page = d.get("nextPageToken")
            if not page or (not fresh and not a.again):   # newest first: a page of known videos = caught up
                break
        _details(call, conn, new, chan)
        have |= set(new)
        print(f"  {handle}: {len(new)} new videos", flush=True)


def cmd_restats(a, conn, call):
    """4 Nov: re-read every stored video's details now, to fill the new like / comment counts (~1 unit per 50)."""
    chan = {c: (h, k) for c, h, k in conn.execute("select channel_id, handle, kind from yt_channels")}
    ids = [v for (v,) in conn.execute("select video_key from yt_analysis where likes is null")]
    print(f"restats: {len(ids):,} videos without like counts", flush=True)
    _details(call, conn, ids, chan)
    cmd_stats(a, conn)


# ------------------------------------------------------------------ film match + chapters
def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold().replace("&", "and"))).strip()


def title_index(conn, min_votes=300):
    rows = conn.execute("select tmdb_id, title, year, coalesce(vote_count, 0) from films where vote_count >= ?",
                        (min_votes,)).fetchall()
    idx = {}
    for fid, t, y, vc in rows:
        n = _norm(t)
        if n and (len(n.split()) >= 2 or len(n) >= 5):
            idx.setdefault(n, []).append((fid, y, vc))
    return idx


def match_films(title, desc, idx):
    """[(tmdb_id, score, how)] the video is about. Title evidence only counts for whole-phrase matches."""
    out = {}
    t = title or ""
    nt = " " + _norm(t) + " "
    years = [int(y) for y in re.findall(r"\b(19[2-9]\d|20[0-4]\d)\b", t)]
    quoted = [_norm(q) for q in re.findall(r"[\"“‘']([^\"”’']{2,80})[\"”’']", t)]
    for n, cands in idx.items():
        if f" {n} " not in nt:
            continue
        best = max(cands, key=lambda c: c[2])
        if years:
            near = [c for c in cands if c[1] and any(abs(c[1] - y) <= 1 for y in years)]
            best = max(near, key=lambda c: c[2]) if near else best
        one_word = len(n.split()) == 1
        if n in quoted or (years and best[1] and any(abs(best[1] - y) <= 1 for y in years)):
            score, how = 1.0, "quoted or with its year in the title"
        elif not one_word and len(n) >= 8:
            score, how = 0.85, "full title in the video title"
        elif not one_word:
            score, how = 0.6, "short title in the video title"
        elif best[2] >= 2000 and re.search(rf"(^|[:\-–|] *)({re.escape(n)})\b|\b{re.escape(n)}\s*(['’]s\b|[:\-–|]|"
                                            rf"review|explained|analysis|breakdown|ending|essay)", t, re.I) and \
                re.search(rf"\b{re.escape(n[:1].upper() + n[1:])}", t):
            score, how = 0.85, "well-known one-word title, written as a title"
        else:
            score, how = 0.4, "one-word title in the video title"
        # a longer title containing this one wins ("The Dark Knight Rises" over "The Dark Knight")
        if any(n != m and f" {n} " in f" {m} " and f" {m} " in nt for m in idx):
            continue
        if best[0] not in out or out[best[0]][0] < score:
            out[best[0]] = (score, how)
    if not out and desc:
        nd = " " + _norm(desc[:400]) + " "
        for n, cands in idx.items():
            if len(n.split()) >= 2 and len(n) >= 10 and f" {n} " in nd:
                best = max(cands, key=lambda c: c[2])
                out[best[0]] = (0.5, "title in the description only")
    return sorted(((f, s, h) for f, (s, h) in out.items()), key=lambda x: -x[1])[:3]


_TS = re.compile(r"^\s*[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?\s*[-–—:|.]?\s*(.{2,120})$")


def chapters(desc):
    """[(start_s, title)] when the description has a chapter list (>= 3 timestamps starting at 0:00)."""
    out = []
    for line in (desc or "").split("\n"):
        m = _TS.match(line.strip())
        if m:
            parts = [int(x) for x in m.group(1).split(":")]
            s = parts[-1] + 60 * parts[-2] + (3600 * parts[0] if len(parts) == 3 else 0)
            out.append((s, m.group(2).strip()))
    if len(out) >= 3 and out[0][0] == 0:
        return out
    return []


def techniques(text):
    return ",".join(k for k, rx in TECH.items() if re.search(rx, text or "", re.I))


def clean_desc(desc):
    lines = []
    for l in (desc or "").split("\n"):
        l = l.strip()
        if not l or _TS.match(l) or re.search(r"https?://|patreon|sponsor|subscribe|merch|instagram|twitter|"
                                              r"tiktok|discord|support the channel|use code|\bad\b", l, re.I):
            continue
        lines.append(l)
    return " ".join(lines)[:700]


def cmd_match(a, conn):
    idx = title_index(conn, a.min_votes)
    vids = conn.execute("select video_key, title, description, channel, kind, published_at from yt_analysis").fetchall()
    conn.execute("delete from yt_analysis_film")
    conn.execute("delete from yt_analysis_units")
    nf = nu = 0
    for vid, title, desc, channel, kind, pub in vids:
        ms = match_films(title, desc, idx)
        conn.executemany("insert or replace into yt_analysis_film values (?,?,?,?)", [(vid, f, s, h) for f, s, h in ms])
        chs = chapters(desc)
        body = clean_desc(desc)
        for fid, score, _how in ms:
            ok = int(score >= a.min_score)
            label = "Video essay" if kind == "analysis" else "YouTube review"
            if chs:
                for i, (st, ct) in enumerate(chs):
                    txt = f"{label} \"{title}\" - chapter: {ct}"
                    conn.execute("insert or replace into yt_analysis_units values (?,?,?,?,?,?,?,?,?,?)",
                                 (vid, fid, i, st, ct, txt, channel, pub, ok, techniques(f"{title} {ct}")))
                    nu += 1
            else:
                txt = f"{label} \"{title}\"" + (f": {body}" if body else "")
                conn.execute("insert or replace into yt_analysis_units values (?,?,?,?,?,?,?,?,?,?)",
                             (vid, fid, 0, 0, None, txt, channel, pub, ok, techniques(f"{title} {body}")))
                nu += 1
            nf += ok
    conn.commit()
    print(f"match: {len(vids):,} videos; {nf:,} confident video-film matches; {nu:,} evidence units")
    cmd_stats(a, conn)


def cmd_comments(a, conn, call, refresh_only=False):
    """Top comments (by relevance) under every confidently matched video: 1 API unit per video. Videos fetched less
    than REFRESH_DAYS ago are skipped; comments switched off by the channel are recorded as such."""
    now = datetime.datetime.now(datetime.timezone.utc)
    done = {v: ts for v, ts in conn.execute("select video_key, fetched_at from yt_analysis_comments_done")}
    vids = [v for (v,) in conn.execute("""select u.video_key from yt_analysis_units u join yt_analysis a
        on a.video_key = u.video_key where u.match_ok = 1 group by u.video_key order by max(a.views) desc""")]
    todo = [v for v in vids if v not in done or (now - datetime.datetime.fromisoformat(done[v])).days >= REFRESH_DAYS]
    if refresh_only:
        todo = [v for v in todo if v in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"comments: {len(vids):,} matched videos, {len(todo):,} to fetch ({COMMENTS_N} each, 1 unit per video)", flush=True)
    n = 0
    for i, vid in enumerate(todo, 1):
        d = call("commentThreads", part="snippet", videoId=vid, order="relevance", maxResults=COMMENTS_N,
                 textFormat="plainText")
        ts = F.now()
        rows = []
        for it in (d or {}).get("items") or []:
            sn = (((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {})
            if sn.get("textDisplay"):
                rows.append((it["id"], vid, sn["textDisplay"][:2000], int(sn.get("likeCount", 0) or 0),
                             sn.get("publishedAt"), ts))
        conn.execute("delete from yt_analysis_comments where video_key = ?", (vid,))
        conn.executemany("insert or replace into yt_analysis_comments values (?,?,?,?,?,?)", rows)
        conn.execute("insert or replace into yt_analysis_comments_done values (?,?,?,?)",
                     (vid, len(rows), "ok" if d else "off_or_failed", ts))
        n += len(rows)
        if i % 200 == 0:
            conn.commit()
            print(f"  {i:,}/{len(todo):,} videos, {n:,} comments", flush=True)
    conn.commit()
    print(f"  comments stored: {n:,} from {len(todo):,} videos", flush=True)


def cmd_refresh(a, conn, call):
    """YouTube API policy: stored API data is refreshed or deleted within 30 days."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old = [v for v, ts in conn.execute("select video_key, fetched_at from yt_analysis")
           if ts and (now - datetime.datetime.fromisoformat(ts)).days >= REFRESH_DAYS]
    chan = {c: (h, k) for c, h, k in conn.execute("select channel_id, handle, kind from yt_channels")}
    print(f"refresh: {len(old):,} videos fetched >= {REFRESH_DAYS} days ago", flush=True)
    try:
        _details(call, conn, old, chan)
    except Quota:
        print("  quota used up; the rest tomorrow")
    stale = [v for v, ts in conn.execute("select video_key, fetched_at from yt_analysis")
             if ts and (now - datetime.datetime.fromisoformat(ts)).days > DELETE_DAYS]
    for t in ("yt_analysis", "yt_analysis_units", "yt_analysis_film", "yt_analysis_comments", "yt_analysis_comments_done"):
        conn.executemany(f"delete from {t} where video_key = ?", [(v,) for v in stale])
    conn.commit()
    print(f"  deleted {len(stale):,} videos not refreshed within {DELETE_DAYS} days")
    try:                                                 # 4 Nov: comment text follows the same 30-day rule
        cmd_comments(a, conn, call, refresh_only=True)
    except Quota:
        print("  quota used up before the comment refresh; the rest tomorrow")
    old_c = [v for v, ts in conn.execute("select video_key, fetched_at from yt_analysis_comments_done")
             if ts and (now - datetime.datetime.fromisoformat(ts)).days > DELETE_DAYS]
    conn.executemany("delete from yt_analysis_comments where video_key = ?", [(v,) for v in old_c])
    conn.executemany("delete from yt_analysis_comments_done where video_key = ?", [(v,) for v in old_c])
    conn.commit()
    print(f"  deleted comments of {len(old_c):,} videos not refreshed within {DELETE_DAYS} days")


def cmd_sample(a, conn):
    rows = conn.execute("""select f.video_key, a.channel, a.title, m.title, m.year, f.score, f.how
        from yt_analysis_film f join yt_analysis a on a.video_key = f.video_key join films m on m.tmdb_id = f.tmdb_id
        where f.score >= ?""", (a.min_score,)).fetchall()
    random.seed(a.seed)
    pick = random.sample(rows, min(a.n, len(rows)))
    path = os.path.join(HERE, "results", "video_match_check.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video", "channel", "video title", "matched film", "how", "right (y/n)"])
        for vid, ch, vt, ft, fy, sc, how in pick:
            w.writerow([f"https://youtu.be/{vid}", ch, vt, f"{ft} ({fy})", how, ""])
    print(f"{len(pick)} videos -> {path}")


def cmd_score(a, conn):
    rows = [r for r in csv.DictReader(open(a.path, encoding="utf-8-sig")) if (r.get("right (y/n)") or "").strip()]
    ok = sum(1 for r in rows if r["right (y/n)"].strip().lower().startswith("y"))
    print(f"right film: {ok}/{len(rows)} = {ok / max(1, len(rows)):.0%} (rule >= 90%): "
          f"{'PASS' if rows and ok / len(rows) >= 0.9 else 'FAIL'}")


def cmd_stats(a, conn):
    one = lambda sql: (conn.execute(sql).fetchone() or [0])[0] or 0  # noqa: E731
    print(f"  channels {one('select count(*) from yt_channels')}, videos {one('select count(*) from yt_analysis'):,}, "
          f"films with a confident video {one('select count(distinct tmdb_id) from yt_analysis_units where match_ok = 1'):,}, "
          f"units {one('select count(*) from yt_analysis_units where match_ok = 1'):,}")
    print(f"  with like counts {one('select count(*) from yt_analysis where likes is not null'):,}; comments "
          f"{one('select count(*) from yt_analysis_comments'):,} under {one('select count(distinct video_key) from yt_analysis_comments'):,} videos")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["channels", "fetch", "match", "refresh", "sample", "score", "stats", "comments",
                                    "restats"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--min-votes", type=int, default=300)
    ap.add_argument("--min-score", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    conn = connect(a.facts)
    units = {"n": 0}
    if a.cmd in ("channels", "fetch", "refresh", "comments", "restats"):
        key = (os.environ.get("YOUTUBE_API_KEY") or "").strip()
        if not key:
            sys.exit("export YOUTUBE_API_KEY=... first")
        call = make_call(key, units)
        try:
            {"channels": cmd_channels, "fetch": cmd_fetch, "refresh": cmd_refresh, "comments": cmd_comments,
             "restats": cmd_restats}[a.cmd](a, conn, call)
        except Quota:
            print("  YouTube daily quota used up: stopping; it resets at midnight Pacific")
        print(f"  ({units['n']} API calls)")
        return
    if a.cmd == "sample":
        a.n = int(a.arg or 50)
    if a.cmd == "score":
        a.path = a.arg or os.path.join(HERE, "results", "video_match_check.csv")
    {"match": cmd_match, "sample": cmd_sample, "score": cmd_score, "stats": cmd_stats}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
