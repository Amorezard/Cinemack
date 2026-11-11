#!/usr/bin/env python3
"""Round 27: one email digest per verified, opted-in person with new alerts (web/emailer.py). Run by nightly.sh.
Sandbox unless EMAIL_SANDBOX=0 and SMTP_* are set: emails then go to logs/email_outbox/*.eml.
    python tools/send_digests.py [--limit N]
"""
import os
import sys

RAG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(RAG, "web"), RAG]
os.chdir(RAG)
import discovery  # noqa: E402
import emailer  # noqa: E402

lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
r = emailer.digest_all(lambda u: discovery.alerts(u, refresh=True), lim)
print(f"email digests ({'sandbox: logs/email_outbox' if emailer.SANDBOX or not os.environ.get('SMTP_HOST') else 'SMTP'}): {r}")
