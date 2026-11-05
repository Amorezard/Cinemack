"""D4 (v7): two people at once - no shared state leaks between them.

    python -m unittest tests.test_two_users -v          (MOCK site; no GPU, no Ollama, temporary databases)
"""
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(MOVIE_BUFF_MOCK="1", CHATS_DB=f"{TMP}/chats.db", MEMORY_DB=f"{TMP}/memory.db",
                  USERS_DB=f"{TMP}/users.db", SITE_CACHE_DB=f"{TMP}/site_cache.sqlite")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "web"))


class TwoUsers(unittest.TestCase):
    def test_request_state_is_per_thread(self):
        from request_state import LocalDict
        d = LocalDict(lambda: {"hide": set()})
        seen, gate = {}, threading.Barrier(2)

        def person(name, hide):
            d["hide"] = {hide}
            gate.wait()                       # both have written before either reads
            seen[name] = set(d["hide"])
        ts = [threading.Thread(target=person, args=("a", "money")), threading.Thread(target=person, args=("b", "critics"))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(seen, {"a": {"money"}, "b": {"critics"}})

    def test_modules_use_per_thread_state(self):
        import tools
        import retrieve
        import chat
        from request_state import LocalDict
        for obj in (tools.REQUEST, retrieve.PERSON, retrieve.LAST, chat.LAST_TOOL):
            self.assertIsInstance(obj, LocalDict)

    def test_site_keeps_people_apart(self):
        from fastapi.testclient import TestClient
        import server
        c = TestClient(server.app)
        A, B = {"x-mb-user": "userA"}, {"x-mb-user": "userB"}
        c.post("/api/settings", json={"settings": {"memory": True}}, headers=A)
        import memory
        memory.add("userA", "Watches films with two young kids")
        r = c.post("/api/chat", json={"message": "a heist movie please"}, headers={**A, "x-mb-conv": "convA"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(c.get("/api/memory", headers=A).json()["notes"]), 1)
        self.assertEqual(c.get("/api/memory", headers=B).json()["notes"], [])
        self.assertTrue(c.get("/api/chats", headers=A).json()["chats"])
        self.assertEqual(c.get("/api/chats", headers=B).json()["chats"], [])
        self.assertEqual(c.get("/api/chats/convA", headers=B).status_code, 404)
        self.assertFalse(c.delete("/api/chats/convA", headers=B).json()["ok"])     # B cannot delete A's chat
        self.assertTrue(c.get("/api/chats", headers=A).json()["chats"])


if __name__ == "__main__":
    unittest.main()
