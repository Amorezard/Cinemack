#!/usr/bin/env python3
"""v8.1 (12 Nov 2026): a consistent copy of a live SQLite database for the v8.1 worktree.

The source is opened READ-ONLY and copied with SQLite's backup API in one step (a WAL reader does not block the
site's writers). Nothing is written to the source. The copy is checked by comparing row counts of a few tables.

    python tools/v81_copy_db.py ~/movie-buff/rag/facts.db facts.db films money credits
"""
import os
import sqlite3
import sys
import time


def main():
    src_path, dst_path, tables = os.path.expanduser(sys.argv[1]), sys.argv[2], sys.argv[3:]
    if os.path.realpath(src_path) == os.path.realpath(dst_path):
        sys.exit("source and copy are the same file")
    if os.path.exists(dst_path):
        sys.exit(f"{dst_path} already exists; not overwritten")
    t0 = time.time()
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=120)
    tmp = dst_path + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    dst = sqlite3.connect(tmp)
    src.backup(dst, pages=-1)
    dst.commit()
    ok = True
    for t in tables:
        a = src.execute(f'select count(*) from "{t}"').fetchone()[0]
        b = dst.execute(f'select count(*) from "{t}"').fetchone()[0]
        print(f"  {t}: source {a:,} copy {b:,} {'ok' if a <= b + 50 and b <= a else 'DIFFERENT'}")
        ok = ok and b <= a and a <= b + 50          # the site may add a few rows while the counts are taken
    dst.close()
    src.close()
    if not ok:
        os.remove(tmp)
        sys.exit("copy does not match; removed")
    os.replace(tmp, dst_path)
    print(f"copied {os.path.getsize(dst_path) / 1e9:.2f} GB in {time.time() - t0:.0f} s -> {dst_path}")


if __name__ == "__main__":
    main()
