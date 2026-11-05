#!/usr/bin/env python3
"""
R12 + R13 (v7, 27 Oct): one reception card per film, and three-level reception summaries.
With R8 (audience distributions), E2 (reappraisal labels), E3 (sample-size honesty), E4 (critic-audience gap lists)
and H9 (the box-office model's factors as short lines inside the card).

    python reception_cards.py build              # every film with any reception signal (SQL only, minutes)
    python reception_cards.py summaries --films 3000   # R13: llama3.2 summaries for the most-reviewed films,
                                                 # rebuilt only when what is under them changed (hash in the key)
    python reception_cards.py show "Oppenheimer"  # the card exactly as the answer model would see it
    python reception_cards.py check 20           # results/reception_card_check.md: 20 random cards to read by eye
    python reception_cards.py gaps --side critics --limit 20      # E4: critics liked it much more than audiences
    python reception_cards.py gaps --side audiences               #     audiences liked it much more
    python reception_cards.py divisive           # R8: most polarised films (MovieLens bimodality, n >= 100)
    python reception_cards.py reappraised        # E2: films whose standing rose (or fell) after release
    python reception_cards.py stats

What a card holds (reception_card table; `text` is what tools.py hands the answer model, each line source-tagged):
    critics      Rotten Tomatoes / Metacritic (Wikipedia text, Wikidata, OMDb - the first that has it, dated);
                 Wikipedia critic statements: how many, their sentiment, the named outlets and critics (R2, R3)
    audiences    CinemaScore, IMDb, MovieLens 32M (mean, Bayesian mean, n), TMDB rating, TMDB user reviews'
                 sentiment (usable reviews only; review-bombing bursts and jokes left out and counted)
    praise / complaints   the most praised / criticised aspects, audience and critics apart, with a quote each
    themes       MovieLens tag genome: the most relevant tags (R9)
    gap          critics vs audiences on one 0-100 scale (critics: Metacritic, else RT average x 10, else RT %;
                 audiences: IMDb x 10, else MovieLens mean x 20, else TMDB x 10); |gap| >= 15 = they disagree
    polarisation MovieLens rating histogram: sd, share of low (<= 2 of 5) and high (>= 4.5) ratings, bimodality
                 coefficient (> 0.555 suggests two camps); "divisive" needs n >= 100, bc > 0.555, both >= 15%
    over time    E2: MovieLens mean in the release years vs 5+ years later (n >= 200 each; +/- 0.25 = rose / fell);
                 Wikipedia Legacy text saying "cult", "reappraisal", "re-evaluated" ... (quoted)
    samples      E3: n for every number; below the minimum the line says it is too few to generalise
                 (audience reviews 10, MovieLens 100, critic statements 3)
    model        H9: the box-office model's expectation vs the actual result (performance.py), when fitted
    summary      R13 level 3: llama3.2's 3-4 sentence summary of the card (marked as Movie Buff's summary)

Summaries (R13, in the existing `summaries` table, kind = 'reception'):
    level 1  per review: no model - the review's sentiment label and verified aspects with quotes already are its
             summary (review_sentiment, review_aspects)
    level 2  per source per film: what critics said (from the critic statements), what audiences said (from
             sentiment counts, aspects and quotes) - one llama3.2 call each
    level 3  per film: from the level-2 summaries and the card's numbers
    key = rc|<level>|<source>|<tmdb_id>|<hash of inputs>|<VERSION>; a changed input -> a new key -> rebuilt.
    The claim checker (claimcheck.py, R16) scores every summary sentence against its inputs (rule: >= 95%).
"""
import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.request
import zlib

import facts_db as F

VERSION = "rc1"
SUM_REV = "s2"  # 29 Oct: summary prompt v2 (numbers with units, Metacritic out of 100, who said it)
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
MIN_AUDIENCE_REVIEWS = 10            # E3 (same as tools.EVIDENCE_MIN)
MIN_ML = 100
MIN_CRITIC_STMTS = 3
MIN_RT_N = 20                        # 28 Oct: a Rotten Tomatoes % from fewer reviews is not used for the gap
# 1 Nov: ... and neither is one whose review count is unknown (4,973 films have an RT % only from Wikidata, with no
# count; Seoul Station / AKA at 100% and Kronk's New Groove at 0% topped the gap lists). RT_UNKNOWN_N=1 = old rule.
RT_UNKNOWN_N = os.environ.get("RT_UNKNOWN_N", "0") == "1"
MIN_TREND_N = 200
GAP_DISAGREE = 15
SUM_MODEL = os.environ.get("SUM_MODEL", "llama3.2")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")

DDL = """
create table if not exists reception_card(tmdb_id integer primary key, version text, built_at text, inputs_hash text,
  critic_score real, critic_score_src text, audience_score real, audience_score_src text, gap real, gap_label text,
  polar_label text, bc real, ml_sd real, ml_n integer, trend_label text, trend_delta real, legacy_label text,
  n_critic_stmts integer, critic_pos real, n_aud_reviews integer, aud_pos real, aud_neg real, aud_mixed real,
  bombing integer, data_json text, text text);
create index if not exists reception_card_gap on reception_card(gap);
"""


def connect(path=None, readonly=False):
    if readonly:
        return F.connect(path, readonly=True)
    conn = F.connect(path)
    conn.executescript(DDL)
    return conn


_WARNED = set()


