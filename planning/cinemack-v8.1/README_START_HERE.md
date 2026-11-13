# Cinemack v8.1 — Claude Code implementation kit

**Prepared:** 2026-11-12 | **Current state:** Plan and handoff artifacts only. No edits made to the real Cinemack repository or server.

## Open these first

1. **`CLAUDE_MASTER_PROMPT.md`** — full implementation instructions, policies, priority order, success criteria and execution cycle.
2. **`docs/IMPLEMENTATION_ROADMAP.md`** — vertical slices and dependencies.
3. **`docs/ACCEPTANCE_TEST_MATRIX.md`** — specific test cases and evidence rules.
4. **`docs/ARCHITECTURE_AND_DATA_CONTRACTS.md`** — boundaries, candidate contracts and invariants.
5. **`docs/UX_MOTION_SPEC.md`** — 20 UX principles mapped to actual site behaviors.
6. **`docs/RUNBOOK_AND_DEPLOYMENT.md`** — audit, mocks, backup, security and rollout restrictions.
7. **`config/quality_gates.yaml`** and `specs/*.json` — proposed machine-readable gates and schema examples.
8. **`reference/`** — user's November 11 README and UX screenshot.
9. **`START_IN_CLAUDE_CODE.txt`** — short invocation to paste into Claude Code.

## How to use with Claude Code

- Unzip the kit **outside** the project first. Review the contents. Then copy the kit into the project under a folder such as `planning/cinemack-v8.1/`; do not overwrite `CLAUDE.md`, any existing source file, lockfile, DB, model checkpoint, evaluation result, or README.
- Open Claude Code in the **actual repository** (the README reports the server path as `~/movie-buff/rag`, but verify). Paste `START_IN_CLAUDE_CODE.txt`, changing the kit path if needed. Attach the kit files if using a Claude Code interface that cannot read their filesystem paths.
- Claude should begin with a read-only audit, create a separate working branch when safe, write a file-by-file plan based on the real repo and tests, and then implement safe vertical slices. It must request permission for production/deployment/destructive steps.
- You can run `python scripts/validate_kit.py` locally to verify sample JSON and schemas. This validates **only the kit**, not Cinemack's implementation.

## Grounding and limitations

- The provided 113-line README is a description of current architecture as of Nov 11, 2026; it is **not a full source audit**. No access to Cinemack's actual repository/DBs/models was available during this packaging task.
- The UX screenshot lists principles, not a finished Cinemack visual design. It should guide interaction and usability tests, not justify copying Instagram branding or UI.
- Schemas are **examples for discussion and adaptation**, not validated against the current DB/API code. The YAML gate values are *proposals* and may be made stricter by existing research rules.
- This kit deliberately excludes credentials, data exports, weight files, or destructive deployment scripts.

## Verify source-of-truth before implementation

The README states feature flags and known experiments including `SEMCACHE` on, `NEWS` off, `PERF` off, `MEM_CARDS`/`CLAIM_REWRITE`/`CTX_BUILDER`/`GUARD2` built but not yet enabled, and the `embed_ft` in shadow evaluation. These should be **audited and tested**, not recreated without reason.

**Acceptance:** Approved plan is ready for execution; actual implementation/testing requires Claude Code and project access. Do not confuse a generated plan with code changes, passing tests, or deployment.
