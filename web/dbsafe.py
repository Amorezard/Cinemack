"""
10 Nov 2026: one sqlite3 connection shared by the web server's worker threads must never be used by two threads at
the same moment. Several modules open one long-lived connection (check_same_thread=False) and read from it without a
lock; when a page sends two requests at once (Profile, Pick Tonight) a read could come back as None or raise
"SystemError: ... returned NULL". wrap() returns a drop-in proxy that runs each call under one lock per connection and
reads the rows INSIDE the lock, so a cursor is never shared. Module-level write locks keep working as before.
"""
import threading


class _Rows:
    __slots__ = ("_rows", "_i", "rowcount", "lastrowid", "description")

    def __init__(self, cur):
        self.description = cur.description
        self._rows = cur.fetchall() if cur.description else []
        self._i = 0
        self.rowcount, self.lastrowid = cur.rowcount, cur.lastrowid

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self):
        out, self._i = self._rows[self._i:], len(self._rows)
        return out

    def fetchmany(self, n=1):
        out = self._rows[self._i:self._i + n]
        self._i += len(out)
        return out

    def __iter__(self):
        return iter(self.fetchall())


class SafeConn:
    def __init__(self, conn):
        self._c = conn
        self._l = threading.RLock()

    def execute(self, sql, params=()):
        with self._l:
            return _Rows(self._c.execute(sql, params))

    def executemany(self, sql, seq):
        with self._l:
            return _Rows(self._c.executemany(sql, seq))

    def executescript(self, script):
        with self._l:
            self._c.executescript(script)

    def commit(self):
        with self._l:
            self._c.commit()

    def rollback(self):
        with self._l:
            self._c.rollback()

    def close(self):
        with self._l:
            self._c.close()

    def __getattr__(self, name):            # total_changes, in_transaction, ...
        return getattr(self._c, name)

    def __enter__(self):
        self._l.acquire()
        self._c.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._c.__exit__(*exc)
        finally:
            self._l.release()


def wrap(conn):
    return SafeConn(conn) if conn is not None and not isinstance(conn, SafeConn) else conn
