# 18 — Technical Interview Preparation

**Status:** Explains a proposed design. It is not a claim of personal
implementation accomplishments. Distinguish every answer's design from
verified implementation.

## 30-second introduction

“MiniCloud is an educational Kubernetes control plane I’m planning to build. It
will let a developer submit a container deployment through an API, CLI, or
small dashboard, while a Python modular monolith records desired state in
PostgreSQL and a background worker reconciles it through the Kubernetes API.
The key design focus is truthful rollout status, project authorization,
idempotent retries, and recovery from partial failure—not rebuilding
Kubernetes or claiming to be a production cloud.”

## Two-minute explanation

“The user authenticates, creates a project, and submits a constrained
deployment specification. The API validates identity, membership, quota, and
request shape synchronously. It commits a revision, job, idempotency record,
and audit event together in PostgreSQL, then returns 202 with an operation ID.
That only means the request is durably accepted. A worker claims the job under
a lease, uses a Kubernetes client adapter to apply deterministic Deployment
and Service objects in the project's namespace, and observes rollout
generation, ready replicas, and events. It records a terminal ready or failed
state. The API and UI/CLI show stale or unavailable data honestly. Retry after
a crash is safe because the request and resource identities are stable. The
database and Kubernetes cannot share a transaction, so reconciliation handles
the gap. This is a local educational proposal; kind is for integration testing,
not high availability.”

## Architecture walkthrough

Start with system context in [06-architecture.md](06-architecture.md). Trace
browser/CLI → auth → project authorization → service transaction → job lease →
Kubernetes adapter → observed state → API/UI. Explain the separate trust
boundaries: users never receive cluster credentials, and namespace grouping
does not make hostile multi-tenancy safe. Identify PostgreSQL as desired-state
source and Kubernetes as runtime observation source. Point to
[database design](07-database-design.md), [API](08-api-spec.md), and
[security](11-security.md).

## Key design explanations

- **Why Kubernetes:** it already schedules containers, runs controllers, and
  supplies Deployment/Service/namespace primitives; reimplementing a scheduler
  would duplicate responsibility.
- **Why modular monolith:** shared domain/auth logic with fewer operational
  services; API and worker can be separate processes. Microservices need
  demonstrated scaling/ownership benefits.
- **Asynchronous deploy:** long rollout time does not fit a request timeout.
  Persist work atomically, return 202 and operation ID, inspect completion via
  operation endpoint.
- **PostgreSQL/Kubernetes reconciliation:** no distributed atomic transaction.
  Commit desired revision/job; deterministic idempotent apply; record observed
  UID/generation; retry after crash and surface drift.
- **Idempotency:** unique actor/project/scope/key plus request hash. Same key
  same body returns same operation; changed body conflicts.
- **Retries/timeouts:** bounded retries with backoff/jitter and an operation
  deadline; never retry non-idempotent effect blindly.
- **Worker crash:** DB lease expires; another worker claims; stable object names
  and generation converge without duplicate resource.
- **Readiness:** require desired generation observed and configured ready
  replicas; object creation/API 2xx is not readiness.
- **Rolling deployment/rollback:** Kubernetes Deployment manages rollout.
  Rollback creates a new immutable MiniCloud revision copying an old supported
  spec; history remains and new rollout gets its own readiness check.
- **Service discovery/load balancing:** Kubernetes Service selects matching
  Pods and provides in-cluster virtual endpoint; external ingress is not MVP.
- **RBAC and namespace:** application membership controls users; Kubernetes
  service account controls adapter. Namespace plus quotas aid grouping but do
  not contain every hostile workload risk.
- **Quota:** API pre-check plus Kubernetes ResourceQuota/LimitRange backstop;
  cluster observation handles drift.
- **Logs/metrics:** logs/events from actual Kube sources; metrics from an
  installed provider. Missing source is unavailable, never fabricated.
- **Autoscaling:** HPA needs a functioning metrics API and exclusive/explicit
  ownership of replica changes; absent provider means no autoscaling claim.
- **Tests:** unit/service tests isolate logic; PostgreSQL and real kind validate
  transaction and cluster semantics; fault injection tests worker recovery.
- **Performance:** report separately API acceptance and rollout-to-ready,
  specify image cache, host, sample count, and latency percentiles; not
  production availability.
