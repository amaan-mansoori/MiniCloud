# 02 — Product Requirements Document

**Status:** Original proposed product contract, retained for context. Current
implementation and verification status are in the
[Resume MVP audit](../../reports/MINICLOUD-RESUME-MVP-AUDIT.md).

## 1. Product overview

MiniCloud is an educational, self-service Kubernetes control plane for
containerized applications. A modular monolith provides a versioned REST API
and asynchronous worker; a CLI and responsive dark console use the same
project-scoped operations. PostgreSQL stores control-plane records. Kubernetes
creates and schedules workloads.

## 2–3. Vision, mission, and problem

**Vision:** make a narrow but real application deployment lifecycle
understandable and inspectable.

**Mission:** provide safe, repeatable, truthful application operations over a
local Kubernetes cluster while teaching the control-plane responsibilities
that `kubectl apply` alone does not solve.

Developers currently coordinate workload configuration, status, logs,
permissions, and recovery across multiple tools. MiniCloud abstracts selected
operations; Kubernetes remains visible as the execution system.

## 4–5. Users and needs

| Persona | Needs and success outcome |
| --- | --- |
| Individual developer | Register, create a project, deploy a supported image, distinguish accepted from ready, view logs/events, scale, and roll back |
| Project owner / administrator | Invite/revoke project members, assign bounded roles, review audit history, establish resource quotas, control project lifecycle |
| Platform operator | Configure cluster access, run workers safely, inspect health/reconciliation/queue failures, maintain migrations and backups, avoid granting cluster-admin to users |

## 6. Product goals

1. Make a supported image-to-ready path inspectable end to end.
2. Enforce server-side project authorization on every protected operation.
3. Preserve desired configuration, revisions, and operation history in
   PostgreSQL.
4. Recover safely from worker retries and PostgreSQL/Kubernetes partial failure.
5. Provide consistent truthful feedback across API, CLI, and UI.
6. Validate critical cluster behavior against kind, not mocks alone.

## 7. Non-goals

No cloud-provider abstraction, cluster provisioning, scheduler, arbitrary
manifest submission, enterprise/hostile multi-tenancy, SLA, billing, managed
registry, full secrets product, service mesh, automatic remediation, AI
deployment decisions, or production certification.

## 8. Core capability priorities

See the single-source acceptance catalog in [requirements](03-requirements.md).
At a glance:

| Capability | Priority | MVP interpretation |
| --- | --- | --- |
| Accounts, projects, membership, server RBAC | MUST | Email/password account, project owner/member roles, ownership checks |
| Image deployment, Deployment/Service, namespace | MUST | Bounded API-created resources; no arbitrary manifests |
| Lifecycle, rollout/readiness, failure diagnostics | MUST | Durable asynchronous operation; real observed conditions |
| Manual scale and revision rollback | MUST | Validated replica bounds; rollback creates a new revision |
| History, logs, events, quotas, audit | MUST | Project-authorized and bounded access |
| REST/OpenAPI, CLI, responsive dashboard | MUST | Essential flows supported by each interface |
| Metrics and autoscaling | SHOULD | Integrate only when a real provider is installed; otherwise explicit unavailable state |
| Configurable alerts | SHOULD | Persist bounded rules; delivery path must be implemented/tested before advertised as operational |
| Private registries, ingress/TLS, advanced security scanning | COULD | Later design, security review, and tests required |

## 9. User journeys

1. **Onboard:** register → login → create project → obtain owner role.
2. **Deploy:** submit image/configuration → synchronous authentication,
   authorization, and validation → `202 Accepted` with operation ID → worker
   applies namespaced resources → user polls/listens to status → readiness is
   reported only from observed Kubernetes conditions.
3. **Diagnose:** inspect revision timeline → authorized events/logs → distinguish
   failed image pull, scheduling, and readiness causes where evidence permits.
4. **Operate:** inspect current replica count → request manual scaling → see
   observed replicas and operation result.
5. **Recover:** choose an eligible historical revision → authorize rollback →
   create a new revision containing the prior desired configuration → observe
   it through the same rollout contract.

## 10. Functional scope

Detailed IDs, validation, failure behavior, acceptance criteria, and verification
are in [03-requirements.md](03-requirements.md), with traceability in
[TRACEABILITY.md](TRACEABILITY.md). MVP does not expose raw manifests or
unrestricted Kubernetes objects.

## 11. Non-functional requirements

Security, accuracy, idempotency, retries, reconciliation, performance,
accessibility, and reproducibility are specified with testable criteria in
[requirements](03-requirements.md). Proposed numeric targets remain provisional
until the local host, kind configuration, and representative workload are
measured.

## 12. UX principles

Permanent dark theme; restrained near-black/charcoal surfaces; readable
typography; useful tables; keyboard access; clear statuses; specific errors;
real data only; explicit unavailable/empty/loading states; destructive
confirmation; no decorative analytics. See [09-ui-ux.md](09-ui-ux.md).

