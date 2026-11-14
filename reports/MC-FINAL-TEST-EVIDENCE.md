# MiniCloud Test Evidence Summary

**Evidence date:** 2026-11-14

**Publication note:** The results below are historical and were not rerun
during public-repository preparation. Raw machine-specific logs and local
runtime artifacts are intentionally not included.

## Recorded automated checks

| Check | Historical result | Scope and limitation |
|---|---|---|
| Backend/API/Worker/CLI pytest suite | 176 passed, 1 skipped, 2 warnings | The skipped test is opt-in Kubernetes integration. |
| Ruff | Passed | API, Worker, CLI, and related test sources. |
| Backend `pip-audit --strict` | No known vulnerabilities | Audited declared backend requirements; not a container-image scan. |
| Web lint and TypeScript | Passed | Web source. |
| Web production build | Passed in prior validation | Not rerun in the final completion phase or during publication. |
| Playwright | 1 passed | Mocked API; does not verify backend, Worker, or Kubernetes behavior. |
| Web `npm audit` | 0 vulnerabilities | Audited Web dependency tree; not a full supply-chain assessment. |
| Disposable PostgreSQL integration | 8 passed, 2 warnings | Isolated database workflow; not evidence about a persistent deployment. |
| Compose configuration | Passed | Configuration parsing only; containers were not started by this check. |

## Runtime and integration limits

- Docker Linux-engine availability prevented current full-stack runtime
  verification during the last reported assessment.
- The current-candidate, least-privilege Kubernetes E2E has **not** been
  verified. Historical Kubernetes E2E used administrator credentials and an
  earlier candidate, so it is not acceptance evidence for the current source.
- Current-candidate Ready-state readback, scaling convergence, rollback,
  Kubernetes diagnostics, and successful Worker reconciliation remain
  unverified.
- GitHub Actions was not run.
- No genuine successful-deployment screenshots or demo recording exist.
- Container operating-system vulnerability scanning was not completed.

The [testing guide](../docs/TESTING.md) documents reproducible commands. The
[Resume MVP audit](MINICLOUD-RESUME-MVP-AUDIT.md) records requirement status
and the next verification gates.