def q(conn, sql, *p):
    """Rows, or [] when a table is missing (every source is optional). Other errors are printed once."""
    try:
        return conn.execute(sql, p).fetchall()
    except sqlite3.Error as e:
        if "no such table" not in str(e) and str(e) not in _WARNED:
            _WARNED.add(str(e))
            print(f"[reception_cards] {e}: {' '.join(sql.split())[:120]}", file=sys.stderr)
        return []


def q1(conn, sql, *p):
    r = q(conn, sql, *p)
    return r[0] if r else None


def _n(v):
    return f"{int(v):,}" if v is not None else "?"


def mc_agrees(a, b, tol=10):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def mc_plausible(mc, text, rt):
    """A Metacritic number parsed from Wikipedia text: kept unless it is not written as 'N out of 100' / 'N/100'
    AND it contradicts Rotten Tomatoes (< 30 while RT >= 70%, or > 80 while RT <= 30%). 28 Oct: Lawrence of Arabia
    came out as 'Metacritic 9'."""
    if text and re.search(rf"\b{int(mc)}\b\s*(?:out of 100|/\s?100)", text):
        return True
    if rt and rt[0] is not None and ((mc < 30 and rt[0] >= 70) or (mc > 80 and rt[0] <= 30)):
        return False
    return True


# ------------------------------------------------------------------ gather
def critic_scores(conn, fid):
    """[(label, value_text, source_tag)] and the best 0-100 critic score with its source."""
    lines, best = [], (None, None)
    r = q1(conn, "select rt_pct, rt_n, rt_avg, mc_score, mc_n, cinemascore, snapshot, mc_text from critic_scores "
                 "where tmdb_id = ?", fid)
    wd = q(conn, "select source, score_text, value, scale, n_reviews, as_of from wikidata_scores where tmdb_id = ? "
                 "order by as_of desc", fid)
    om = q1(conn, "select rt_pct, mc_score, fetched_at from omdb where tmdb_id = ? and status = 'ok'", fid)
    rt = mc = cs = None
    if r and (r[0] is not None or r[3] is not None or r[5]):
        snap = str(r[6] or "")[:7]
        tag = f"[Wikipedia: Reception{', ' + snap if snap else ''}]"
        if r[0] is not None:
            rt = (r[0], r[1], r[2], tag)
        if r[3] is not None:
            mc = (r[3], r[4], tag)
        if r[5]:
            cs = (r[5], tag)
    for src, txt, val, scale, n, asof in wd:
        tag = f"[Wikidata{', ' + asof[:7] if asof else ''}]"
        if src == "Rotten Tomatoes" and scale == 100 and rt is None:
            rt = (val, n, None, tag)
        elif src == "Rotten Tomatoes" and scale == 10 and rt and rt[2] is None and rt[3] == tag:
            rt = (rt[0], rt[1], val, tag)
        elif src == "Metacritic" and scale == 100 and val is not None and (mc is None or mc[2] != tag):
            mc_wd = (val, n, tag)
            if mc is None:
                mc = mc_wd
            elif not mc_agrees(mc[0], val):
                mc = mc_wd                       # 28 Oct: Wikipedia-text Metacritic disagreed; Wikidata wins
    if om:
        tag = f"[OMDb, {str(om[2])[:7]}]"
        if rt is None and om[0] is not None:
            rt = (om[0], None, None, tag)
        if om[1] is not None and (mc is None or (mc[2].startswith("[Wikipedia") and not mc_agrees(mc[0], om[1]))):
            mc = (om[1], None, tag)
    if mc and mc[2].startswith("[Wikipedia") and not mc_plausible(mc[0], r[7] if r else None, rt):
        mc = None                                # text parse looks wrong and nothing else confirms it
    if rt:
        lines.append(f"Rotten Tomatoes {rt[0]:.0f}% positive" + (f" of {_n(rt[1])} reviews" if rt[1] else "")
                     + (f", average {rt[2]:g}/10" if rt[2] is not None else "") + f" {rt[3]}")
    if mc:
        lines.append(f"Metacritic {mc[0]:.0f}/100" + (f" from {_n(mc[1])} critics" if mc[1] else "") + f" {mc[2]}")
    if mc:
        best = (float(mc[0]), "Metacritic")
    elif rt and rt[2] is not None:
        best = (float(rt[2]) * 10, "Rotten Tomatoes average")
    elif rt and (rt[1] >= MIN_RT_N if rt[1] is not None else RT_UNKNOWN_N):
        best = (float(rt[0]), "Rotten Tomatoes %")       # share of positive reviews: fewer than MIN_RT_N (or an
                                                         # unknown count, 1 Nov) -> no score for the gap
    return lines, best, cs


def critic_statements(conn, fid):
    rows = q(conn, """select c.stmt_id, c.kind, c.outlet, c.critic, c.text, s.label, c.quote, c.section_kind
        from critic_statements c left join review_sentiment s on s.item_key = 'cs:' || c.stmt_id
        where c.tmdb_id = ? and c.kind in ('critic', 'summary') order by c.stmt_id""", fid)
    return rows


def audience(conn, fid, usable=None):
    """TMDB audience review sentiment: usable reviews only; bursts and jokes counted apart."""
    rows = q(conn, """select s.item_key, s.label, s.confidence, coalesce(g.burst, 0), coalesce(g.joke, 0)
        from review_sentiment s left join review_flags g on g.review_id = s.item_key
        where s.tmdb_id = ? and s.source_type = 'audience'""", fid)
    kept, burst, joke, unusable = [], 0, 0, 0
    for k, lab, conf, b, j in rows:
        if b:
            burst += 1
            continue
        if j:
            joke += 1
            continue
        if usable is not None and k not in usable:
            unusable += 1
            continue
        kept.append(lab)
    n = len(kept)
    share = {l: (kept.count(l) / n if n else 0.0) for l in ("positive", "mixed", "negative")}
    return n, share, {"burst": burst, "joke": joke, "unusable": unusable, "all": len(rows)}


