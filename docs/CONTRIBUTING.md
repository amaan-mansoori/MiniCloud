# Contributing

1. Inspect `git status` and the target file's current diff before editing.
   Preserve all existing and untracked changes.
2. Keep changes narrow and add tests for introduced behavior.
3. Run focused tests first, then the backend suite and relevant frontend
   quality gates.
4. Do not include `.env`, `.local`, tokens, passwords, database URLs, kubeconfig
   files, or test credentials in changes, logs, screenshots, or reports.
5. Do not modify the existing PostgreSQL volume or Kubernetes cluster as a
   test shortcut. Use isolated disposable resources with verified ownership.
6. Update the requirement matrix and evidence only when the acceptance
   criteria are demonstrated. Use `IMPLEMENTED_UNVERIFIED` when integration
   acceptance is incomplete; do not equate implementation with verification.
7. Preserve meaningful failures. Do not hide skipped/blocked tests, use fake
   metrics, or treat API acceptance as rollout readiness.

Useful checks are listed in [testing](TESTING.md). The current release report
states unresolved implementation and infrastructure gates. Do not include
local reports, raw logs, runtime state, private configuration, or data
artifacts in a contribution.
