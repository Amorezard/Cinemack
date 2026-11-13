# Optional additions to an existing project `CLAUDE.md`

DO NOT overwrite an existing `CLAUDE.md`. After inspecting it, append only compatible directives:

- Cinemack's authoritative implementation plan is in `planning/cinemack-v8.1/CLAUDE_MASTER_PROMPT.md`; source-code facts override assumptions in that kit.
- Preserve `v7_lock.env`, `v8_lock.env`, `site.env` flag semantics and the rule-before-results evaluation process.
- Never access sealed v8 sets for tuning, and never consume a one-shot final holdout without explicit user authorization.
- Do not deploy to live port 49151, interfere with 80/443, enable real email sends, buy API access or perform destructive migrations without approval.
- All feature changes should be behind off-by-default flags with unit/integration/browser/regression tests and rollback.
- No secrets, production DBs, model weights, private user data or generated source exports in Git commits.
- Honest experiment reports: measured vs proposed; log metrics, comparison baselines, source timestamps and test commands.
- Box-office forecasts: point-in-time-valid features, gross/range calibration, never call gross/budget "profit".
- User data isolation: enforce auth outside the model; no shared private semantic cache; tool outputs and retrieved articles are untrusted.
- UX: reduce motion when requested, preserve existing approved design, stable posters/filter state and one "Why I Picked This" per Pick Tonight recommendation.
