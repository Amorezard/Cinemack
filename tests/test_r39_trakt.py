"""Round 39 (12 Nov): every Trakt request carries a User-Agent.   python -m unittest tests.test_r39_trakt"""
import os
import sys
import tempfile
import unittest
import urllib.request

T = tempfile.mkdtemp()
for k in ("TRAKT_DB", "HISTORY_DB", "USERS_DB", "FACTS_DB", "SOCIAL_DB"):
    os.environ[k] = os.path.join(T, k.lower() + ".db")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(HERE, "web"), HERE]
import trakt_sync  # noqa: E402


class UserAgent(unittest.TestCase):
    def test_header_sent(self):
        seen = {}

        class R:
            status = 200
            headers = {}

            def read(self):
                return b'{"ok": 1}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return R()
        orig, urllib.request.urlopen = urllib.request.urlopen, fake
        try:
            st, body, _h = trakt_sync._http("POST", trakt_sync.API + "/oauth/device/code", {"client_id": "x"}, {"trakt-api-version": "2"})
        finally:
            urllib.request.urlopen = orig
        self.assertEqual((st, body), (200, {"ok": 1}))
        self.assertTrue(seen.get("user-agent", "").startswith("Cinemack/"))
        self.assertEqual(seen.get("trakt-api-version"), "2")


if __name__ == "__main__":
    unittest.main()
