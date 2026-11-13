# What else is needed for Claude to do the actual implementation?

The **kit is sufficient as a brief**, but it is not a repository clone. Claude Code needs local/SSH access to the real project and its installed dependencies.

## Required project context

1. Repository source (verify actual path; user README suggests `~/movie-buff/rag`) including all `web/v8/` page JS/CSS, `retrieve.py`, `tools.py`, `performance.py`, `pf5_*`, `start_site.sh`, `nightly.sh` and tests.
2. Root `README.md` v3–v7 history, `docs/validation_matrix.md`, `docs/decision_log.md`, `docs/issue_log.md`, `docs/threat_model.md`, round-27 notes/results as present.
3. Safe mock fixtures and test DB copies; current schemas/row counts/index definitions, but **not** a raw production DB export in chat.
4. Installed Python/JS/Ollama toolchain versions and GPU model/VRAM/quantization/context settings. No credential disclosures.
5. Permission boundaries: can Claude create branches, run mock tests, use staging, consume limited APIs, modify Git, or reboot services? The kit allows local reversible development only by default.
6. UI page screenshots of actual Home, Pick Tonight, Calendar, Watchlist and Profile *if current visuals differ from what README describes*. The existing uploaded screenshot documents UX principles only.

## Optional but useful

- Existing product requirements/brand tokens, page design screenshots already approved, user research and anonymous example queries.
- Expected hosting architecture, domain/subdomain arrangements, HTTPS plan and authorized showtime/offer providers.
- Staging load-test accounts with synthetic private data, and isolated production-like DB read-only snapshots.
- External data-provider license agreements, permitted API scopes, rate limits and source attribution requirements.
- Existing box-office dataset feature timestamp provenance and forecast evaluation reports.

## No need to provide

Passwords, SSH private keys, `.env` values, email SMTP credentials, auth session tokens, sensitive individual user chat histories, massive DB/model files in this kit, or sealed-test contents. Run tools in the actual authorized environment; keep secrets in existing secure storage.
