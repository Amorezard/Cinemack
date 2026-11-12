#!/usr/bin/env python3
"""Email digests of new alerts (web/emailer.py), run by nightly.sh; plus checks of the email API set-up.
    python tools/send_digests.py                    # one digest per verified, opted-in person with new alerts
    python tools/send_digests.py --status           # which provider, live or sandbox, and why (never prints keys)
    python tools/send_digests.py --test you@example.edu   # send one test email through the provider
    python tools/send_digests.py --limit N
Live sending needs EMAIL_SANDBOX=0, EMAIL_PROVIDER and its key, and EMAIL_FROM (keys.env); otherwise emails are
written to logs/email_outbox/.
"""
import os
import sys

RAG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(RAG, "web"), RAG]
os.chdir(RAG)
import emailer  # noqa: E402

live, why = emailer.configured()
mode = f"LIVE via {why}" if live else f"sandbox - {why} (emails go to logs/email_outbox/)"
if "--status" in sys.argv:
    print(f"email: {mode}; from {emailer.FROM!r}; site link {emailer.SITE_URL}")
    sys.exit(0)
if "--test" in sys.argv:
    to = sys.argv[sys.argv.index("--test") + 1]
    try:
        where = emailer.send(to, "Cinemack test email", "This is a test from Cinemack's email set-up. No action needed.")
        print(f"test email ({mode}): sent -> {where}")
    except RuntimeError as e:
        print(f"test email ({mode}): FAILED - {e}")
        sys.exit(1)
    sys.exit(0)
import discovery  # noqa: E402

lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
r = emailer.digest_all(lambda u: discovery.alerts(u, refresh=True), lim)
print(f"email digests ({mode}): {r}")
