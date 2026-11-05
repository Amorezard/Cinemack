#!/usr/bin/env python3
"""
v5 (15 Oct evening): more usable money figures, today's dollars, and critics' scores as numbers.

What was already there (v3): budget and box office from TMDB, the Wikipedia infobox and Wikidata, one row
per source in facts.facts, merged into the `money` table by `python tools.py build`. That merge keeps only
US-dollar amounts, so a British film whose infobox says "£3 million", or an Indian film's "₹150 crore", had
no usable budget. This script adds:

  1. rates       yearly exchange rates (FRED, Federal Reserve) and the US consumer price index (CPI-U,
                 FRED CPIAUCNS) into facts.db: fx_annual, cpi_annual. Before floating rates (1971) the
                 fixed parities are used for GBP, JPY and INR only.
  2. currencies  Wikidata's currency items -> ISO codes (Wikidata P498), so Wikidata amounts in pounds,
                 euros, yen ... can be converted too.
  3. convert     every non-USD budget / box office row -> a new USD row at the release year's average rate,
                 source '<source>_fx' (e.g. wikipedia_infobox_fx). The originals stay; tools.py build
                 prefers any real US-dollar figure and uses a converted one only when there is none.
  4. critics     Rotten Tomatoes %, review count and average, Metacritic score and count, CinemaScore grade,
                 read from each film's Wikipedia Reception passages -> critic_scores (as of the page's
                 snapshot: 2023 for the catalogue, the fetch day for recent films).
  5. money       python tools.py build (rebuilds `money` with the converted rows)
  6. real        money_real: budget and gross in today's dollars (CPI of the latest complete year)
  7. report      results/money_extra_summary.txt: films with budget AND gross before/after, and a sample of
                 conversions to check by eye

    python money_extra.py all            # 1-7 (network: FRED + Wikidata, a few minutes; no GPU)
    python money_extra.py rates | currencies | convert | critics | money | real | report
    python money_extra.py rates --fx-csv fx.csv --cpi-csv cpi.csv    # offline: your own rate files
    python money_extra.py remove         # take everything above out again, then rebuild money
    python money_extra.py show "Slumdog Millionaire"
    python money_extra.py diagnose       # v7 H2: rate coverage per currency + the last convert run's skip reasons

Nothing here changes an answer by itself: converted figures reach answers through the money table (as
"[Wikipedia infobox, converted to USD]"); today's-dollar lines and critic scores only with
MONEY_REAL=1 / CRITIC_SCORES=1 in tools.py; the performance model (performance.py) reads all three.
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_db as F  # noqa: E402

UA = "MovieBuff/5 (student research project; Merrimack College) python-urllib"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
WIKIDATA = "https://query.wikidata.org/sparql"

DDL = """
create table if not exists fx_annual(currency text, year integer, usd_per_unit real, series text,
  primary key (currency, year));
create table if not exists cpi_annual(year integer primary key, cpi real, series text);
create table if not exists currency_qid(qid text primary key, code text, label text);
create table if not exists critic_scores(tmdb_id integer primary key, rt_pct real, rt_n integer, rt_avg real,
  mc_score real, mc_n integer, cinemascore text, cinemascore_num integer, snapshot text, url text,
  rt_text text, mc_text text, cs_text text, parsed_at text);
create table if not exists money_real(tmdb_id integer primary key, year integer, base_year integer,
  budget_real real, gross_real real);