## 13–15. Security, performance, reliability

- Authenticate identities, then independently authorize each project operation.
- Give control-plane service accounts only the Kubernetes verbs/resources
  required for supported operations.
- Treat tenant isolation as an unproven security boundary until tested; do not
  market hostile multi-tenancy.
- Keep long work outside request handlers; use bounded retries and explicit
  operation deadlines.
- Return accepted/pending, progressing, ready, or failed accurately. On
  uncertain cluster state, expose unknown/stale observations rather than
  success-shaped defaults.
- Provisional performance objectives are evaluation hypotheses, not promises.

## 16. Compatibility and accessibility

The web console targets Safari 16.4+, Chrome/Edge 111+, and Firefox 128+.
Tailwind CSS 4 relies on modern CSS features and does not support older browser
versions. Treat this as a product compatibility contract and revisit it when
the framework major version or user requirements change. The target matrix has
not yet been validated by browser-specific E2E runs.

Design for keyboard-only operation, visible focus, semantic controls,
accessible names, sufficient contrast, non-color status labels, reduced
motion, and responsive narrow screens. Accessibility must be verified with
automated checks and manual keyboard/screen-reader review; no conformance claim
is made yet.

## 17. Success metrics

Requirement coverage, clean setup reproducibility, success/failure deployment
test outcomes, readiness time distribution, API latency/error rate,
authorization-negative tests, retry/idempotency correctness, recovery after
worker interruption, resource use, and usability task completion. Procedures
and limitations live in [14-evaluation.md](14-evaluation.md).

## 18–20. Constraints, assumptions, dependencies, risks

**Environment prerequisites:** Windows PowerShell and Docker Desktop with
Linux containers are used by the documented local procedures. Kubernetes
procedures additionally require kubectl and kind. Verify the actual installed
versions, engine availability, and selected cluster context before use; this
document does not assert local machine state.

**Assumptions:** local kind is the first cluster; images can be pulled or loaded
locally; PostgreSQL can run in Compose; one operator-managed cluster is enough
for the MVP.

**Dependencies:** Docker Desktop, kind, PostgreSQL, reachable image registry or
local image loading, Kubernetes API, metrics provider only for metrics/HPA,
GitHub Actions only after a remote repository is configured.

**Risks:** resource limits on host, cluster/DB partial failure, image pull
credentials, ambiguous namespace isolation, worker duplication, metrics
absence, and scope. Mitigations are specified in security, architecture, and
deployment documents.

## 21. MVP scope

One local cluster; account registration/login; project owner/member roles;
project namespace provisioning; supported image-based Deployment + ClusterIP
Service; bounded CPU/memory/replicas; asynchronous deployment/revision status;
readiness and failure diagnostics; history; manual scale; rollback as a new
revision; authorized bounded logs/events; REST/OpenAPI; usable CLI/dashboard;
audit trail; tests, CI, and runbooks. Each item remains **proposed** until
implemented and evidenced. Metrics/HPA and alerts are conditional SHOULD
features, not prerequisites for claiming a basic deployment works.

## 22. Post-MVP roadmap

1. Validate workflows with users and improve error diagnosis.
2. Add metrics provider integration and HPA after local resource measurements.
3. Add alert persistence/delivery with deduplication and actionability.
4. Evaluate private registry credentials, secret reference UX, ingress/TLS.
5. Reassess multi-cluster operation, stronger tenant isolation, and production
   operations only with a separate threat model and deployment target.

## 23. Release acceptance criteria

- All MUST requirements have evidence linked in traceability.
- Migrations apply to an empty DB and a previous schema; upgrade test passes.
- API schema and examples match actual endpoints.
- Success, failure, duplicate, authorization-negative, and worker-recovery
  workflows pass against isolated test resources.
- UI/CLI distinguish accepted from ready and show missing observability data.
- Security review finds no unresolved critical/high issues for stated scope.
- A clean local start/teardown is repeatable and documented.
- No production/high availability claim is made.

## 24. Open questions

1. Is registration open or operator-invited?
2. Which identity/session flow is required for browser and CLI?
3. Is namespace-per-project enough for the educational threat model, and what
   cluster admission/network controls are available?
4. Which image registries and image trust policies are needed?
5. Which metrics provider will be installed and who owns it?
6. Are alerts in MVP delivery channels email, webhook, or in-console only?
7. Which ingress and DNS model is expected locally?
8. Which browsers are in the support matrix?
9. What data retention/deletion obligations apply?
10. Which user's hardware budget is the local resource ceiling?

## 25. Explicitly out of scope

Cluster creation, control-plane replacement, cloud billing, public marketplace,
arbitrary YAML, shell access to Pods, cluster-admin delegation, guaranteed
uptime, managed backups, multi-region failover, unrestricted production
secrets, AI-generated/automated remediation, and any unsupported claim of
commercial cloud equivalence.
