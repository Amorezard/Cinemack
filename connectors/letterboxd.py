"""
R10 (v7): Letterboxd. Its API is by request only (you@example.edu, emailed 23 Oct) and its terms forbid
scraping, so nothing here fetches anything until access is approved. If it is: the API's film endpoint gives the
rating histogram and review counts per film (matched by TMDB id, which Letterboxd stores), and member reviews;
they would go into letterboxd_stats(tmdb_id, rating, n, hist_json, fetched_at) and the evidence index as
'audience' units with source Letterboxd, credited and linked as its terms require.
"""
APPROVED = False


def fetch(*_a, **_k):
    raise SystemExit("Letterboxd API access has not been approved; nothing is fetched (terms forbid scraping).")
