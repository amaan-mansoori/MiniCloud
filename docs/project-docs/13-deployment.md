# 13 — Deployment Plan

**Status:** Proposed deployment workflow. No application services, Compose
files, Kubernetes manifests, CI workflows, or deployment were created.

## Local development topology

Proposed Windows/PowerShell setup:

```text
Browser / CLI -> Next.js dev server -> FastAPI API -> PostgreSQL (Compose)
                                             |
                                             +-> Worker process
                                                   |
                                                   +-> explicit kind context
```

Docker Compose may run PostgreSQL, API, worker, and web service. kind provides
the Kubernetes API and workload nodes. Optional metrics profile may add
metrics-server or Prometheus/Grafana only after measuring available resources.
Docker Desktop Linux engine and kind were present at the earlier Phase 0
inspection, but `kubectl` had no configured context; recheck before any setup.

## Prerequisites and configuration

Future README must state verified versions for Git, Docker Desktop/Linux
containers, kind, kubectl, Python, Node/npm, and optional Helm. No version
compatibility is certified by this plan. Do not install packages or change
contexts automatically.

Configuration categories (document names at implementation):

- PostgreSQL URL and least-privileged credentials.
- API signing/session keys from local ignored environment file; generate safe
  local-only values, never commit or print them.
- Explicit Kubernetes context name and namespace-provisioning permissions.
- Worker concurrency, timeout/backoff and resource policy limits.
- Image registry or `kind load docker-image` strategy; `imagePullPolicy` must
  align with local image availability.
- Optional metrics provider URL/capability setting.

Do not store secrets in tracked files or log env dumps. `.env.example` contains
placeholders only.

## Setup sequence and commands to validate later

Commands below are a planning outline, **not executed/validated commands**;
package manager scripts and paths do not exist yet:

1. Confirm Docker Linux engine is responsive with `docker info`.
2. Create or select an explicitly approved kind test cluster; do not alter
   existing kubectl context without approval. Document exact command and
   context safety guard after target is agreed.
3. Start PostgreSQL and API/worker/web containers with `docker compose up -d`.
4. Wait for DB health check; run reviewed migrations (`alembic upgrade head`).
5. Build/load test application image and confirm imagePullPolicy.
6. Start UI/CLI and follow deployment walkthrough.
7. Verify `/health/live`, `/health/ready`, operation progress, and actual Pod
   readiness; inspect logs/events if failure.
8. Record versions, context, commands, result, resource consumption.

Expected outputs are health ready, a 202 operation, and later an observed ready
revision for a pullable test image; these are expectations only, not results.

## Local operations and teardown

Use `docker compose logs --follow <service>` and `kubectl --context <approved-kind-context>
get ...` after context is confirmed. PostgreSQL migrations run separately and
are not auto-downgraded. Teardown must distinguish stopping Compose from
deleting cluster/data. `docker compose down` may preserve volumes; volume removal
or kind cluster deletion is destructive and must be a specifically scoped,
explicit developer action, never part of normal tests. No teardown command is
provided until project service names and cluster target exist.

## Common failures

| Symptom | Likely cause | Safe response |
| --- | --- | --- |
| Docker CLI but no daemon | Desktop engine stopped or context mismatch | Inspect Docker context/process; do not start/change without approval |
| `kubectl` no context | Cluster not configured | Stop before cluster command; select approved local test target |
| ImagePullBackOff | invalid/unreachable tag, registry auth, kind image missing | Inspect actual event; validate tag or explicitly load image |
| Pending Pod | quota/resource shortage, scheduling constraint | Inspect describe/events/quota, avoid raising limits blindly |
| API not ready | DB migration/connection or worker health failure | Inspect component health and safe logs |
| Metrics unavailable | metrics provider absent/stale | Show unavailable; do not fabricate samples or install implicitly |

## CI proposal

GitHub Actions stages: checkout with least permissions; verify lockfiles; format,
lint, type-check; unit/service/API tests; run PostgreSQL migration integration;
frontend component tests/build; dependency/secret scans; container build; kind
integration job on controlled runner where feasible; publish artifacts with
retention and no credentials. Pin third-party actions by reviewed immutable
commit and avoid deploying from untrusted pull requests. No GitHub repository
or workflow is confirmed.

## Staging and production-like future work

Before external deployment: operated/managed Kubernetes; TLS certificates,
DNS/ingress; secrets manager and rotation; identity/SSO; narrowly scoped RBAC;
network policies/admission controls; private registry/image verification;
metrics/logs/alerting; PostgreSQL HA/backup/restore; capacity/cost planning;
release approval and rollback; incident response; vulnerability management;
privacy/retention policies. kind is a local test cluster, **not** production
high availability. No cloud account, external endpoint, credentials, or
production-ready claim exists.