def aspects(conn, fid):
    out = {}
    for st, asp, pos, neg, mixed, n, quotes in q(conn, "select source_type, aspect, pos, neg, mixed, n_reviews, "
                                                       "quotes_json from film_aspects where tmdb_id = ?", fid):
        out.setdefault(st, []).append((asp, pos or 0, neg or 0, mixed or 0, n or 0, json.loads(quotes or "[]")))
    return out


def legacy_label(conn, fid):
    rows = q(conn, "select wikitext_z from wiki_reception where tmdb_id = ? and kind in ('legacy', 'reception')", fid)
    pat = re.compile(r"[^.]*\b(cult (?:film|classic|following|status|favou?rite)|reappraise\w*|re-?evaluat\w*|"
                     r"reassess\w*|re-?assess\w*|in retrospect|retrospective(?:ly)?|initially (?:received|met|panned|"
                     r"poorly|mixed|negative)|has since (?:been|become|gained)|critical reassessment|"
                     r"reconsider\w*)\b[^.]*\.", re.I)
    for (z,) in rows:
        try:
            t = zlib.decompress(z).decode("utf-8", "replace")
        except zlib.error:
            continue
        try:
            import critics as C
            t = C.strip_markup(re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S))
        except Exception:  # noqa: BLE001
            t = re.sub(r"<[^>]+>|\{\{[^}]*\}\}|\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)
        m = pat.search(t)
        if m:
            sent = " ".join(m.group(0).split())
            kind = "cult following" if re.search(r"\bcult\b", sent, re.I) else "reappraised"
            return kind, sent[:300]
    return None, None


def perf_lines(conn, fid):
    """H9: the box-office model in two short lines, or []."""
    r = q1(conn, "select actual, pre, post from perf_film where tmdb_id = ?", fid)
    if not r or r[1] is None:
        return []
    try:
        import performance as P
        x = P._x
    except Exception:  # noqa: BLE001
        x = lambda v: f"{2.718281828 ** v:.1f}x"  # noqa: E731 - model works in log space
    actual, pre, post = r
    out = [f"Expected before release (box-office model, a statistical estimate): about {x(pre)} its budget"]
    if actual is not None:
        gap = actual - pre
        word = ("far above" if gap > 1.0 else "above" if gap > 0.4 else "far below" if gap < -1.0
                else "below" if gap < -0.4 else "close to")
        out.append(f"Actual: {x(actual)} its budget, {word} that expectation"
                   + (f"; with its reception added the model expects {x(post)}" if post is not None else ""))
    return out


def build_card(conn, fid, usable=None):
    f = q1(conn, "select title, year, vote_average, vote_count, imdb_id, release_date from films where tmdb_id = ?", fid)
    if not f:
        return None
    title, year, va, vc, imdb, rd = f
    lines, data = [f"Reception card: {title} ({year or '?'}) - Movie Buff's reception data, sources in brackets"], {}
    # critics
    cl, (cscore, csrc), cs = critic_scores(conn, fid)
    st = critic_statements(conn, fid)
    named = [r for r in st if r[1] == "critic" and r[2]]
    labs = [r[5] for r in st if r[5]]
    cpos = labs.count("positive") / len(labs) if labs else None
    if st:
        few = len(labs) < MIN_CRITIC_STMTS
        sbit = (f"{len(st)} critic statements in Wikipedia's reception text" + (
            f": {labs.count('positive')} positive, {labs.count('mixed')} mixed, {labs.count('negative')} negative"
            if labs else "") + (" (too few to generalise)" if few else "") + " [Wikipedia: Reception; sentiment: Movie Buff]")
        cl.append(sbit)
        if named:
            seen, bits = set(), []
            for r in named:
                key = (r[2], r[3])
                if key in seen:
                    continue
                seen.add(key)
                bits.append(f"{r[2]}" + (f" ({r[3]})" if r[3] else "") + (f": {r[5]}" if r[5] else ""))
            cl.append("Named in it: " + "; ".join(bits[:8]) + " [Wikipedia: Reception]")
        # 28 Oct: the share of positive statements is NOT used as a critic score (it is not on the same scale as a
        # Metacritic / RT number and small films have few statements: Radio Rebel "100" from 2). Text line only.
    pro = []                                         # R7: Guardian stars; R6: NYT critic + Critic's Pick (no NYT text)
    for critic, stars, pub in q(conn, "select critic, stars, published from critic_reviews where tmdb_id = ? and "
                                      "source = 'guardian' and match >= 1 and stars is not null order by published", fid):
        pro.append(f"The Guardian {stars:g}/5 stars ({', '.join(x for x in (critic, str(pub or '')[:7]) if x)}) "
                   f"[Guardian review]")
    for key, critic, pub in q(conn, "select review_key, critic, published from critic_reviews where tmdb_id = ? and "
                                    "source = 'nyt' and match >= 1 order by published", fid):
        pick = (q1(conn, "select critics_pick from nyt_meta where review_key = ?", key) or [0])[0]
        pro.append(f"The New York Times reviewed it ({', '.join(x for x in (critic, str(pub or '')[:7]) if x)}"
                   f"{'; Critic' + chr(8217) + 's Pick' if pick else ''}) [NYT, link under the answer]")
    if pro:
        cl.append("; ".join(pro[:3]))
    if cl:
        lines.append("Critics: " + " | ".join(cl))
    data["critics"] = {"score": cscore, "score_src": csrc, "n_statements": len(st), "pos_share": cpos,
                       "named": [(r[2], r[3], r[5]) for r in named][:12]}
    # audiences
    al, ascore, asrc = [], None, None
    if cs:
        al.append(f"CinemaScore (opening-night audiences) {cs[0]} {cs[1]}")
    im = q1(conn, "select rating, votes, fetched_at from imdb_ratings where imdb_id = ?", imdb) if imdb else None
    if im and im[0]:
        al.append(f"IMDb {im[0]:.1f}/10 from {_n(im[1])} votes [IMDb, {str(im[2])[:7]}]")
        ascore, asrc = im[0] * 10, "IMDb"
    ml = q1(conn, "select n, mean, sd, bayes_mean, pct_low, pct_high, bc, early_n, early_mean, late_n, late_mean, film_year "
                  "from ml_dist where tmdb_id = ?", fid)
    if ml and ml[0]:
        few = ml[0] < MIN_ML
        al.append(f"MovieLens {ml[1]:.2f}/5 from {_n(ml[0])} ratings (Bayesian {ml[3]:.2f})"
                  + (" (too few to generalise)" if few else "") + " [MovieLens 32M, ratings to 2023]")
        if ascore is None and not few:
            ascore, asrc = ml[1] * 20, "MovieLens"
    if va and (vc or 0) >= 50:
        al.append(f"TMDB {va:.1f}/10 from {_n(vc)} votes [TMDB]")
        if ascore is None:
            ascore, asrc = va * 10, "TMDB"
    n_aud, share, excl = audience(conn, fid, usable)
    if n_aud or excl["all"]:
        bits = (f"TMDB user reviews: {n_aud} analysed - {share['positive']:.0%} positive, {share['mixed']:.0%} mixed, "
                f"{share['negative']:.0%} negative")
        if n_aud < MIN_AUDIENCE_REVIEWS:
            bits += f" (only {n_aud}: too few to generalise)"
        left = [f"{v} {k if k != 'burst' else 'in a review-bombing burst'}" for k, v in excl.items()
                if k in ("burst", "joke", "unusable") and v]
        if left:
            bits += " (left out: " + ", ".join(x.replace(" joke", " jokes").replace(" unusable", " failing the quality "
                                                                                       "filter") for x in left) + ")"
        al.append(bits + " [TMDB user reviews; sentiment: Movie Buff]")
    if al:
        lines.append("Audiences: " + " | ".join(al))
    data["audience"] = {"score": ascore, "score_src": asrc, "n_reviews": n_aud, "share": share, "excluded": excl}
    # praise / complaints
    asp = aspects(conn, fid)
    praise, compl = [], []
    for stp, who in (("audience", "audience"), ("critic", "critics")):
        rows = asp.get(stp) or []
        for a, pos, neg, mixed, n, quotes in sorted(rows, key=lambda r: -(r[1] - r[2])):
            if pos >= 2 and pos > neg:
                praise.append((pos, f"{a} ({who} {pos} of {n}" + (f'; "{quotes[0]}"' if quotes else "") + ")"))
        for a, pos, neg, mixed, n, quotes in sorted(rows, key=lambda r: -(r[2] - r[1] * 0.5)):
            if neg >= 2 and neg >= pos * 0.5:
                compl.append((neg, f"{a} ({who} {neg} of {n}" + (f'; "{quotes[-1]}"' if quotes else "") + ")"))
    if praise:
        lines.append("Most praised: " + "; ".join(x for _n_, x in sorted(praise, key=lambda t: -t[0])[:4])
                     + " [aspects by Movie Buff from TMDB user reviews / Wikipedia]")
    if compl:
        lines.append("Most criticised: " + "; ".join(x for _n_, x in sorted(compl, key=lambda t: -t[0])[:4])
                     + " [aspects by Movie Buff from TMDB user reviews / Wikipedia]")
    data["praise"] = [x for _a, x in praise][:6]
    data["complaints"] = [x for _a, x in compl][:6]
    tags = q(conn, "select tag from film_tags where tmdb_id = ? and relevance >= 0.6 order by rank limit 8", fid)
    if tags:
        lines.append("Recurring themes in how users tag it: " + ", ".join(t for (t,) in tags)
                     + " [MovieLens tag genome]")
    # R11: what allow-listed analysis / review channels made about it (titles and chapters only)
    vids = q(conn, "select video_key, channel, techniques from yt_analysis_units where tmdb_id = ? and match_ok = 1", fid)
    if vids:
        vk = {v for v, _c, _t in vids}
        chans = list(dict.fromkeys(c for _v, c, _t in vids if c))
        topics = {}
        for _v, _c, t in vids:
            for x in (t or "").split(","):
                if x:
                    topics[x] = topics.get(x, 0) + 1
        top = sorted(topics.items(), key=lambda kv: -kv[1])[:5]
        lines.append(f"Analysis videos: {len(vk)} from {', '.join(chans[:4])}"
                     + (f"; topics: {', '.join(f'{k} ({n})' for k, n in top)}" if top else "")
                     + " [YouTube, allow-listed channels; titles and chapters only]")
        data["videos"] = {"n": len(vk), "channels": chans[:6], "topics": top}
    # gap
    gap, gl = None, None
    if cscore is not None and ascore is not None:
        gap = round(cscore - ascore, 1)
        gl = ("critics liked it much more than audiences" if gap >= GAP_DISAGREE else
              "audiences liked it much more than critics" if gap <= -GAP_DISAGREE else
              "critics and audiences broadly agree")
        lines.append(f"Critics vs audiences (0-100): critics {cscore:.0f} ({csrc}) vs audiences {ascore:.0f} ({asrc}); "
                     f"gap {gap:+.0f}: {gl} [computed]")
    # polarisation (R8)
    pl = None
    if ml and ml[0]:
        n, mean, sd, bayes, lo, hi, bc = ml[:7]
        if n >= MIN_ML:
            pl = ("divisive (two camps)" if (bc or 0) > 0.555 and lo >= 0.15 and hi >= 0.15 else
                  "spread out" if sd >= 1.2 else "broadly agreed")
            lines.append(f"Audience spread: MovieLens sd {sd:.2f}, {lo:.0%} of ratings <= 2/5, {hi:.0%} >= 4.5/5, "
                         f"bimodality {bc if bc is None else round(bc, 2)} -> {pl} (n {_n(n)}) [MovieLens 32M, computed]")
    # over time (E2)
    tl, td = None, None
    if ml and ml[7] and ml[9] and ml[7] >= MIN_TREND_N and ml[9] >= MIN_TREND_N:
        td = round(ml[10] - ml[8], 2)
        tl = "rose" if td >= 0.25 else "fell" if td <= -0.25 else "steady"
        lines.append(f"Over time: MovieLens mean {ml[8]:.2f} in the release years (n {_n(ml[7])}) vs {ml[10]:.2f} five or "
                     f"more years later (n {_n(ml[9])}): {tl} [MovieLens 32M, computed]")
    lg, lsent = legacy_label(conn, fid)
    if lg:
        lines.append(f"Later standing: {lg} - \"{lsent}\" [Wikipedia: Legacy / Reception]")
    # bombing
    bomb = q1(conn, "select count(*), sum(n) from review_bursts where tmdb_id = ? and direction = 'low'", fid)
    if bomb and bomb[0]:
        lines.append(f"Review bombing: {bomb[0]} burst(s) of extreme low ratings ({_n(bomb[1])} reviews), left out of "
                     f"the counts above [TMDB user reviews, computed]")
    # H9
    pls = perf_lines(conn, fid)
    if pls:
        lines.append("Box office vs expectation: " + "; ".join(pls) + " [computed]")
    if len(lines) == 1:
        return None
    # level-3 summary, if built for these inputs
    h = hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]
    s3 = q1(conn, "select text from summaries where tmdb_id = ? and kind = 'reception' and level = '3' "
                  "order by created_at desc limit 1", fid)
    text = "\n".join(lines)
    if s3:
        text += f"\nSummary (Movie Buff's own summary of the lines above): {s3[0]}"
    data.update({"gap": gap, "gap_label": gl, "polar": pl, "trend": tl, "trend_delta": td, "legacy": lg})
    return {"tmdb_id": fid, "hash": h, "critic_score": cscore, "critic_src": csrc, "audience_score": ascore,
            "audience_src": asrc, "gap": gap, "gap_label": gl, "polar": pl, "bc": ml[6] if ml else None,
            "ml_sd": ml[2] if ml else None, "ml_n": ml[0] if ml else None, "trend": tl, "trend_delta": td,
            "legacy": lg, "n_critic": len(st), "critic_pos": cpos, "n_aud": n_aud, "share": share,
            "bombing": bomb[0] if bomb else 0, "data": data, "text": text}


