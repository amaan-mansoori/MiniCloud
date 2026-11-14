# 05 — Technology Stack Decisions

**Status:** Proposed technology selection. No package is installed or pinned.
Compatibility and supported versions must be rechecked from official release
documentation when implementation starts. Phase 0 tool inventory is not a
package compatibility test.

## Proposed stack at a glance

| Layer | Proposed choice | Status |
| --- | --- | --- |
| Web | Next.js + TypeScript + Tailwind CSS + shadcn/ui + Lucide | Selected proposal |
| API/worker | Python + FastAPI + Pydantic + SQLAlchemy + Alembic | Selected proposal |
| Password hashing | Argon2id via a maintained library (for example `pwdlib`/supported Argon2 backend) | Select exact maintained package/version during implementation review |
| Background jobs | PostgreSQL-backed job table and worker in same modular monolith | Proposed MVP; no broker |
| Data | PostgreSQL | Selected |
| Cluster integration | Kubernetes Python client | Selected |
| Local runtime | Docker Compose for API/worker/DB; kind for cluster | Selected proposal; current cluster context unconfigured |
| CLI | Python Typer + HTTPX | Proposed |
| Tests | pytest; frontend Vitest + Testing Library; Playwright E2E | Proposed |
| Metrics UI | Prometheus + Grafana optional | Optional; measure host cost |
| CI | GitHub Actions | Proposed after repository hosting decision |

## Frontend decisions

### Next.js and TypeScript

- **Fit/responsibility:** typed responsive dashboard, route/layout structure,
  browser session handling, build and test integration.
- **Alternatives:** Vite + React SPA (lighter and simpler); server-rendered
  backend templates (fewer dependencies but less rich client flow).
- **Advantages:** mature ecosystem and conventions; TypeScript contracts can
  align with generated OpenAPI clients.
- **Disadvantages/cost:** larger toolchain/runtime than static SPA; server
  rendering adds deployment and caching complexity not required for this
  console.
- **Security:** never serialize secrets/tokens to server-rendered HTML;
  server-side routes still call API authorization; prevent XSS and CSRF.
- **Compatibility:** MiniCloud pins Next.js 16.4.0 and React 19.0.0. Next.js
  requires Node.js >=20.9; the development baseline is Node.js 22.20.0.
  Validate these exact versions through the committed lockfile, lint, typecheck,
  production build, and browser smoke checks.
- **Decision:** use Next.js for coherent dashboard routing; reconsider if local
  resource/build complexity outweighs value.

### Tailwind CSS, shadcn/ui, Lucide

- **Fit:** design tokens/utilities, accessible composable primitives, consistent
  icons, reduced one-off styling.
- **Alternatives:** CSS Modules and custom components; another licensed UI kit.
- **Advantages:** custom visual control, composability, modest runtime when
  selected components are copied/compiled.
- **Disadvantages/cost:** shadcn components become project-owned source that
  must be updated/audited; Tailwind utility markup can become inconsistent.
- **Licensing/security:** verify licenses of each component/dependency at
  selection time and keep attribution/license notices; do not copy templates
  with unclear terms. No gallery component or asset is selected/reused by this
  proposal.
- **Compatibility:** Tailwind CSS 4.3.3 targets Safari 16.4+, Chrome/Edge 111+,
  and Firefox 128+. Older browsers are outside the web-console support target.
- **Decision:** use only needed accessible primitives and original styling.

### Charts and frontend testing

- **Chart library:** defer selection until actual metrics views exist. Consider
  a maintained accessible React visualization package only if plain tables and
  compact SVG do not meet need; chart must have textual/table equivalent and
  actual backend timestamps/source. Cost is bundle size and accessibility work.
- **Testing:** Vitest + Testing Library for components/interaction, Playwright
  for user flows. Alternatives: Jest/Cypress. Evaluate versions with chosen
  Next.js/browser matrix; not installed or tested.

## Backend decisions

### Python, FastAPI, Pydantic

- **Fit:** type-driven HTTP API, input validation, generated OpenAPI, shared
  Python ecosystem with Kubernetes client and worker.
- **Alternatives:** Django REST Framework (integrated admin/ORM, broader
  framework), Go HTTP framework (small deployment binary, typed concurrency),
  Node/NestJS (single-language UI/API).
- **Advantages:** explicit request schemas and good Python K8s integration;
  useful educational service boundaries.
- **Disadvantages/cost:** Python runtime/image and async-vs-blocking integration
  require care; process model must bound worker concurrency.
- **Security:** validate with Pydantic but enforce authorization/business
  invariants in service layer; do not use client schemas as sole safety control.
- **Compatibility:** official FastAPI docs describe typed APIs/OpenAPI; the
  official site fetch succeeded during Phase 1. Exact Python floor/dependency
  matrix not verified and must be checked before lockfile.

### SQLAlchemy, Alembic, PostgreSQL

- **Fit:** SQLAlchemy 2.x typed ORM/Core and explicit transactions; Alembic
  schema migration; PostgreSQL relational integrity, JSONB snapshots where
  appropriate, transactions and row locking.
- **Alternatives:** Django ORM, SQLite, MySQL.
- **Advantages:** FK/unique constraints and durable transactions; PostgreSQL
  supports concurrency and audit/history queries.
- **Disadvantages/cost:** migration discipline and operational backups required;
  one local DB adds memory/disk usage.
- **Security:** use parameterized queries; secrets excluded; least-privileged
  DB role; TLS/at-rest encryption is production deployment responsibility.
- **Compatibility:** SQLAlchemy 2.0 official docs were reachable; no versions
  selected, package install, migration, or DB compatibility test performed.

### Password hashing

