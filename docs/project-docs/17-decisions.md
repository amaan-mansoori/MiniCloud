# 17 — Engineering Decisions Index

**Status:** The following are proposed decisions for review, not evidence of
implementation. ADRs capture major choices and reconsideration triggers.

| ID / topic | Status | Proposed decision and rationale | Positive / negative effects | Security / operations | Reconsider when |
| --- | --- | --- | --- | --- | --- |
| DEC-001 Control plane architecture | Proposed | Modular monolith; API/worker separate processes in one codebase. Simple deployment and shared domain logic. [ADR-0001](../adr/ADR-0001-control-plane-architecture.md) | Clear boundaries and fewer network dependencies; shared release/DB coupling | Easier unified auth; worker resource contention | Measured independent scaling, fault-isolation, or team ownership need |
| DEC-002 Kubernetes integration | Proposed | Kubernetes Python client behind allowlisted adapter; no raw manifests/kubectl subprocess. [ADR-0002](../adr/ADR-0002-kubernetes-integration.md) | Typed operations and error handling; client API version/compatibility must be maintained | Narrow service account, explicit context; operational client upgrades | Required API operation absent or client compatibility constraint |
| DEC-003 Persistence | Proposed | PostgreSQL for intent/history/jobs; migrations via Alembic. [ADR-0003](../adr/ADR-0003-database-and-persistence.md) | Transactions, constraints, recovery; DB operational cost | Least privilege, backup/retention responsibility | Scale or deployment model makes another store necessary with evidence |
| DEC-004 AuthN/AuthZ | Proposed | Argon2id + short access / hashed rotating refresh session; project roles checked server-side. [ADR-0004](../adr/ADR-0004-authentication-and-authorization.md) | Revocation and ownership model; token lifecycle complexity | Fail-closed membership, CSRF, no user Kube credentials | External identity provider or browser/CLI usability needs validated |
| DEC-005 Observability | Proposed | Structured logs + IDs and real Kube observations; optional Prometheus/Grafana; defer Loki. [ADR-0005](../adr/ADR-0005-observability-approach.md) | Useful evidence without mandatory stack; limited history initially | Redaction and project ACL; optional stack adds resource cost | Measured history, alerting, retention requirement |
| DEC-006 PostgreSQL-backed worker | Proposed | Durable job/lease table, one worker process; no Redis/Kafka for MVP. | Minimal dependencies; polling load and DB coupling | Recovery via leases/backoff, DB capacity | Measured queue throughput/latency makes broker beneficial |
| DEC-007 Desired-state reconciliation | Proposed | DB desired revision + deterministic Kube identity; observe/reconcile partial failures. | Recoverable state; eventual consistency visible | Foreign identity conflicts stop; no atomic cross-system commit | Cluster controller/operator model becomes better justified |
| DEC-008 Namespace/RBAC | Proposed | Namespace per project, quota/limits, least-privilege control-plane identity; trusted educational users only. | Understandable scope; namespace not hostile isolation | No cluster-admin; admission/network policy gaps explicit | Stronger isolation or shared cluster/public users required |
| DEC-009 CLI implementation | Proposed | Python Typer/HTTPX client over public versioned API. | Reuses backend language/client; package distribution work | OS keychain; never embeds cluster creds | Need independent binary/other language ecosystem |
| DEC-010 AI exclusion | Proposed | No LLM for core; future advisory-only summary behind security review. | Deterministic core and no inference cost; fewer novelty features | Avoid prompt injection/unsafe actions in MVP | User research identifies valuable, safe, evaluated need |

## Requirement and implementation mapping

Each decision references requirement IDs in [traceability](TRACEABILITY.md).
Implementation areas remain planned; no component is claimed to exist. For
alternatives, trade-offs, security/operations detail and review conditions, see
the linked ADR. Do not mark a decision Accepted until human approval or
implementation evidence is recorded.