def _usable():
    try:
        import review_filter
        return review_filter.usable_ids()
    except Exception:  # noqa: BLE001
        return None


def card_films(conn):
    ids = set()
    for sql in ("select distinct tmdb_id from critic_statements", "select distinct tmdb_id from review_sentiment where "
                "source_type = 'audience'", "select tmdb_id from critic_scores", "select distinct tmdb_id from "
                "wikidata_scores", "select tmdb_id from ml_dist where n >= 20", "select distinct tmdb_id from film_aspects",
                "select tmdb_id from omdb where status = 'ok'"):
        ids.update(r[0] for r in q(conn, sql) if r[0] is not None)
    return sorted(ids)


def cmd_build(a, conn):
    fids = card_films(conn)
    if a.limit:
        fids = fids[:a.limit]
    usable = _usable()
    print(f"reception cards: {len(fids):,} films with a signal", flush=True)
    t0, rows = time.time(), []
    for i, fid in enumerate(fids, 1):
        c = build_card(conn, fid, usable)
        if c:
            s = c["share"]
            rows.append((fid, VERSION, F.now(), c["hash"], c["critic_score"], c["critic_src"], c["audience_score"],
                         c["audience_src"], c["gap"], c["gap_label"], c["polar"], c["bc"], c["ml_sd"], c["ml_n"],
                         c["trend"], c["trend_delta"], c["legacy"], c["n_critic"], c["critic_pos"], c["n_aud"],
                         s["positive"], s["negative"], s["mixed"], c["bombing"], json.dumps(c["data"]), c["text"]))
        if len(rows) >= 2000 or i == len(fids):
            conn.executemany("insert or replace into reception_card values (" + ",".join("?" * 26) + ")", rows)
            conn.commit()
            rows = []
            print(f"  {i:,}/{len(fids):,} ({time.time() - t0:.0f}s)", flush=True)
    F.set_meta(conn, "reception_cards_at", F.now())
    conn.commit()
    cmd_stats(a, conn)


