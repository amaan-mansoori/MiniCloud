# MiniCloud Resume MVP Audit

**Assessment:** NOT COMPLETE

**Scope:** Educational portfolio MVP; not a production-readiness assessment.

This report distinguishes implemented code and automated test evidence from
runtime behavior that has not been validated. Results below are historical
and were not rerun as part of public-repository preparation.

## Architecture

MiniCloud is a modular monolith composed of:

- Next.js and TypeScript dashboard (`apps/web`).
- FastAPI control plane and SQLAlchemy models (`services/api`).
- PostgreSQL with Alembic migrations.
- Python polling Worker (`services/worker`) using PostgreSQL as its durable job
  store; there is no separate message broker.
- Python CLI (`packages/minicloud_cli`).
- Kubernetes adapter and namespace-scoped RBAC manifests (`infra/kubernetes`).

The API persists deployment requests and lifecycle events. The Worker
reconciles Kubernetes workloads and observes readiness when configured. The
safe local demo profile intentionally omits the Worker and Kubernetes, so an
accepted deployment remains queued.

## Requirement status

| Area | Status | Evidence and limitation |
|---|---|---|
| Authentication, project membership, authorization | Implemented and tested | Backend and PostgreSQL test coverage exists; this is not a hostile multi-tenant security certification. |
| Durable deployment requests and lifecycle events | Implemented and tested | API/Worker persistence paths and isolated database behavior were exercised. |
| Migrations and API/Worker database roles | Implemented and tested in isolation | Dedicated role behavior was tested against disposable PostgreSQL; no persistent deployment is certified. |
| Worker retries and lease recovery | Implemented; live operation unverified | Focused tests exist; current-candidate successful Kubernetes reconciliation has not been demonstrated. |
| Kubernetes Deployment/Service and readiness | Not verified end to end | Adapter and source tests exist. Historical E2E used administrator credentials and an earlier candidate, so it is not current least-privilege evidence. |
| Scale, revisions, rollback, diagnostics | Implemented; live operation unverified | API, Worker, CLI, and UI coverage exists; current-candidate Kubernetes convergence is not verified. |
| Project quotas | Partially implemented | API admission policies exist; Kubernetes `ResourceQuota`/`LimitRange` reconciliation is not implemented. |
| Local API/Worker/Web runtime | Not verified | Compose configuration validation does not start services. Docker runtime validation was unavailable during the last reported assessment. |
| Security controls | Partially implemented | Authentication, authorization, input constraints, role separation, and dependency checks exist; image scanning and production hardening remain incomplete. |
| CI | Configured; execution unverified | Workflow configuration exists; no GitHub Actions run is claimed. |
| Genuine deployment screenshots/video | Not implemented | No genuine successful current-candidate demo artifacts are available. |

## Historical validation summary

These results were recorded on 2026-11-14 before publication work and have not
been rerun for this publication:

- Backend/API/Worker/CLI test suite: **176 passed, 1 skipped, 2 warnings**.
  The skipped test is opt-in Kubernetes integration.
- Ruff: passed for API, Worker, CLI, and related tests.
- `pip-audit --strict`: no known vulnerabilities in the audited backend
  requirements.
- Web lint and TypeScript checks: passed.
- Web production build: passed in prior validation.
- Playwright: **1 passed** using a mocked API; this is not a real deployment
  test.
- `npm audit`: **0 vulnerabilities** in the audited Web dependency tree.
- Disposable PostgreSQL integration: **8 passed, 2 warnings**.
- Compose configuration checks passed; no services were started by those
  checks.

See the [testing guide](../docs/TESTING.md) and
[test evidence summary](MC-FINAL-TEST-EVIDENCE.md) for scope and caveats.

## Required next verification

1. Validate Docker runtime in an explicitly authorized disposable environment.
2. Select an isolated Kubernetes cluster and namespace; verify namespace-scoped
   Worker and diagnostics identities without administrator credentials.
3. Run the exact current API and Worker candidates through create, readiness,
   scale, rollback, lifecycle readback, and bounded diagnostics.
4. Rerun the relevant test suites and CI, then update this report with
   attributable evidence.
5. Capture real screenshots or a recording only after that workflow succeeds.

Until these gates pass, do not describe MiniCloud as production-ready or claim
that current-candidate least-privilege Kubernetes E2E has been verified.