Use Argon2id through a maintained, supported library selected against current
Python versions. Alternatives: scrypt, bcrypt. Argon2id is memory-hard but
requires measured parameters that fit constrained host and authentication
throughput. Store hash/algorithm parameters, never plaintext. Test upgrade path,
timing behavior, and dependency maintenance before release. Package choice and
parameters remain open.

### Background processing

**Proposed MVP:** PostgreSQL `deployment_jobs` table with atomic claim/lease,
attempt count, visibility timeout, bounded exponential backoff, and operation
idempotency. A worker process in the same codebase runs separately from the
API process. Alternatives: FastAPI `BackgroundTasks` (not durable across
process crash), Celery/RQ + Redis (mature broker ecosystem but extra service),
Kafka (high operational complexity). Postgres polling adds DB load and requires
careful `SKIP LOCKED`/lease recovery; measure before scaling. If job volume or
latency justifies a broker, record new ADR and migration plan.

## Data and infrastructure

### PostgreSQL

Persistent source for user/project intent, revision/job state, membership,
quota policy, audit and event history. Not the source of actual Pod readiness.
Costs: volume/backup/upgrade responsibilities; local Compose fit. Do not replace
with SQLite except possibly isolated unit test fixtures.

### Docker, Kubernetes, kind, Kubernetes Python client

Docker packages services and runs local development. kind runs real Kubernetes
nodes as containers for integration tests; it is not production HA. Python
client communicates with Kubernetes API; prefer typed client operations over
shelling out to `kubectl`, which couples behavior to CLI version/output and
quoting. `kubectl` remains an operator diagnostic tool. Use an explicit
configured context and never silently mutate it.

### Prometheus, Grafana, metrics-server, Loki

- **Prometheus/Grafana:** optional real metrics collection/query and dashboards;
  metrics-server is needed for basic Kubernetes resource metrics/HPA but is not
  interchangeable with Prometheus for arbitrary app metrics. Each consumes
  memory/storage and needs retention/configuration. Add only after host budget
  measurement; core UI must show metrics unavailable if absent.
- **Loki:** defer centralized logs; Kubernetes container logs/events can support
  MVP diagnostics. Loki adds storage, ingestion, access control, retention,
  and local resource cost. Reconsider when log retention/search demand exists.
- **HPA:** defer automatic scaling until a functioning metrics API and scale
  ownership policy are validated; Kubernetes HPA changes replicas and must not
  conflict with manual reconciler writes.

### GitHub Actions

Proposed CI for lint, type checks, unit/API/frontend tests, migrations,
containers, secret/dependency scanning, and optional kind integration. Cost:
runner minutes/artifact storage and secrets management. Restrict workflow
permissions, pin third-party actions by reviewed immutable refs, never expose
deploy credentials to untrusted pull requests. Repository hosting is not
confirmed; no workflow exists yet.

### pytest, Playwright

pytest for Python unit/integration/service/API/worker tests; Playwright for
browser E2E. Costs include browser installation and slower CI. Isolate database
schema and cluster namespace per test run; no uncontrolled cluster cleanup.

## Optional technology decision matrix

| Technology | Decision | Reason / cost-benefit |
| --- | --- | --- |
| Redis | Defer | No demonstrated cache/rate-limit/job requirement yet; adds service and failure mode |
| Kafka | Reject for MVP | Event streaming throughput/consumer needs not established; operational cost disproportionate |
| Helm | Optional/defer | Useful packaging ecosystem but absent in inspected PATH; basic local setup can use Compose/kind configuration; install only if later plan requires charts |
| Loki | Defer | Real bounded Pod logs suffice initially; central retention not yet justified |
| metrics-server | Optional for resource metrics/HPA | Required for Kubernetes resource metric APIs; adds local resources and must be checked in actual cluster |
| Prometheus/Grafana | Optional profile | Useful for real metrics and operator dashboard but not necessary for API/deployment MVP |
| Tracing backend | Defer | Correlation IDs and structured logs first; add tracing when cross-process diagnosis needs it |
| AI/LLM | Exclude MVP | Not required for deterministic deployment/authorization; risks and costs described in [AI design](10-ai-design.md) |

## Authoritative references and verification limits

Documentation consulted during this phase:

- [FastAPI official documentation](https://fastapi.tiangolo.com/) — page describes
  typed API patterns and OpenAPI/JSON Schema compatibility.
- [SQLAlchemy 2.0 official documentation](https://docs.sqlalchemy.org/en/20/) —
  points users to the unified tutorial and 2.0 documentation.
- [Prometheus overview](https://prometheus.io/docs/introduction/overview/) —
  time-series model, pull collection, and query features.
- [Grafana documentation](https://grafana.com/docs/grafana/latest/introduction/) —
  metrics/logs/traces exploration and alerting features.
- [Next.js documentation](https://nextjs.org/docs),
  [Kubernetes Deployment documentation](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/),
  [HPA documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/),
  [shadcn/ui](https://ui.shadcn.com/docs).

Attempts to fetch Next.js, Kubernetes, and shadcn official pages were blocked by
the research fetch network resolver; those references are provided for future
manual verification, not claimed as inspected. None of the external sources
was used to assert exact package versions, compatibility, installation,
license compliance, or benchmark results. Source access date: 2026-10-11.
Before implementation, record exact versions and validate official support
matrices, Python 3.14 support, dependency licenses, and actual resource cost.

## Current environment fit

Phase 0 observed Node 22.20.0, npm 11.6.2, Python 3.14.3, Docker 29.2.0,
kubectl 1.34.1, kind 0.33.0, Git 2.51.1, Helm missing; Linux engine reported
12 CPUs/~3.54 GiB. This does not prove FastAPI ecosystem compatibility with
Python 3.14 or sufficient memory for all proposed services. Recheck before
implementation.