def cmd_stats(a, conn):
    one = lambda sql: (q1(conn, sql) or [0])[0] or 0  # noqa: E731
    print(f"reception cards: {one('select count(*) from reception_card'):,}; with a critic-audience gap "
          f"{one('select count(*) from reception_card where gap is not null'):,} "
          f"(critics warmer by >= {GAP_DISAGREE}: {one(f'select count(*) from reception_card where gap >= {GAP_DISAGREE}'):,}, "
          f"audiences warmer: {one(f'select count(*) from reception_card where gap <= -{GAP_DISAGREE}'):,})")
    print(f"  divisive {one(chr(39).join(['select count(*) from reception_card where polar_label like ', 'divisive%', '']))}, "
          f"rose over time {one(chr(39).join(['select count(*) from reception_card where trend_label = ', 'rose', '']))}, "
          f"fell {one(chr(39).join(['select count(*) from reception_card where trend_label = ', 'fell', '']))}, "
          f"cult / reappraised (Wikipedia) {one('select count(*) from reception_card where legacy_label is not null')}, "
          f"review-bombed {one('select count(*) from reception_card where bombing > 0')}")
    print(f"  with a level-3 summary: {one(chr(39).join(['select count(distinct tmdb_id) from summaries where kind = ', 'reception', ' and level = ', '3', '']))}")