- **Non-guarantees:** no public cloud, SLA, HA, hostile tenancy, automatic
  cluster provisioning, or AI deployment decisions.
- **Trade-offs:** PostgreSQL queue is simpler but may bottleneck; namespace
  isolation is educational but limited; optional monitoring reduces local cost
  but decreases signal availability.

## Interview questions and answer outlines

1. **What problem does this solve?** A guided but transparent selected
   Kubernetes lifecycle; do not claim market validation.
2. **Why not use kubectl directly?** CLI is an operator tool; user-facing
   control plane needs durable identity, ownership, status, and async history.
3. **Why not microservices?** No measured need; modular boundaries suffice
   until independent scaling/ownership justifies network cost.
4. **What does 202 mean?** Validated/persisted acceptance, not ready.
5. **How do you prevent duplicate deploys?** Scoped idempotency key + payload
   hash and DB uniqueness transaction.
6. **What if API crashes after DB commit?** Job is durable and worker can claim.
7. **What if worker crashes after Kube apply?** Lease expiry and deterministic
   reconciliation observe/reapply same desired resource.
8. **Can PostgreSQL and Kube update atomically?** No; use durable intent,
   retryable reconciliation, visible divergence.
9. **How is ready determined?** Desired generation observed and configured
   ready replica condition; timestamp and source.
10. **How do you avoid lost updates?** ETag/generation optimistic concurrency;
    stale requests receive conflict.
11. **How does rollback work?** New revision references old revision and
    follows same apply/readiness process; history immutable.
12. **Why PostgreSQL?** Transactions, constraints, relational ownership and
    jobs; alternative document store lacks useful relational integrity here.
13. **Why a DB job table?** Small MVP avoids broker; lease/claim durable.
    Throughput must be measured; Kafka/Redis not free.
14. **Why a Kubernetes client library?** Structured API objects/errors rather
    than shell output parsing; version compatibility still needs tests.
15. **What is the RBAC boundary?** User/project role in API plus separate
    service-account role in cluster; neither substitutes for the other.
16. **Is namespace a security boundary?** Not sufficient alone for hostile
    workloads; node/runtime/CNI/admission/secrets matter.
17. **How do you validate image input?** Syntax/policy at API; pull success is
    asynchronous and only observed later.
18. **How do quotas work?** API validation for fast feedback plus actual
    ResourceQuota/LimitRange admission enforcement.
19. **How do you protect secrets?** Don't store raw values; use operator
    references, token hashing/rotation, redaction, access control.
20. **What if metrics-server is absent?** Return explicit unavailable, keep
    manual scaling; don't show zero or fake chart.
21. **How is autoscaling safe with manual scale?** One owner of replica field;
    defer HPA until source and interaction policy are tested.
22. **How do logs stay authorized?** Check membership before querying
    Kubernetes; bound lines/time and redact; no arbitrary exec.
23. **How do you test Kube behavior?** Mock unit boundaries plus real disposable
    kind integration for apply/readiness/rollback/failure.
24. **How do you test multi-tenant isolation?** Two users/projects, enumerate
    every endpoint and nested resources, assert no Kube read on deny.
25. **How do you handle worker retries?** Bounded backoff, lease, stable
    operation identity, terminal failure after budget, operator-visible error.
26. **Why not AI for failure diagnosis?** Deterministic reasons and evidence
    should work first; future LLM only advisory after privacy/evaluation review.
27. **What would you measure?** API latency and accepted/ready separately,
    error classes, recovery, host resources, sample counts and conditions.
28. **Does a passing test mean availability?** No; tests cover scenarios, not
    continuous real-world SLO.
29. **How would you scale the control plane?** Measure DB polling/pool first;
    horizontally scale API/workers under claim locks, add broker/service only
    with evidence.
30. **What is the largest open security concern?** Trusted-user educational
    scope and shared cluster; namespace alone is insufficient for hostile
    multi-tenancy.

## Follow-ups to prepare

For each answer, draw the relevant sequence, state the failure case, identify
the evidence needed, and name an alternative and its cost. If implementation
has not verified it, say “proposed behavior” and describe the planned test; do
not claim personal accomplishment or production readiness.
