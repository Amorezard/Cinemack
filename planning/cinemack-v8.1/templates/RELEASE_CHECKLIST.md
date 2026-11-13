# Release checklist — not a deployment authorization

- [ ] Deployment/restart and external API permissions explicitly approved
- [ ] Working tree clean or exact expected files accounted for
- [ ] Backups/restore tested on safe copies; migrations backward compatible
- [ ] Unit/integration/browser/synthetic security checks pass
- [ ] Applicable sealed tests approved and executed under defined protocol, or not required
- [ ] Predictions calibrated and labeled; no profit/ROI misrepresentation
- [ ] RAG sources, citations, cache TTL and permission tests pass
- [ ] No cross-user recommendation/history/chat data leakage
- [ ] Core user journeys (Home/Chat/Pick Tonight/Calendar/Watchlist/Profile) regression tested
- [ ] Accessibility/mobile/reduced-motion verified
- [ ] Load/queue/error/p95/V RAM metrics from real test captured
- [ ] External content licenses/attribution/rights reviewed
- [ ] New flags OFF by default; launch toggles explicitly controlled
- [ ] `EMAIL_SANDBOX=1` unless separately authorized
- [ ] HTTPS and cookie security reviewed before public accounts
- [ ] Health checks, rollback commands and responsible operator documented
- [ ] Post-deploy metrics and rollback triggers specified