def cmd_show(a, conn):
    r = q1(conn, "select tmdb_id from films where title = ? collate nocase order by vote_count desc limit 1", a.title)
    if not r:
        sys.exit("no such film")
    c = build_card(conn, r[0], _usable())
    print(c["text"] if c else "(no reception data for this film)")


def cmd_check(a, conn):
    ids = [r[0] for r in q(conn, "select tmdb_id from reception_card where n_critic_stmts >= 3 or n_aud_reviews >= 10")]
    random.seed(a.seed)
    pick = random.sample(ids, min(a.n, len(ids)))
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "reception_card_check.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Reception cards to check by eye ({len(pick)})\n\nFor each: is every line right for this film, and "
                f"would you trust the labels (gap, divisive, rose / fell)? Note anything wrong under the card.\n\n")
        for fid in pick:
            t = (q1(conn, "select text from reception_card where tmdb_id = ?", fid) or [""])[0]
            f.write("```\n" + t + "\n```\nOK? \n\n")
    print(f"{len(pick)} cards -> {path}")


def cmd_gaps(a, conn):
    order = "desc" if a.side == "critics" else "asc"
    cond = f"gap >= {GAP_DISAGREE}" if a.side == "critics" else f"gap <= -{GAP_DISAGREE}"
    rows = q(conn, f"""select f.title, f.year, r.critic_score, r.critic_score_src, r.audience_score, r.audience_score_src,
        r.gap from reception_card r join films f on f.tmdb_id = r.tmdb_id where {cond} and coalesce(f.vote_count, 0) >= ?
        order by r.gap {order} limit ?""", a.min_votes, a.limit)
    print(f"{'Critics' if a.side == 'critics' else 'Audiences'} liked these much more (gap on a 0-100 scale; films with "
          f">= {a.min_votes} TMDB votes):")
    for t, y, c, cs, au, as_, g in rows:
        print(f"  {t} ({y}): critics {c:.0f} ({cs}) vs audiences {au:.0f} ({as_}), gap {g:+.0f}")


def gap_block(question, side=None, limit=10, genre=None, year_from=None, year_to=None, conn=None):
    """E4 for tools.py: the ranked list as a tagged block, or None."""
    conn = conn or connect(readonly=True)
    if conn is None:
        return None
    ql = question or ""
    m_a = re.search(r"\b(audiences?|fans|viewers|people|moviegoers)\b.{0,40}\b(lov(e|ed)|lik(e|ed)|enjoy(ed)?|more)\b", ql, re.I)
    m_c = re.search(r"\bcritics?\b.{0,40}\b(lov(e|ed)|lik(e|ed)|prais\w*|more)\b", ql, re.I)
    if side is None:
        side = ("audiences" if m_a and (not m_c or m_a.start() < m_c.start()) else "critics" if m_c else "both")
    where, args = ["coalesce(f.vote_count, 0) >= 200"], []
    if genre:
        where.append("f.genres_json like ?")
        args.append(f'%"{genre}"%')
    if year_from:
        where.append("f.year >= ?")
        args.append(int(year_from))
    if year_to:
        where.append("f.year <= ?")
        args.append(int(year_to))
    out = []
    for sd in (("critics", "audiences") if side == "both" else (side,)):
        cond = f"r.gap >= {GAP_DISAGREE}" if sd == "critics" else f"r.gap <= -{GAP_DISAGREE}"
        rows = q(conn, f"""select f.title, f.year, r.critic_score, r.critic_score_src, r.audience_score, r.audience_score_src,
            r.gap from reception_card r join films f on f.tmdb_id = r.tmdb_id where {cond} and {' and '.join(where)}
            order by abs(r.gap) desc limit ?""", *args, limit)
        if rows:
            out.append(f"{'Critics' if sd == 'critics' else 'Audiences'} liked these much more than "
                       f"{'audiences' if sd == 'critics' else 'critics'} (0-100 scale; critics = Metacritic, else Rotten "
                       f"Tomatoes; audiences = IMDb, else MovieLens): title | year | critics | audiences | gap")
            out += [f"{t} | {y} | {c:.0f} ({cs}) | {au:.0f} ({as_}) | {g:+.0f}" for t, y, c, cs, au, as_, g in rows]
    if not out:
        return None
    return "\n".join(out) + "\n[Wikipedia / Wikidata / OMDb critics' scores; IMDb / MovieLens audiences; computed by Movie Buff]"


