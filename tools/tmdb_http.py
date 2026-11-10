"""Small TMDB client shared by the round-19 loaders (10 Nov 2026). The key comes from TMDB_API_KEY (or TMDB_TOKEN);
a v4 read token (long, starts "eyJ") goes in a Bearer header, a v3 key in the query. The key is never printed: errors
show the path only. Backs off on 429; 404 returns None; other failures return FAILED so the caller can retry later."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"
FAILED = object()
STATS = {"requests": 0, "errors": 0, "not_found": 0}
_lock = threading.Lock()


def key():
    return (os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_TOKEN") or "").strip().strip("'\"")


def get(path, **params):
    k = key()
    if not k:
        raise SystemExit("TMDB_API_KEY is not set")
    headers = {"Accept": "application/json", "User-Agent": "cinemack-data/1"}
    if k.startswith("eyJ") or len(k) > 40:
        headers["Authorization"] = f"Bearer {k}"
    else:
        params["api_key"] = k
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        with _lock:
            STATS["requests"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After") or 2) + attempt)
                continue
            if e.code == 404:
                with _lock:
                    STATS["not_found"] += 1
                return None
            if e.code >= 500:
                time.sleep(1 + attempt)
                continue
            with _lock:
                STATS["errors"] += 1
            print(f"  {path}: HTTP {e.code}", flush=True)
            return FAILED
        except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError) as e:
            time.sleep(1 + attempt)
            last = type(e).__name__
    with _lock:
        STATS["errors"] += 1
    print(f"  {path}: gave up ({locals().get('last', 'retries')})", flush=True)
    return FAILED


def columns(conn, table):
    return [r[1] for r in conn.execute(f"pragma table_info({table})")]


def insert(conn, table, row, replace=True):
    """Insert a dict, using only the columns the table really has (server and fixture schemas differ)."""
    cols = [c for c in columns(conn, table) if c in row]
    conn.execute(f"insert {'or replace ' if replace else ''}into {table}({','.join(cols)}) values ({','.join('?' * len(cols))})",
                 [row[c] for c in cols])
