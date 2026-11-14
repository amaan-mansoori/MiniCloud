# Testing

## Commands

From the repository root:

```powershell
python -m pytest services\api\tests -q
python -m ruff check services\api\app services\api\tests services\api\integration_tests services\worker packages\minicloud_cli
pip-audit --strict -r services\api\requirements.txt
```

For Web checks, from `apps/web`:

```powershell
npm ci
npm run lint
npm run typecheck
npm run test:e2e
npm run build
npm audit --audit-level=moderate
```

The Playwright test uses a mocked API; it is not a backend, Worker, or
Kubernetes integration test. The dedicated PostgreSQL integration script is
`scripts/Test-MiniCloudPostgresIntegration.ps1`; inspect it before running.
The opt-in kind test requires an explicitly selected disposable cluster and
context.

## Last reported results

These are historical results from the 2026-11-14 validation, not checks rerun
as part of public-repository preparation:

| Check | Last recorded result | Scope |
|---|---|---|
| Backend/API/CLI pytest suite | 176 passed, 1 skipped, 2 warnings | Python 3.14.3; skip is opt-in Kubernetes integration. |
| Ruff | Passed | API, Worker, tests, and CLI sources. |
| `pip-audit --strict` | No known vulnerabilities | Backend requirements. |
| Web lint and TypeScript | Passed | Web source. |
| Web production build | Passed in prior validation | Not rerun in the final completion phase. |
| Playwright | 1 passed | Mocked API; not a real deployment flow. |
| `npm audit --audit-level=moderate` | 0 vulnerabilities | Web dependency tree. |
| Compose configuration | Both files parsed | No containers were started by those checks. |
| Disposable PostgreSQL integration | 8 passed, 2 warnings | Historical isolated PostgreSQL run. |

Docker runtime, current-candidate Kubernetes readiness, scaling, rollback,
and live diagnostics remain unverified. GitHub Actions was not run. See the
[test evidence report](../reports/MC-FINAL-TEST-EVIDENCE.md) and
[current audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md) for qualifications.