def cmd_divisive(a, conn):
    rows = q(conn, """select f.title, f.year, d.n, d.mean, d.sd, d.pct_low, d.pct_high, d.bc from ml_dist d
        join films f on f.tmdb_id = d.tmdb_id where d.n >= ? and d.pct_low >= 0.15 and d.pct_high >= 0.15
        order by d.bc desc limit ?""", max(MIN_ML, a.min_n), a.limit)
    print("Most polarised (MovieLens 32M; bimodality > 0.555 suggests two camps):")
    for t, y, n, m, sd, lo, hi, bc in rows:
        print(f"  {t} ({y}): n {n:,}, mean {m:.2f}, sd {sd:.2f}, {lo:.0%} low / {hi:.0%} high, bc {bc:.2f}")


def cmd_reappraised(a, conn):
    rows = q(conn, """select f.title, f.year, r.trend_label, r.trend_delta, r.legacy_label from reception_card r
        join films f on f.tmdb_id = r.tmdb_id where r.trend_label in ('rose', 'fell') or r.legacy_label is not null
        order by coalesce(r.trend_delta, 0) desc limit ?""", a.limit)
    for t, y, tl, td, lg in rows:
        print(f"  {t} ({y}): MovieLens {tl or '-'} {'' if td is None else f'{td:+.2f}'}; Wikipedia: {lg or '-'}")


# ------------------------------------------------------------------ R13 summaries
SUM_SYSTEM = ("You summarise what is in the notes below about how a film was received. Use ONLY the notes: no facts, "
              "names, numbers or opinions that are not in them. Copy each number exactly as the notes give it, with its "
              "unit and its source. A Metacritic score is out of 100 (write it as 'a Metacritic score of 64 out of 100'), "
              "never a percentage; a Rotten Tomatoes percentage is the share of positive reviews. Keep critics and "
              "audiences apart: say what audiences thought only from audience sources in the notes, and draw no "
              "conclusion from a source the notes call too few to generalise. Do not make praise or criticism stronger "
              "than the notes do. Write plain sentences, no headings or lists. If the notes are thin, say so in a few "
              "words.")


def ollama(prompt, n_pred=220):
    body = json.dumps({"model": SUM_MODEL, "stream": False, "keep_alive": "30m",
                       "options": {"temperature": 0, "num_predict": n_pred},
                       "messages": [{"role": "system", "content": SUM_SYSTEM}, {"role": "user", "content": prompt}]}).encode()
    for attempt in range(2):
        try:
            req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return " ".join(json.load(r)["message"]["content"].split())
        except Exception as e:  # noqa: BLE001
            print(f"  retry: {str(e)[:100]}", file=sys.stderr)
            time.sleep(3)
    return None


def _store_summary(conn, fid, level, source, inputs, text):
    key = f"rc|{level}|{source}|{fid}|{hashlib.sha1(inputs.encode()).hexdigest()[:12]}|{VERSION}{SUM_REV}"
    conn.execute("delete from summaries where tmdb_id = ? and kind = 'reception' and level = ? and key like ?",
                 (fid, str(level), f"rc|{level}|{source}|%"))
    conn.execute("insert or replace into summaries values (?,?,?,?,?,?,?,?,?)",
                 (key, fid, "reception", str(level), SUM_MODEL, VERSION, text, json.dumps({"source": source,
                                                                                           "inputs": inputs[:6000]}),
                  F.now()))


def _have(conn, fid, level, source, inputs):
    key = f"rc|{level}|{source}|{fid}|{hashlib.sha1(inputs.encode()).hexdigest()[:12]}|{VERSION}{SUM_REV}"
    r = q1(conn, "select text from summaries where key = ?", key)
    return r[0] if r else None


def summary_inputs(conn, fid, usable=None):
    """{'critics': text, 'audience': text} - the notes each level-2 summary is written from."""
    out = {}
    st = critic_statements(conn, fid)
    if st:
        out["critics"] = "\n".join(f"- {r[4]}" + (f" [{r[2]}" + (f", {r[3]}" if r[3] else "") + "]" if r[2] else "")
                                   for r in st[:14])
    n, share, excl = audience(conn, fid, usable)
    asp = aspects(conn, fid).get("audience") or []
    if n or asp:
        lines = [f"- {n} ordinary viewers' reviews analysed: {share['positive']:.0%} positive, {share['mixed']:.0%} "
                 f"mixed, {share['negative']:.0%} negative" + (" (a small sample)" if n < MIN_AUDIENCE_REVIEWS else "")]
        for a, pos, neg, mixed, nn, quotes in sorted(asp, key=lambda r: -(r[1] + r[2]))[:6]:
            lines.append(f"- {a}: {pos} praised, {neg} criticised" + (f' (e.g. "{quotes[0]}")' if quotes else ""))
        if excl.get("burst"):
            lines.append(f"- {excl['burst']} reviews from a review-bombing burst were left out")
        out["audience"] = "\n".join(lines)
    return out


