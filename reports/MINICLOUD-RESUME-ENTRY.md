# MiniCloud — Resume and Interview Notes

## Project title

**MiniCloud — Cloud-Native Deployment Control Plane**

## One-line summary

A personal portfolio project implementing a FastAPI/PostgreSQL control plane,
Python job-processing Worker, Kubernetes adapter, Next.js dashboard, and
Python CLI for container deployment lifecycle management.

## Resume bullets

- Designed and implemented a modular REST control plane using FastAPI,
  SQLAlchemy, PostgreSQL, and Alembic, with authenticated project
  membership, role-based authorization, durable deployment jobs, and
  correlated lifecycle events.
- Implemented a Python polling Worker with database-backed leases, retries,
  failure classification, and Kubernetes Deployment/Service reconciliation;
  the Worker reports ready only after observing Kubernetes readiness. Current
  candidate live-cluster readiness verification remains pending.
- Built a Next.js/TypeScript developer dashboard and Python CLI for project
  workflows, deployment status, horizontal replica scaling, revision history,
  rollback, and bounded lifecycle diagnostics; automated backend/API/CLI
  suite passed **176 tests** with one opt-in Kubernetes test skipped.
- Added non-root container configurations, environment-based secret handling,
  separate API/Worker database roles, namespace-scoped Kubernetes RBAC
  manifests, and GitHub Actions checks. Frontend lint, TypeScript, production
  build, mocked-API browser test, and dependency audits passed locally; no
  current-candidate least-privilege Kubernetes E2E is claimed.

## Tech stack

Python, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, Kubernetes Python
client, Next.js, React, TypeScript, Tailwind CSS, Docker, Docker Compose,
GitHub Actions, pytest, Ruff, Playwright, pip-audit, npm audit.

## Interview explanation

MiniCloud is a self-contained portfolio platform for submitting and tracking
container deployment requests. The API validates authenticated,
project-authorized requests and persists deployment state and a job in
PostgreSQL. A separate polling Worker claims jobs with leases, applies
deterministic Kubernetes resources, observes rollout state, and records
lifecycle events. The dashboard and CLI read the same API state and expose
deployment operations.

The implementation is substantial and has automated coverage, but its final
acceptance gate is still open: no current candidate Worker has been run against
a separate cluster with a dedicated least-privilege identity. Describe
Kubernetes deployment success only after that proof is captured.

## Architecture explanation

The architecture is a modular monolith for the control plane plus one
asynchronous Worker. PostgreSQL serves both as the application database and
the durable job store, avoiding an additional broker for this portfolio's
scope. The API owns validation, project authorization, idempotency, and
transactional job acceptance. The Worker owns long-running reconciliation and
readiness observation. The Web console and CLI are API clients, not direct
database or Kubernetes clients.

`202 Accepted` indicates the request and job were persisted. It does not mean
the Kubernetes object is ready. The Worker observes the desired generation and
ready/available replica counts before recording `ready`; failures remain
explicit lifecycle states.

## Likely interview questions

### 1. Why use PostgreSQL for background jobs instead of Kafka or Redis?

The portfolio workload does not justify another service. A transaction can
persist the deployment request, revision, job, and initial event together.
Worker leases and retry state make work recoverable. This keeps local
development smaller while retaining durable asynchronous processing.

### 2. How do you prevent an accepted API request from being reported as ready?

The API returns `202 Accepted` after persistence. The Worker performs the
Kubernetes apply and then observes the Deployment's current generation and
ready/available replicas. Only the observation path records `ready`; a missing
cluster configuration or failed rollout records an explicit failure instead.
This behavior has tests and disposable-PostgreSQL evidence, but the
current-candidate Ready E2E remains pending.

### 3. How does scaling handle concurrent requests?

The caller supplies `expected_replicas`. The API locks the deployment row,
checks that the precondition still matches, and persists an idempotent scale
operation. A stale precondition returns HTTP 409. This protects the scale
path; it is not a claim that every mutation path has global serialization.

### 4. How does rollback preserve history?

Deployments retain immutable desired-state revisions. Rollback validates the
target revision and expected current revision, then copies the target into a
new revision and enqueues an ordinary Worker operation. It does not overwrite
or delete historical revisions. Live Kubernetes rollback readiness has not
yet been verified.

### 5. What security boundaries are implemented and what remains unproven?

The API checks membership and roles server-side, passwords are hashed, access
tokens can be revoked, and API/Worker database roles are separated. Container
configurations use non-root users. The Worker RBAC manifest is namespace
scoped and omits Secret, delete, and cluster-wide permissions. Source and
database tests cover these controls; the proposed Kubernetes Role has not yet
been exercised against a disposable cluster with the current candidate.

## Known limitations to disclose

- No current-candidate end-to-end Kubernetes deployment has been verified
  with a dedicated Worker identity. Historical Ready evidence used
  administrator credentials.
- The Docker Linux engine was unavailable during the latest verification.
  Full local Compose startup, current image runtime validation, and CI image
  builds were therefore not completed.
- The safe demo Compose profile has no Worker; deployments remain queued.
- The dashboard Playwright test uses a mocked API and is not a deployment
  integration test.
- Project policy admission exists in the API, but Kubernetes
  Namespace/ResourceQuota/LimitRange reconciliation is not implemented.
- No genuine workflow screenshots or recorded video are available.
- The project is not production-ready; commercial billing, enterprise SSO,
  multi-region availability, and advanced quota/metrics infrastructure are
  outside the resume MVP scope.