"""

# FRED series per currency, tried in order (A = yearly average, EX = monthly, DEX = daily; a yearly mean is
# taken from whatever comes back). "EXUS.." series are US dollars per unit, the others units per US dollar.
FX_SERIES = {
    "GBP": ["AEXUSUK", "EXUSUK", "DEXUSUK"], "EUR": ["AEXUSEU", "EXUSEU", "DEXUSEU"],
    "JPY": ["AEXJPUS", "EXJPUS", "DEXJPUS"], "INR": ["AEXINUS", "EXINUS", "DEXINUS"],
    "CAD": ["AEXCAUS", "EXCAUS", "DEXCAUS"], "AUD": ["AEXUSAL", "EXUSAL", "DEXUSAL"],
    "HKD": ["AEXHKUS", "EXHKUS", "DEXHKUS"], "NZD": ["AEXUSNZ", "EXUSNZ", "DEXUSNZ"],
    "CNY": ["AEXCHUS", "EXCHUS", "DEXCHUS"], "KRW": ["AEXKOUS", "EXKOUS", "DEXKOUS"],
    "CHF": ["AEXSZUS", "EXSZUS", "DEXSZUS"], "SEK": ["AEXSDUS", "EXSDUS", "DEXSDUS"],
    "DKK": ["AEXDNUS", "EXDNUS", "DEXDNUS"], "NOK": ["AEXNOUS", "EXNOUS", "DEXNOUS"],
    "MXN": ["AEXMXUS", "EXMXUS", "DEXMXUS"], "BRL": ["AEXBZUS", "EXBZUS", "DEXBZUS"],
    "THB": ["AEXTHUS", "EXTHUS", "DEXTHUS"], "TWD": ["AEXTAUS", "EXTAUS", "DEXTAUS"],
    "SGD": ["AEXSIUS", "EXSIUS", "DEXSIUS"], "ZAR": ["AEXSFUS", "EXSFUS", "DEXSFUS"],
    # currencies the euro replaced (FRED stops in 1998/2001)
    "FRF": ["EXFRUS"], "DEM": ["EXGEUS"], "ITL": ["EXITUS"], "ESP": ["EXSPUS"], "NLG": ["EXNEUS"],
}
CPI_SERIES = "CPIAUCNS"
# 15 Oct 20:40: FRED timed out from the server, so the World Bank API comes first (yearly averages, 1960 on):
# PA.NUS.FCRF = official exchange rate, local currency units per US dollar; FP.CPI.TOTL = consumer prices (US).
WB = "https://api.worldbank.org/v2/country/{c}/indicator/{i}?format=json&per_page=200"
WB_COUNTRY = {"GBP": "GBR", "EUR": "EMU", "JPY": "JPN", "INR": "IND", "CAD": "CAN", "AUD": "AUS", "HKD": "HKG",
              "NZD": "NZL", "CNY": "CHN", "KRW": "KOR", "CHF": "CHE", "SEK": "SWE", "DKK": "DNK", "NOK": "NOR",
              "MXN": "MEX", "BRL": "BRA", "THB": "THA", "SGD": "SGP", "ZAR": "ZAF", "RUB": "RUS", "TRY": "TUR",
              "PLN": "POL", "CZK": "CZE", "HUF": "HUN", "ILS": "ISR", "IDR": "IDN", "PHP": "PHL", "MYR": "MYS",
              "ARS": "ARG", "CLP": "CHL", "COP": "COL", "EGP": "EGY", "NGN": "NGA", "PKR": "PAK", "BDT": "BGD",
              "IRR": "IRN", "ISK": "ISL"}


def wb_series(country, indicator):
    """{year: value} from the World Bank API, or {} if unreachable."""
    txt = _get(WB.format(c=country, i=indicator), tries=2, timeout=30)
    try:
        d = json.loads(txt or "[]")
        return {int(x["date"]): float(x["value"]) for x in (d[1] if len(d) > 1 and d[1] else [])
                if x.get("value") is not None and str(x.get("date", "")).isdigit()}
    except (ValueError, TypeError, KeyError):
        return {}          # CPI-U, all items, US city average, not seasonally adjusted, from 1913

# Fixed parities before floating rates (US dollars per unit), only where they are well documented.
FIXED = {
    "GBP": [(1940, 1949, 4.03), (1950, 1967, 2.80), (1968, 1971, 2.40)],
    "JPY": [(1949, 1971, 1 / 360)],
    "INR": [(1949, 1965, 1 / 4.76), (1966, 1971, 1 / 7.50)],
}

# v7 H2 (27 Oct): the euro's irrevocable conversion rates (units per euro). The World Bank reports France, Germany ...
# in euros for every year (old francs / marks converted at these rates), so the old currency's yearly rate is
# recovered as WB value x this rate. Used when FRED (the 15 Oct source of these rates) does not answer.
EURO_LEGACY = {"FRF": ("FRA", 6.55957), "DEM": ("DEU", 1.95583), "ITL": ("ITA", 1936.27), "ESP": ("ESP", 166.386),
               "NLG": ("NLD", 2.20371), "BEF": ("BEL", 40.3399), "ATS": ("AUT", 13.7603), "PTE": ("PRT", 200.482),
               "FIM": ("FIN", 5.94573), "IEP": ("IRL", 0.787564), "GRD": ("GRC", 340.750)}
NEAREST_YEARS = 2          # convert(): a missing year may use the nearest year within this many years (noted)

# Wikidata currency items -> ISO codes, used only if the P498 lookup fails.
QID_FALLBACK = {"Q4917": "USD", "Q4916": "EUR", "Q25224": "GBP", "Q8146": "JPY", "Q80524": "INR",
                "Q1104069": "CAD", "Q259502": "AUD", "Q31015": "HKD", "Q1472704": "NZD", "Q39099": "CNY",
                "Q202040": "KRW", "Q25344": "CHF", "Q122922": "SEK", "Q25417": "DKK", "Q132643": "NOK",
                "Q4730": "MXN", "Q173117": "BRL", "Q177882": "THB", "Q208526": "TWD", "Q190951": "SGD",
                "Q181907": "ZAR", "Q4192": "FRF", "Q106720": "DEM", "Q193094": "ITL", "Q61217": "ESP"}

# Converted figures above these are almost certainly parse errors (a figure in the wrong unit).
MAX_BUDGET, MAX_GROSS = 6e8, 3.5e9


def conn():
    c = F.connect()
    c.executescript(DDL)
    return c


def _get(url, data=None, headers=None, tries=4, timeout=120):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"  {str(e)[:90]} ({url[:80]})", file=sys.stderr)
            if "404" in str(e) or "400" in str(e):
                return None
            time.sleep(5 * (i + 1))
    return None


def yearly_mean(csv_text):
    """FRED csv (date column + value column, '.' for missing) -> {year: mean}."""
    out = {}
    rd = csv.reader(io.StringIO(csv_text))
    head = next(rd, None)
    if not head or len(head) < 2:
        return {}
    for row in rd:
        if len(row) < 2 or not row[0][:4].isdigit():
            continue
        try:
            v = float(row[1])
        except ValueError:
            continue
        out.setdefault(int(row[0][:4]), []).append(v)
    return {y: sum(v) / len(v) for y, v in out.items()}


def usd_per_unit_series(sid):
    return sid.replace("AEX", "EX", 1).replace("DEX", "EX", 1).startswith("EXUS")


# ------------------------------------------------------------------ 1. rates
def cmd_rates(a):
    c = conn()
    if a.cpi_csv:
        cpi = yearly_mean(open(a.cpi_csv).read())
        src = os.path.basename(a.cpi_csv)
    else:
        cpi, src = wb_series("USA", "FP.CPI.TOTL"), "World Bank FP.CPI.TOTL"
    if not cpi and not a.cpi_csv:
        txt = _get(FRED + CPI_SERIES, tries=1, timeout=20)
        cpi, src = (yearly_mean(txt) if txt else {}), CPI_SERIES
        if txt and src == CPI_SERIES:   # only complete years
            months = {}
            for row in csv.reader(io.StringIO(txt)):
                if row and row[0][:4].isdigit() and len(row) > 1 and row[1] not in (".", ""):
                    months[int(row[0][:4])] = months.get(int(row[0][:4]), 0) + 1
            cpi = {y: v for y, v in cpi.items() if months.get(y, 0) == 12}
    if cpi:
        c.execute("delete from cpi_annual")
        c.executemany("insert into cpi_annual values (?,?,?)", [(y, v, src) for y, v in sorted(cpi.items())])
        print(f"CPI: {min(cpi)}-{max(cpi)} ({len(cpi)} complete years) from {src}")
    else:
        print("CPI: not loaded (World Bank and FRED unreachable? use --cpi-csv)")
    n_cur = 0
    rows = []
    if a.fx_csv:      # columns: currency, year, usd_per_unit
        for r in csv.DictReader(open(a.fx_csv)):
            rows.append((r["currency"].upper(), int(r["year"]), float(r["usd_per_unit"]), "file"))
    else:
        for cur, country in WB_COUNTRY.items():
            ym = wb_series(country, "PA.NUS.FCRF")
            if ym:
                rows += [(cur, y, 1 / v, f"World Bank {country}") for y, v in ym.items() if v > 0]
                print(f"  {cur}: World Bank {country} {min(ym)}-{max(ym)}", flush=True)
            time.sleep(0.3)
        for cur, (country, per_eur) in EURO_LEGACY.items():   # v7 H2: old European currencies without FRED
            ym = wb_series(country, "PA.NUS.FCRF")
            if ym:
                rows += [(cur, y, 1 / (v * per_eur), f"World Bank {country} x euro rate") for y, v in ym.items()
                         if v > 0 and y <= 2001]
                print(f"  {cur}: World Bank {country} (euro-converted) {min(ym)}-{min(max(ym), 2001)}", flush=True)
            time.sleep(0.3)
        have_cy = {(r[0], r[1]) for r in rows}
        fred_ok = _get(FRED + "AEXUSUK", tries=2, timeout=40) is not None
        if not fred_ok:
            print("  FRED did not answer: currency-years only FRED has (before 1960, some currencies) stay missing")
        for cur, series in FX_SERIES.items():
            if not fred_ok:
                continue
            # v7 H2: before, a currency the World Bank had skipped FRED entirely, so its years before 1960 had no rate
            if all((cur, y) in have_cy for y in range(1950, datetime.date.today().year)):
                continue
            for sid in series:
                txt = _get(FRED + sid, tries=1, timeout=20)
                ym = yearly_mean(txt) if txt else {}
                if ym:
                    direct = usd_per_unit_series(sid)
                    rows += [(cur, y, (v if direct else 1 / v), sid) for y, v in ym.items() if v > 0
                             and (cur, y) not in have_cy]
                    n_cur += 1
                    print(f"  {cur}: {sid} {min(ym)}-{max(ym)}", flush=True)
                    break
                time.sleep(0.5)
            else:
                print(f"  {cur}: no FRED series answered")
    have = {(r[0], r[1]) for r in rows}
    for cur, spans in FIXED.items():
        for lo, hi, v in spans:
            rows += [(cur, y, v, "fixed parity") for y in range(lo, hi + 1) if (cur, y) not in have]
    if rows:
        c.execute("delete from fx_annual")
        c.executemany("insert or replace into fx_annual values (?,?,?,?)", rows)
    c.commit()
    print(f"exchange rates: {len(rows):,} currency-years for {len({r[0] for r in rows})} currencies")


# ------------------------------------------------------------------ 2. currencies
def cmd_currencies(_a):
    c = conn()
    qids = sorted({r[0] for r in c.execute("select distinct currency from facts where source = 'wikidata' "
                                           "and currency like 'Q%'")})
    print(f"Wikidata currency items in facts: {len(qids)}")
    found = {}
    for s in range(0, len(qids), 200):
        part = qids[s:s + 200]
        q = ("SELECT ?u ?code ?uLabel WHERE { VALUES ?u { %s } OPTIONAL { ?u wdt:P498 ?code } "
             "SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } }" % " ".join(f"wd:{x}" for x in part))
        txt = _get(WIKIDATA, data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
                   headers={"Accept": "application/sparql-results+json"})
        try:
            for b in json.loads(txt or "{}").get("results", {}).get("bindings", []):
                u = b["u"]["value"].rsplit("/", 1)[-1]
                found[u] = (b.get("code", {}).get("value"), b.get("uLabel", {}).get("value"))
        except ValueError:
            pass
        time.sleep(1)
    rows = []
    for qid in qids:
        code, label = found.get(qid, (None, None))
        code = code or QID_FALLBACK.get(qid)
        rows.append((qid, code, label))
    c.executemany("insert or replace into currency_qid values (?,?,?)", rows)
    c.commit()
    print(f"resolved to an ISO code: {sum(1 for r in rows if r[1])}/{len(rows)}")


# ------------------------------------------------------------------ 3. convert
def rate_table(c):
    fx = {}
    for cur, y, v in c.execute("select currency, year, usd_per_unit from fx_annual"):
        fx[(cur, y)] = v
    return fx


_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)


def comment_only(text):
    """True when the text has an HTML comment and no digit is left once comments are removed."""
    t = text or ""
    return "<!--" in t and not re.search(r"\d", _COMMENT.sub("", t))


def clean_amount(vt):
    """v7 H2: the infobox text behind a figure, without <ref> citations, templates' markup or comments (the 21 Oct
    eye-check printed raw '<ref>...' wikitext)."""
    t = re.sub(r"<!--.*?(-->|$)", "", str(vt or ""), flags=re.S)
    t = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S | re.I)
    t = re.sub(r"\{\{\s*(?:efn|sfn|refn|citation needed|cn|r)\b[^{}]*\}\}", "", t, flags=re.I)
    t = re.sub(r"\{\{[^{}|]*\|([^{}]*)\}\}", lambda m: m.group(1).replace("|", " "), t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(t.split())[:120]


def cmd_diagnose(_a):
    """v7 H2: why conversions fell (1,185 -> 985 films on 21 Oct): rate coverage per currency and the skip reasons of
    the last convert run, with the films converted per source."""
    c = conn()
    print("exchange-rate coverage (currency: years, first-last, source):")
    for cur, n, lo, hi, src in c.execute("select currency, count(*), min(year), max(year), group_concat(distinct series) "
                                         "from fx_annual group by currency order by currency"):
        print(f"  {cur}: {n} years {lo}-{hi} ({(src or '')[:60]})")
    need = c.execute("""select x.currency, count(*) from facts x where x.field in ('budget','box_office','revenue')
        and x.currency is not null and x.currency != 'USD' and x.source in ('wikipedia_infobox','wikidata')
        group by x.currency order by 2 desc limit 15""").fetchall()
    print("non-USD figures by currency (top 15): " + ", ".join(f"{c_} {n:,}" for c_, n in need))
    last = F.get_meta(c, "money_fx_last_run")
    print(f"last convert run: {last}")
    for src, n in c.execute("select source, count(distinct tmdb_id) from facts where source like '%\\_fx' escape '\\' "
                            "group by source"):
        print(f"  converted from {src}: {n:,} films")


def cmd_convert(_a):
    c = conn()
    fx = rate_table(c)
    if not fx:
        raise SystemExit("no exchange rates yet: python money_extra.py rates")
    qmap = {q: code for q, code in c.execute("select qid, code from currency_qid") if code}
    years = {fid: y for fid, y in c.execute("select tmdb_id, year from films")}
    c.execute("delete from facts where source like '%\\_fx' escape '\\'")
    rows, skipped = [], {}
    for fid, field, v, vt, cur, src, ref, as_of, rel in c.execute(
            "select tmdb_id, field, value_num, value_text, currency, source, source_ref, as_of, reliability "
            "from facts where field in ('budget','box_office','revenue') and currency is not null and currency != 'USD' "
            "and source in ('wikipedia_infobox','wikidata') and value_num > 0").fetchall():
        code = qmap.get(cur, cur) if (cur or "").startswith("Q") else cur
        y = years.get(fid)
        if comment_only(vt):
            # 16 Oct: an empty infobox field holding only the editors' hint
            # <!-- (please use ... e.g. "£11.6 million" ...) --> was read as a real 11.6M GBP figure
            # (Contracted: Phase II, box office $17.7M). The number exists only inside the comment.
            skipped["number only inside an HTML comment"] = skipped.get("number only inside an HTML comment", 0) + 1
            continue
        if not code or not y:
            skipped["no currency code or year"] = skipped.get("no currency code or year", 0) + 1
            continue
        r = fx.get((code, y))
        near = None
        if r is None:                                  # v7 H2: the nearest year within NEAREST_YEARS, said in the note
            for dy in range(1, NEAREST_YEARS + 1):
                for yy in (y - dy, y + dy):
                    if r is None and fx.get((code, yy)) is not None:
                        r, near = fx[(code, yy)], yy
        if r is None:
            skipped[f"no {code} rate for the year"] = skipped.get(f"no {code} rate for the year", 0) + 1
            continue
        usd = v * r
        if usd > (MAX_BUDGET if field == "budget" else MAX_GROSS) or usd < 1e4:
            skipped["implausible after conversion"] = skipped.get("implausible after conversion", 0) + 1
            continue
        note = (f"{clean_amount(vt)} = {v:,.0f} {code} at the {near or y} average rate ({r:.4g} USD per {code})"
                + (f"; no {y} rate, nearest year used" if near else "")).strip(" =")
        rows.append((fid, field, usd, note[:300], "USD", f"{src}_fx", ref, as_of, F.now(), (rel or 0.7) - 0.15))
    c.executemany("insert into facts values (?,?,?,?,?,?,?,?,?,?)", rows)
    F.set_meta(c, "money_fx_converted_at", F.now())
    F.set_meta(c, "money_fx_last_run", json.dumps({"converted_rows": len(rows), "films": len({r[0] for r in rows}),
                                                   "skipped": skipped, "at": F.now()}))
    c.commit()
    print(f"converted to USD: {len(rows):,} figures for {len({r[0] for r in rows}):,} films")
    for k, n in sorted(skipped.items(), key=lambda x: -x[1])[:12]:
        print(f"  not converted ({k}): {n:,}")


# ------------------------------------------------------------------ 4. critics
_PCT = re.compile(r"(\d{1,3}(?:\.\d)?)\s?%")
_RT_N = re.compile(r"(?:based on|of|from|among|out of)\s+(\d[\d,]{0,6})\s+(?:\w+\s){0,2}?(?:critics|reviews|critic)", re.I)
_AVG10 = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s?/\s?10\b|(\d{1,2}(?:\.\d{1,2})?) out of 10\b")
# 28 Oct: the old pattern read "a score of 1-100" as 1 (Frida), and the Rotten Tomatoes "average rating of 9.30/10"
# after a Metacritic sentence as 9 (Lawrence of Arabia, The White Ribbon, Three Colors: Red). A Metacritic number must
# not be a range start, a decimal, or out of 10 / a percentage, and the search stops at the next aggregator's name.
_MC = re.compile(r"(?:score|scored|average|rating|metascore|perfect)\s+(?:rating\s+|score\s+)?(?:of\s+)?(\d{1,3})"
                 r"(?![\d.,]?\d|\s*(?:[–\-−]|to)\s*\d|\s*/\s*(?!100\b)\d|\s*out of (?!100\b)\d|\s*%|\s*stars?\b)",
                 re.I)   # 28 Oct: also "scored 84 out of 100"; not "a perfect 4/4 star rating" (My Winnipeg)
_WORDNUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_MC_N = re.compile(r"based on\s+(\d[\d,]{0,5}|" + "|".join(_WORDNUM) + r")\s+(?:\w+\s){0,2}?(?:critics|reviews|critic)",
                   re.I)
_OTHER_AGG = re.compile(r"Rotten Tomatoes|CinemaScore|IMDb|Letterboxd|PostTrak")


def _upto_next(w, own):
    """The window w cut at the next aggregator other than `own`."""
    m = [x for x in _OTHER_AGG.finditer(w) if x.group(0) != own]
    return w[: m[0].start()] if m else w


def _count(x):
    x = x.lower().replace(",", "")
    return _WORDNUM[x] if x in _WORDNUM else int(x)
_CS = re.compile(r"(?:grade(?: of)?|gave (?:the|it|the film|the movie) an?(?: rare| average| overall)?)\s*[\"“'‘]?\s*([ABCDF])\s*([+\-–−]?)\s*[\"”'’]?"
                 r"(?=[^A-Za-z]|$)(?!\s*(?:to|-|–)\s*F)")
CS_NUM = {"A+": 12, "A": 11, "A-": 10, "B+": 9, "B": 8, "B-": 7, "C+": 6, "C": 5, "C-": 4, "D+": 3, "D": 2,
          "D-": 1, "F": 0}


def parse_critics(text):
    """Reception text -> dict of the scores it states (missing ones left out)."""
    out = {}
    t = re.sub(r"\s+", " ", text or "")
    for m in re.finditer(r"Rotten Tomatoes", t):
        w = t[m.end(): m.end() + 420]
        w = w[: w.find("Metacritic")] if "Metacritic" in w else w
        p = _PCT.search(w)
        if not p:                              # "a 100% approval rating on Rotten Tomatoes, based on 54 reviews"
            pre = t[max(0, m.start() - 120): m.start()]
            pre = re.split(r"(?<=[.;])\s", pre)[-1]
            ps = list(_PCT.finditer(pre))
            p = ps[-1] if ps else None
        if not p or not (0 <= float(p.group(1)) <= 100):
            continue
        out["rt_pct"] = float(p.group(1))
        n = _RT_N.search(w)
        if n:
            out["rt_n"] = int(n.group(1).replace(",", ""))
        av = _AVG10.search(w)
        if av:
            x = float(av.group(1) or av.group(2))
            if 0 <= x <= 10:
                out["rt_avg"] = x
        out["rt_text"] = w[:260]
        break
    for m in re.finditer(r"Metacritic", t):
        w = _upto_next(t[m.end(): m.end() + 360], "Metacritic")
        s = _MC.search(w)
        if not s:                              # "It has a score of 100 on Metacritic and ..."
            pre = re.split(r"(?<=[.;])\s", t[max(0, m.start() - 160): m.start()])[-1]
            pre = pre[[x.end() for x in _OTHER_AGG.finditer(pre)][-1]:] if _OTHER_AGG.search(pre) else pre
            ss = list(_MC.finditer(pre))
            s = ss[-1] if ss else None
        if not s or not (0 <= int(s.group(1)) <= 100):
            continue
        out["mc_score"] = float(s.group(1))
        n = _MC_N.search(w)
        if n:
            out["mc_n"] = _count(n.group(1))
        out["mc_text"] = w[:220]
        break
    for m in re.finditer(r"CinemaScore", t):
        w = t[max(0, m.start() - 40): m.end() + 200]
        g = _CS.search(w)
        if g:
            grade = g.group(1) + ("-" if g.group(2) in ("-", "–", "−") else g.group(2))
            if grade in CS_NUM:
                out["cinemascore"] = grade
                out["cinemascore_num"] = CS_NUM[grade]
                out["cs_text"] = w[:200]
                break
    return out


def cmd_critics(_a):
    c = conn()
    films = {}
    for fid, text, snap, url in c.execute("select tmdb_id, text, snapshot, url from sections "
                                          "where kind in ('reception', 'box_office', 'release') "
                                          "order by tmdb_id, kind != 'reception', sec_id, position"):
        f = films.setdefault(fid, {"text": [], "snap": snap, "url": url})
        f["text"].append(text or "")
    rows = []
    for fid, f in films.items():
        d = parse_critics(" ".join(f["text"]))
        if not d:
            continue
        rows.append((fid, d.get("rt_pct"), d.get("rt_n"), d.get("rt_avg"), d.get("mc_score"), d.get("mc_n"),
                     d.get("cinemascore"), d.get("cinemascore_num"), f["snap"], f["url"], d.get("rt_text"),
                     d.get("mc_text"), d.get("cs_text"), F.now()))
    c.execute("delete from critic_scores")
    c.executemany("insert into critic_scores values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    n = lambda k: sum(1 for r in rows if r[k] is not None)  # noqa: E731
    print(f"films with reception text: {len(films):,}; with any score: {len(rows):,} "
          f"(Rotten Tomatoes {n(1):,}, Metacritic {n(4):,}, CinemaScore {n(6):,})")


# ------------------------------------------------------------------ 5. money / 6. real
def cmd_money(_a):
    import tools
    tools.build()


def cmd_real(_a):
    c = conn()
    cpi = {y: v for y, v in c.execute("select year, cpi from cpi_annual")}
    if not cpi:
        raise SystemExit("no CPI yet: python money_extra.py rates")
    base = max(cpi)
    rows = []
    for fid, y, b, g in c.execute("select f.tmdb_id, f.year, m.budget, m.gross from money m "
                                  "join films f on f.tmdb_id = m.tmdb_id"):
        if not y or y not in cpi:
            continue
        k = cpi[base] / cpi[y]
        rows.append((fid, y, base, b * k if b else None, g * k if g else None))
    c.execute("delete from money_real")
    c.executemany("insert into money_real values (?,?,?,?,?)", rows)
    F.set_meta(c, "money_real_base_year", base)
    c.commit()
    print(f"today's dollars ({base} CPI): {len(rows):,} films")


# ------------------------------------------------------------------ 7. report
def cmd_report(_a):
    c = conn()
    lines = [f"money_extra report {F.now()}"]
    tot = c.execute("select count(*) from money where budget > 0 and gross > 0").fetchone()[0]
    fx_any = c.execute("select count(*) from money where budget > 0 and gross > 0 and "
                       "(budget_src like '%\\_fx' escape '\\' or gross_src like '%\\_fx' escape '\\')").fetchone()[0]
    lines.append(f"films with budget AND gross: {tot:,} ({fx_any:,} of them only thanks to a converted figure; "
                 f"{tot - fx_any:,} with US-dollar figures alone)")
    for src, n in c.execute("select source, count(distinct tmdb_id) from facts where source like '%\\_fx' escape '\\' "
                            "group by source"):
        lines.append(f"  converted rows from {src}: {n:,} films")
    try:
        base = F.get_meta(c, "money_real_base_year")
        n = c.execute("select count(*) from money_real where budget_real > 0 and gross_real > 0").fetchone()[0]
        lines.append(f"today's dollars ({base}): {n:,} films with both")
    except sqlite3.Error:
        pass
    try:
        r = c.execute("select count(*), sum(rt_pct is not null), sum(mc_score is not null), "
                      "sum(cinemascore is not null) from critic_scores").fetchone()
        lines.append(f"critic scores from Wikipedia reception text: {r[0]:,} films (Rotten Tomatoes {r[1] or 0:,}, "
                     f"Metacritic {r[2] or 0:,}, CinemaScore {r[3] or 0:,})")
    except sqlite3.Error:
        pass
    lines.append("\nConversions to check by eye (20 random):")
    for t, y, field, v, note in c.execute("""select f.title, f.year, x.field, x.value_num, x.value_text from facts x
            join films f on f.tmdb_id = x.tmdb_id where x.source like '%\\_fx' escape '\\'
            order by random() limit 20"""):
        lines.append(f"  {t} ({y}) {field}: ${v / 1e6:,.1f}M  <- {clean_amount(note)[:140]}")
    lines.append("\nCritic scores to check by eye (10 random):")
    try:
        for t, y, rt, rn, mc, cs, txt in c.execute("""select f.title, f.year, s.rt_pct, s.rt_n, s.mc_score, s.cinemascore,
                s.rt_text from critic_scores s join films f on f.tmdb_id = s.tmdb_id order by random() limit 10"""):
            lines.append(f"  {t} ({y}): RT {rt}% of {rn} | MC {mc} | CinemaScore {cs}  <- {(txt or '')[:100]}")
    except sqlite3.Error:
        pass
    text = "\n".join(lines)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    open(os.path.join(HERE, "results", "money_extra_summary.txt"), "w").write(text + "\n")
    print(text)


def cmd_remove(_a):
    c = conn()
    n = c.execute("delete from facts where source like '%\\_fx' escape '\\'").rowcount
    for t in ("critic_scores", "money_real"):
        c.execute(f"delete from {t}")
    c.commit()
    print(f"removed {n:,} converted rows, critic scores and today's-dollar figures; rebuilding money ...")
    cmd_money(_a)


def cmd_show(a):
    c = conn()
    r = c.execute("select tmdb_id, title, year from films where title like ? order by vote_count desc limit 1",
                  (a.title,)).fetchone()
    if not r:
        raise SystemExit("not found")
    print(r)
    for row in c.execute("select field, value_num, currency, source, value_text from facts where tmdb_id = ? "
                         "and field in ('budget','box_office','revenue')", (r[0],)):
        print("  fact", row)
    print("  money", c.execute("select * from money where tmdb_id = ?", (r[0],)).fetchone())
    print("  real ", c.execute("select * from money_real where tmdb_id = ?", (r[0],)).fetchone())
    print("  critics", c.execute("select rt_pct, rt_n, rt_avg, mc_score, mc_n, cinemascore from critic_scores "
                                 "where tmdb_id = ?", (r[0],)).fetchone())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["all", "rates", "currencies", "convert", "critics", "money", "real", "report",
                                    "remove", "show", "diagnose"])
    ap.add_argument("title", nargs="?")
    ap.add_argument("--fx-csv")
    ap.add_argument("--cpi-csv")
    a = ap.parse_args()
    steps = {"rates": cmd_rates, "currencies": cmd_currencies, "convert": cmd_convert, "critics": cmd_critics,
             "money": cmd_money, "real": cmd_real, "report": cmd_report, "remove": cmd_remove, "show": cmd_show,
             "diagnose": cmd_diagnose}
    if a.cmd == "all":
        for s in ("rates", "currencies", "convert", "critics", "money", "real", "report"):
            print(f"=== {s} {datetime.datetime.now():%H:%M}", flush=True)
            steps[s](a)
    else:
        steps[a.cmd](a)


if __name__ == "__main__":
    main()