def cmd_summaries(a, conn):
    usable = _usable()
    fids = [r[0] for r in q(conn, """select r.tmdb_id from reception_card r join films f on f.tmdb_id = r.tmdb_id
        order by (r.n_aud_reviews + 3 * r.n_critic_stmts) desc, f.vote_count desc limit ?""", a.films)]
    print(f"summaries: {len(fids):,} films (model {SUM_MODEL}, version {VERSION})", flush=True)
    t0, made, kept = time.time(), 0, 0
    for i, fid in enumerate(fids, 1):
        title = (q1(conn, "select title || ' (' || coalesce(year, '?') || ')' from films where tmdb_id = ?", fid) or ["?"])[0]
        ins = summary_inputs(conn, fid, usable)
        l2 = {}
        for src, notes in ins.items():
            have = _have(conn, fid, 2, src, notes)
            if have:
                l2[src] = have
                kept += 1
                continue
            who = "critics (from Wikipedia's reception text)" if src == "critics" else "ordinary viewers (TMDB user reviews)"
            t = ollama(f"Film: {title}\nNotes on what {who} said:\n{notes}\n\nIn 2-3 sentences, what did {who} say?")
            if t:
                _store_summary(conn, fid, 2, src, notes, t)
                l2[src] = t
                made += 1
        card = q1(conn, "select text from reception_card where tmdb_id = ?", fid)
        if card and (l2 or card[0].count("\n") >= 3):
            base = re.sub(r"\nSummary \(Movie Buff.*$", "", card[0], flags=re.S)
            notes = base + "".join(f"\nWhat {s} said (summary): {t}" for s, t in l2.items())
            if not _have(conn, fid, 3, "film", notes):
                t = ollama(f"{notes}\n\nIn 3-4 sentences, how was {title} received? Keep critics and audiences apart and "
                           f"give the numbers that matter with their sample sizes.", n_pred=260)
                if t:
                    _store_summary(conn, fid, 3, "film", notes, t)
                    made += 1
            else:
                kept += 1
        if i % 25 == 0 or i == len(fids):
            conn.commit()
            rate = i / max(time.time() - t0, 1e-6)
            print(f"  {i:,}/{len(fids):,} films; {made:,} summaries written, {kept:,} unchanged  "
                  f"eta {(len(fids) - i) / max(rate, 1e-6) / 60:.0f} min", flush=True)
    conn.commit()
    F.set_meta(conn, "reception_summaries_at", F.now())
    conn.commit()
    print("  (run `python reception_cards.py build` again so the cards carry the new summaries)")


def cmd_mccheck(a, conn):
    """28 Oct: every Metacritic number from Wikipedia text next to OMDb and Wikidata; disagreements and the films
    named on the command line -> results/mc_check.txt."""
    names = [x.strip().lower() for x in (a.arg or "").split("|") if x.strip()]
    has_wd = bool(q(conn, "select 1 from sqlite_master where name = 'wikidata_scores'"))
    wd = ("(select w.value from wikidata_scores w where w.tmdb_id = f.tmdb_id and w.source = 'Metacritic' and "
          "w.scale = 100 limit 1)") if has_wd else "null"
    rows = q(conn, f"""select f.tmdb_id, f.title, f.year, s.mc_score, s.mc_n, s.mc_text, s.rt_pct, o.mc_score, {wd}
        from critic_scores s join films f on f.tmdb_id = s.tmdb_id
        left join omdb o on o.tmdb_id = s.tmdb_id and o.status = 'ok' where s.mc_score is not null""")
    out, bad, odd = [], 0, 0
    for fid, t, y, mc, n, txt, rt, om, wd in rows:
        other = om if om is not None else wd
        dis = other is not None and not mc_agrees(mc, other)
        susp = not mc_plausible(mc, txt, (rt, None))
        bad += dis
        odd += susp
        if dis or susp or (t or "").lower() in names:
            out.append(f"{t} ({y}) id {fid}: text {mc:g} (n {n}), OMDb {om}, Wikidata {wd}, RT {rt}"
                       f"{'  DISAGREE' if dis else ''}{'  SUSPECT' if susp else ''}\n    text: {(txt or '')[:200]}")
    head = (f"Metacritic from Wikipedia text: {len(rows):,} films; disagree with OMDb/Wikidata by > 10: {bad:,}; "
            f"suspect (not 'N out of 100' and contradicts RT): {odd:,}")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    open(os.path.join(HERE, "results", "mc_check.txt"), "w", encoding="utf-8").write(head + "\n" + "\n".join(out) + "\n")
    print(head)
    print("\n".join(out[:25]))
    print("(all in results/mc_check.txt)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "summaries", "show", "check", "gaps", "divisive", "reappraised", "stats", "mccheck"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--facts")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--films", type=int, default=3000)
    ap.add_argument("--side", choices=["critics", "audiences"], default="critics")
    ap.add_argument("--min-votes", type=int, default=500)
    ap.add_argument("--min-n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    conn = connect(a.facts)
    if a.cmd in ("gaps", "divisive", "reappraised") and a.limit is None:
        a.limit = 20
    if a.cmd == "show":
        a.title = a.arg
    if a.cmd == "check":
        a.n = int(a.arg or 20)
    {"build": cmd_build, "summaries": cmd_summaries, "show": cmd_show, "check": cmd_check, "gaps": cmd_gaps,
     "divisive": cmd_divisive, "reappraised": cmd_reappraised, "stats": cmd_stats, "mccheck": cmd_mccheck}[a.cmd](a, conn)


if __name__ == "__main__":
    main()
