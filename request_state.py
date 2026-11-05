"""
D4 (v7, 27 Oct): per-request state that is safe with more than one question at a time.

Several modules kept "the question being answered right now" in module-level dicts (tools.REQUEST, retrieve.PERSON,
retrieve.LAST, chat.LAST_TOOL, user_prefs.CURRENT, the web pipeline's trace). That was safe only because the site
answers one question at a time (gpu_lock); with two at once (D9: OLLAMA_NUM_PARALLEL=2, the fast path, cached
replays) one person's settings or plan could leak into another's answer. LocalDict keeps the same dict interface
(code that does REQUEST["hide"] = ..., LAST.clear(), LAST_TOOL.update(...) is unchanged) but every thread - and
the site runs each question in its own worker thread - sees its own copy.
"""
import threading
from collections.abc import MutableMapping


class LocalDict(MutableMapping):
    def __init__(self, factory=dict):
        self._factory = factory if callable(factory) else (lambda f=factory: dict(f))
        self._local = threading.local()

    def _d(self):
        d = getattr(self._local, "d", None)
        if d is None:
            d = self._local.d = self._factory()
        return d

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __delitem__(self, k):
        del self._d()[k]

    def __iter__(self):
        return iter(self._d())

    def __len__(self):
        return len(self._d())

    def __repr__(self):
        return f"LocalDict({self._d()!r})"

    def copy(self):
        return dict(self._d())
