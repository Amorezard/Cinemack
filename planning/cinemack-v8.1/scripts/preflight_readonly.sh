#!/usr/bin/env bash
# Read-only, minimal Cinemack repository discovery. Does not open sensitive files.
set -euo pipefail
printf '\n=== location ===\n'; pwd
printf '\n=== git state ===\n'; git status --short || true
printf '\n=== branch ===\n'; git branch --show-current || true
printf '\n=== recent history ===\n'; git log -5 --oneline || true
printf '\n=== tool availability ===\n'; python --version || true
if command -v ollama >/dev/null 2>&1; then ollama ps || true; fi
printf '\n=== selected non-sensitive paths ===\n'
for p in README.md retrieve.py tools.py performance.py pf5_model.py pf5_data.py web/server.py web/v8 docs/validation_matrix.md docs/decision_log.md docs/issue_log.md; do
  if [[ -e "$p" ]]; then printf 'PRESENT  %s\n' "$p"; else printf 'ABSENT   %s\n' "$p"; fi
done
printf '\nRead the README and source before running tests, connecting to DBs, or changing files.\n'
