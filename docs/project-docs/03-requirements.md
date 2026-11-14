# 03 — Functional and Non-functional Requirements

**Status:** Proposed and testable. A requirement's priority is not evidence that
it is implemented. Stable IDs are referenced by the story catalog and
[traceability matrix](TRACEABILITY.md).

## Priority meanings

- **MUST:** release-blocking MVP behavior.
- **SHOULD:** valuable, but may be deferred with an explicit unavailable state.
- **COULD:** optional future scope.

Each requirement below includes rationale, preconditions, behavior, acceptance,
failure behavior, verification, story, and interface/component links. Numeric
values marked *provisional* are proposed starting constraints, to be tuned by
measured local resource and workload tests.

## Functional requirements

### FR-001 — Register and authenticate accounts
- **Priority:** MUST. **Rationale:** identify an actor before accepting protected operations.
- **Preconditions:** service healthy; registration policy permits enrollment.
- **Behavior:** validate input, hash passwords with a maintained Argon2id implementation, create an account, and establish a revocable session. Never persist raw passwords.
- **Acceptance:** valid registration can log in; duplicate identity returns stable conflict; invalid credentials do not reveal account existence; logout invalidates refresh/session.
- **Failure:** validation uses `400`; duplicate uses `409`; auth failure uses generic `401`; persistence failure is `503`/`500` with correlation ID, no token leakage.
- **Verification:** service/API tests for hashing, duplicate, login, logout, expiry, and generic errors. **Story:** US-001. **Links:** `/api/v1/auth/*`, AuthService, `users`, `sessions`.

### FR-002 — Create and manage projects
- **Priority:** MUST. **Rationale:** scope ownership and workload isolation.
- **Preconditions:** authenticated actor.
- **Behavior:** create project with unique stable slug and owner membership; list/get only projects the actor may access; update/delete requires owner role. Deletion is soft/tombstoned while workloads require cleanup.
- **Acceptance:** creator is owner; name/slug minimum and maximum lengths are enforced after trimming; slugs are stored lowercase; unrelated project is indistinguishable from absent where policy requires; deletion reports pending cleanup and cannot silently orphan active resources.
- **Failure:** duplicate slug `409`; missing `404`; cleanup failure retained as visible pending state.
- **Verification:** DB/API tests including cross-project reads/deletes. **Story:** US-002. **Links:** ProjectService, `/api/v1/projects`, `projects`, `project_memberships`.

### FR-003 — Manage project membership
- **Priority:** MUST. **Rationale:** delegate without sharing accounts.
- **Preconditions:** owner/admin permission; invitee account exists for MVP.
- **Behavior:** add/revoke members with `owner` or `member` role; only owners change membership/quotas; membership cannot remove the last owner.
- **Acceptance:** role changes take effect on the next request; last owner cannot be removed; self-service elevation is impossible.
- **Failure:** forbidden `403`; missing target `404`; invariant conflict `409`.
- **Verification:** authorization matrix and concurrent last-owner tests. **Story:** US-003. **Links:** ProjectService, `/api/v1/projects/{id}/members`, membership entity.

### FR-004 — Submit a deployment revision asynchronously
- **Priority:** MUST. **Rationale:** rollout duration exceeds an HTTP request.
- **Preconditions:** authenticated project member, authorized project, project namespace available, quota permits request.
- **Behavior:** validate bounded request; persist immutable revision, operation/job, idempotency key, and audit event in one DB transaction; return `202` + operation ID. Worker creates/updates supported Deployment and ClusterIP Service.
- **Acceptance:** accepted response means durable request only; user can poll status; no `ready` response before observed conditions.
- **Failure:** invalid input `422`; quota `409`; Kube API unavailable leaves retryable operation visibly pending/degraded; only observed terminal failure or exhausted apply retries ends `failed` with safe reason.
- **Verification:** API contract, DB transaction, worker + kind deployment. **Story:** US-004. **Links:** `/api/v1/projects/{id}/deployments`, DeploymentService, `deployment_revisions`, `deployment_jobs`.

### FR-005 — Validate image and deployment configuration
- **Priority:** MUST. **Rationale:** reject unsafe or structurally invalid input before scheduling work.
- **Preconditions:** deployment/scale request received.
- **Behavior:** validate OCI-like image reference syntax, non-empty image, supported port/probe/env schema, replicas, CPU/memory quantities, label/slug constraints, and immutable revision snapshot. Flag `latest` as disallowed by proposed MVP policy.
- **Acceptance:** malformed input receives field-specific error; syntactically valid but unavailable image may be accepted and later fail with `ImagePullBackOff`; no arbitrary Pod security fields pass through.
- **Failure:** errors do not echo secrets; registry reachability is not falsely claimed by syntax validation.
- **Verification:** schema boundary/property tests and negative kind cases. **Story:** US-004. **Links:** validation layer, deployment POST/PATCH, revision JSON.

### FR-006 — Provision isolated project namespace and quota policy
- **Priority:** MUST. **Rationale:** create a controllable project boundary and resource budget.
- **Preconditions:** authorized project setup; controller identity can manage required namespace resources.
- **Behavior:** create a namespaced Kubernetes namespace with stable project identity labels, ResourceQuota, and LimitRange; use deterministic names and reconcile if already present.
- **Acceptance:** repeated provisioning converges; quota/limit values match persisted policy; conflict with foreign namespace identity stops safely.
- **Failure:** insufficient Kubernetes RBAC or API failure is reported as setup failure/retry, never treated as success.
- **Verification:** RBAC integration and kind object inspection. **Story:** US-002/US-013. **Links:** NamespaceProvisioner, Kubernetes adapter, `resource_policies`.

### FR-007 — Apply bounded Deployment and Service resources
- **Priority:** MUST. **Rationale:** create actual schedulable/reachable workload.
- **Preconditions:** revision validated and namespace reconciled.
- **Behavior:** generate a constrained Deployment and ClusterIP Service using stable names/selectors, non-privileged security context where supported, resource requests/limits, and configured health checks. No user-supplied raw manifest.
- **Acceptance:** Service selector matches Pods; repeated apply does not duplicate resources; unsupported privilege/host networking fields cannot be expressed.
- **Failure:** Kube validation/permission errors recorded with sanitized reason and correlation identifiers.
- **Verification:** kind integration; inspect actual spec and rollout. **Story:** US-004. **Links:** Kube adapter, Deployment/Service APIs.

### FR-008 — Track rollout state and readiness truthfully
- **Priority:** MUST. **Rationale:** accepted intent is not rollout success.
- **Preconditions:** revision persisted.
- **Behavior:** operation state follows `accepted → queued → applying → progressing → ready` or a failure terminal path. Deadline expiration is exposed as recoverable `timed_out` while observation continues; late observed readiness can recover it. Worker persists observations; `ready` requires desired generation observed and configured ready replicas available. See state contract in [architecture](06-architecture.md).
- **Acceptance:** API, CLI, and UI use the same state; pending/unknown/stale are not displayed as ready; failed probe and image pull are distinguishable when Kubernetes evidence supports it.
- **Failure:** observer/API outage marks observation stale/unavailable; do not fabricate state.
- **Verification:** state-transition unit tests, polling contract, kind success/failure tests. **Story:** US-005/US-006. **Links:** status endpoint, `deployment_revisions`, `deployment_events`.

### FR-009 — Preserve revision and event history
- **Priority:** MUST. **Rationale:** diagnosis and safe rollback require immutable change context.
- **Preconditions:** project access.
- **Behavior:** append revisions and timestamped operation/deployment events; list newest first with cursor pagination; redact secret-like values.
- **Acceptance:** every accepted request has stable revision/job identifiers and actor; events correlate request/worker/Kubernetes object UID where available.
- **Failure:** event-write failure within critical transaction prevents acceptance; subsequent observation failure is retryable and surfaced.
- **Verification:** persistence, pagination, redaction, and recovery tests. **Story:** US-007. **Links:** history/event endpoints, revision/event tables.

### FR-010 — Manually scale replicas
- **Priority:** MUST. **Rationale:** allow bounded load adjustment.
- **Preconditions:** member permission; active deployment; quota allows target.
- **Behavior:** validate replica integer against project and platform max; create a new desired revision/operation, update Deployment, and observe count. Kubernetes HPA must not simultaneously own the same replica field unless a later design explicitly coordinates it.
- **Acceptance:** competing scale requests use optimistic generation/ETag; stale request gets `409`; response stays pending until observed.
- **Failure:** Kube outage leaves operation retryable; over-limit returns `422`/quota conflict.
- **Verification:** kind and concurrent conditional update tests. **Story:** US-008. **Links:** `/scale`, revision/jobs, Deployment.

### FR-011 — Roll back to an eligible revision
- **Priority:** MUST. **Rationale:** recover known-good desired configuration.
- **Preconditions:** project owner/member permitted; target revision belongs to deployment and is retained.
- **Behavior:** create a **new** revision copying the selected immutable desired configuration and recording `rollback_of_revision_id`; apply via normal worker path.
- **Acceptance:** rollback has a new ID and audit record; no history is erased; readiness is independently verified.
- **Failure:** missing/ineligible target `404`/`409`; partial Kube error produces failed operation while preserving target and new revision record.
- **Verification:** rollback success/failure against kind. **Story:** US-009. **Links:** rollback endpoint, revisions, worker.

### FR-012 — Retrieve authorized logs and Kubernetes events
- **Priority:** MUST. **Rationale:** explain observed failure.
- **Preconditions:** project access and workload identity known.
- **Behavior:** fetch bounded time/line windows from actual Pods and Kubernetes Events; cap bytes/count, redact common credential patterns, and identify truncation/source time. Access is read-only using a dedicated API diagnostics identity, not the Worker identity.
- **Acceptance:** another project's logs are denied; no container or arbitrary command execution; unavailable logs are explicitly unavailable.
- **Failure:** Pod gone/Kube API failure is a typed unavailable/not-found outcome, not an empty-success payload.
- **Verification:** API tests verify member authorization, bounded responses, credential-pattern redaction, project/Pod filtering, and explicit unavailable behavior. Live kind diagnostics and the proposed API Role remain unverified. **Story:** US-010/US-011. **Links:** logs/events endpoints, Kube adapter.

### FR-013 — Expose real metrics and honest absence
- **Priority:** SHOULD. **Rationale:** resource observation supports operations.
- **Preconditions:** metrics provider installed and reachable, workload authorized.
- **Behavior:** query a configured Metrics API/provider and return timestamped CPU/memory values and source freshness.
- **Acceptance:** absent provider returns `metrics_unavailable`; stale sample is marked stale; no synthetic utilization values are returned.
- **Failure:** provider error remains visible and does not affect deployment readiness.
- **Verification:** adapter integration where provider exists; deterministic missing/stale tests. **Story:** US-012. **Links:** metrics endpoint, MetricsAdapter.

### FR-014 — Enforce project quotas and deployment limits
- **Priority:** MUST. **Rationale:** limit accidental resource exhaustion.
- **Preconditions:** project policy configured.
- **Behavior:** persist allowed CPU/memory/replica bounds; enforce at API validation and Kubernetes ResourceQuota/LimitRange.
- **Acceptance:** request exceeding application policy is rejected before enqueue; cluster quota remains a backstop; actual quota state is inspectable by owner/operator.
- **Failure:** quota drift blocks new requests and produces an actionable condition.
- **Verification:** API unit and kind limit enforcement tests. **Story:** US-013. **Links:** quota API, `resource_policies`, namespace objects.

### FR-015 — Provide a CLI for MVP lifecycle operations
- **Priority:** MUST. **Rationale:** automation and developer workflow parity.
- **Preconditions:** CLI installed and authenticated.
- **Behavior:** expose login/logout, project list/create, deploy, status/watch-by-polling, logs/events, scale, history, rollback; outputs stable JSON optionally and readable text by default.
- **Acceptance:** CLI reports operation accepted separately from ready; nonzero exit for terminal failure/auth/unavailable; does not write tokens to shell history.
- **Failure:** API error code and correlation ID are printed, secrets omitted.
- **Verification:** CLI command tests against API test server and end-to-end smoke flow. **Story:** US-014. **Links:** API, CLI client.

### FR-016 — Provide a responsive dashboard
- **Priority:** MUST. **Rationale:** discoverability and visual lifecycle inspection.
- **Preconditions:** authenticated browser session.
- **Behavior:** render project/deployment screens, forms, status timeline, events/logs, manual scale, rollback confirmation, role-aware controls.
- **Acceptance:** all controls have defined behavior; unauthorized actions are hidden or disabled for UX but still server-denied; unavailable values are explicit.
- **Failure:** loading/error/empty/forbidden/unavailable states are distinguishable; no fabricated dashboard counts.
- **Verification:** component, keyboard/accessibility, API integration, and E2E tests. **Story:** US-015. **Links:** dashboard screens, API.

### FR-017 — Configure and deliver actionable alerts
- **Priority:** SHOULD. **Rationale:** failures need a notification path beyond manual polling.
- **Preconditions:** supported alert condition and delivery channel configured by owner/operator.
- **Behavior:** persist threshold/event rule, evaluate real data, deduplicate within an incident window, and record delivery outcome.
- **Acceptance:** rule shows source/window/severity; delivery failure is observable; no UI claims active delivery without configured tested channel.
- **Failure:** missing metric source disables dependent rule visibly.
- **Verification:** evaluator unit tests and test-channel delivery/duplicate/recovery integration. **Story:** US-016. **Links:** alert endpoints, `alert_rules`, `alert_events`.

### FR-018 — Version API and publish OpenAPI contract
- **Priority:** MUST. **Rationale:** stable client behavior.
- **Preconditions:** API running.
- **Behavior:** place user operations under `/api/v1`, expose generated schema/docs in configured environment, standardize error envelope and correlation ID.
- **Acceptance:** contract tests validate response schema/status; breaking change requires new version or compatibility policy.
- **Failure:** unknown routes return stable `404`; internal exception hides stack trace.
- **Verification:** OpenAPI snapshot/contract tests. **Story:** US-014. **Links:** all API routes.

### FR-019 — Make create/scale/rollback retries idempotent
- **Priority:** MUST. **Rationale:** network retry must not duplicate intent.
- **Preconditions:** mutating API request carries `Idempotency-Key` and target project scope.
- **Behavior:** persist key + normalized request hash + response/operation in a unique `(project, key)` scope. Same key/same payload within a project returns the same operation; same key/different payload within that project returns `409`.
- **Acceptance:** concurrent duplicates produce one revision/job; reusing a key in another project is independent; expired key behavior is documented and tested.
- **Failure:** DB transaction abort means no success response; client may safely retry with same key.
- **Verification:** concurrent DB/API idempotency tests. **Story:** US-017. **Links:** operation endpoints, jobs/idempotency store.

### FR-020 — Report health and platform capability
- **Priority:** MUST. **Rationale:** clients/operators need to distinguish process health from dependencies.
- **Preconditions:** API process running.
- **Behavior:** liveness reports process responsiveness; readiness reports required DB/worker control-plane dependencies; capability endpoint reports configured optional metrics/alerts support without secrets.
- **Acceptance:** health checks do not claim Kubernetes ready if unreachable; optional capability is explicit.
- **Failure:** dependency problem returns non-ready status with safe component code.
- **Verification:** dependency fault tests. **Story:** US-018. **Links:** `/health/*`, `/api/v1/capabilities`.

## Non-functional requirements

### NFR-001 — API latency measurement
- **Priority:** SHOULD. **Rationale:** establish a reproducible usability baseline.
- **Preconditions:** representative local test environment documented.
- **Behavior:** record route/method/status latency histograms excluding secrets and high-cardinality user labels.
- **Acceptance:** evaluation reports p50/p95, request mix, sample count, host, and warm/cold conditions; provisional target: read API p95 ≤ 500 ms at 5 concurrent local clients, excluding logs and Kubernetes mutations; validate and revise after baseline.
- **Failure:** measurement failure is reported, never replaced by estimated data.
- **Verification:** repeatable load procedure in evaluation plan. **Story:** US-018. **Links:** API middleware, OPS-002.

### NFR-002 — Accurate deployment status
- **Priority:** MUST. **Rationale:** avoid success-shaped lies.
- **Preconditions:** persisted operation and observation source.
- **Behavior:** distinguish requested, persisted, applied, observed, and stale state.
- **Acceptance:** ready requires matching observed generation plus ready replicas; stale age/source are exposed; status tests inject DB/Kube disagreement.
- **Failure:** unknown/stale stays unknown/stale.
- **Verification:** worker state tests and kind reconciliation tests. **Story:** US-005. **Links:** FR-008, FR-020.

### NFR-003 — Safe retry and crash recovery
- **Priority:** MUST. **Rationale:** workers and Kube API calls can fail independently.
- **Preconditions:** operation persisted before execution.
- **Behavior:** lease/claim jobs atomically with expiry; bounded exponential backoff with jitter; retries reconcile deterministic object names and generation.
- **Acceptance:** killed worker's expired lease is reclaimed; same operation does not create duplicate objects; terminal exhaustion is visible.
- **Failure:** exhausted or poison work is quarantined/failed with safe diagnostics and operator visibility.
- **Verification:** controlled worker-kill integration test. **Story:** US-019. **Links:** jobs, worker, OPS-003.

### NFR-004 — PostgreSQL/Kubernetes consistency
- **Priority:** MUST. **Rationale:** no distributed atomic commit exists.
- **Preconditions:** durable DB desired revision.
- **Behavior:** DB is source of desired intent; Kubernetes is observed execution state; periodic/on-demand reconciliation detects drift.
- **Acceptance:** partial failure can be replayed; state records last observed generation/time; drift is not silently overwritten if foreign identity is detected.
- **Failure:** observer outage marks status stale and pages/logs operator per policy.
- **Verification:** failure injection between commit/apply/observe.
- **Story:** US-019. **Links:** architecture, DB, deployment controller.

### NFR-005 — Concurrency control
- **Priority:** MUST. **Rationale:** prevent lost update and competing operations.
- **Preconditions:** concurrent mutation on same deployment.
- **Behavior:** use expected generation/ETag or DB optimistic version; serialize/apply one active operation per deployment or explicitly supersede a queued revision.
- **Acceptance:** stale update yields `409`; one active writer owns transition; every winner/loser response is deterministic.
- **Failure:** transient serialization failure may retry only with same idempotency key.
- **Verification:** concurrent integration tests. **Story:** US-008. **Links:** FR-010, job claim.

### NFR-006 — Bounded resource usage
- **Priority:** MUST. **Rationale:** local Windows/Docker host is constrained.
- **Preconditions:** local setup.
- **Behavior:** API/worker concurrency, Pod counts, database, optional monitoring stack have documented limits; no monitoring stack is mandatory for core build.
- **Acceptance:** evaluate idle and representative workload memory/CPU; proposed local Docker-engine budget for base control plane + DB + one small kind cluster is ≤ 3.0 GiB, provisional and must be measured against the previously observed 3.54 GiB engine limit; keep optional monitoring off by default and report if the base profile cannot fit.
- **Failure:** resource pressure produces actionable health/worker queue signal.
- **Verification:** repeatable resource sampling. **Story:** US-019. **Links:** deployment and evaluation.

### NFR-007 — Accessibility and responsive behavior
- **Priority:** MUST. **Rationale:** core tasks must not depend on pointer or color alone.
- **Preconditions:** UI available.
- **Behavior:** semantic labels, keyboard focus, status text/icons, responsive layout, reduced motion.
- **Acceptance:** all primary flows keyboard-operable; automated accessibility checks have no unresolved critical violations; manual review documents limitations.
- **Failure:** inaccessible third-party component is replaced or wrapped with an accessible original.
- **Verification:** axe-style scan plus manual keyboard/screen-reader checklist. **Story:** US-015. **Links:** UI spec.

### NFR-008 — Maintainability
- **Priority:** MUST. **Rationale:** educational code must remain inspectable.
- **Preconditions:** repository implementation begins.
- **Behavior:** modular boundaries, typed request/response models, migrations, runbooks, architecture records, CI checks.
- **Acceptance:** services depend on domain interfaces, not route/Kube client globals; lint/type/test commands documented.
- **Failure:** bypasses require recorded reason and review.
- **Verification:** CI and code review checklist. **Story:** US-014. **Links:** architecture, ADRs.

### NFR-009 — Reproducibility
- **Priority:** MUST. **Rationale:** portfolio claims must be repeatable.
- **Preconditions:** documented supported host.
- **Behavior:** pin/lock dependencies after selection; deterministic migration/setup scripts; record tool versions.
- **Acceptance:** clean setup follows docs without manual undocumented edits; teardown scope safe.
- **Failure:** unavailable external service is called out; no fake fallback.
- **Verification:** clean-machine/CI setup exercise. **Story:** US-020. **Links:** CI, deployment.

### NFR-010 — Cursor pagination and bounded payloads
- **Priority:** MUST. **Rationale:** prevent unbounded reads.
- **Preconditions:** list/log/events query.
- **Behavior:** stable cursor order `(created_at,id)`, cap page size at provisional 100, apply log byte/time limits.
- **Acceptance:** next cursor does not duplicate/reorder stable records; invalid cursor returns typed `400`; payload limit reported.
- **Failure:** no silent truncation; return `truncated=true` with source metadata.
- **Verification:** pagination boundary/property tests. **Story:** US-007/US-010. **Links:** API.

## Security requirements

### SEC-001 — Password/session protection
- **Priority:** MUST. **Rationale:** protect account access.
- **Preconditions:** auth operation.
- **Behavior:** maintained Argon2id password hash with recorded parameters; opaque high-entropy refresh tokens stored only as hashes, rotate on use; browser cookie HttpOnly/Secure/SameSite and CSRF/origin defense; short-lived access credential.
- **Acceptance:** raw password/token absent from DB/logs; expired/revoked/reused refresh is denied and audited.
- **Failure:** auth infrastructure outage fails closed.
- **Verification:** storage inspection, auth tests, token replay tests.
- **Story:** US-001/US-014. **Links:** auth, `sessions`.

### SEC-002 — Server-side authorization
- **Priority:** MUST. **Rationale:** UI hiding is not a security boundary.
- **Preconditions:** every protected read/write.
- **Behavior:** resolve actor and project role on server for each request; deny by default; check resource belongs to authorized project before fetching logs.
- **Acceptance:** API matrix tests deny cross-project GET and mutation for every route.
- **Failure:** DB membership unavailable fails closed (`503`), not allow.
- **Verification:** negative tests and route-policy review.
- **Story:** US-005. **Links:** all project APIs, AuthZ.

### SEC-003 — Tenant/project isolation
- **Priority:** MUST. **Rationale:** stop IDOR and ownership confusion.
- **Preconditions:** project data and namespace object operations.
- **Behavior:** project foreign keys/ownership; project-scoped queries; immutable namespace identity labels; reject namespace UID/label mismatch.
- **Acceptance:** cross-project identifiers cannot retrieve revision, job, log, event, or metric; orphan/foreign Kubernetes object is not adopted.
- **Failure:** ambiguous identity stops reconcile and raises operator event.
- **Verification:** DB/API/Kube integration tests.
- **Story:** US-017. **Links:** NFR-004, RBAC design.

### SEC-004 — Least-privilege Kubernetes access
- **Priority:** MUST. **Rationale:** limit worker/control-plane compromise.
- **Preconditions:** cluster deployment.
- **Behavior:** dedicated service account and namespace-scoped Roles where feasible; permissions enumerated and audited; user never receives cluster credential.
- **Acceptance:** role review and authorization can-i tests show only needed resource verbs; no cluster-admin binding.
- **Failure:** denied Kubernetes operation is visible, not worked around by broader privilege.
- **Verification:** rendered-role review and kind RBAC test.
- **Story:** US-004. **Links:** Kubernetes adapter, ADR-0002.

### SEC-005 — Secret protection and redaction
- **Priority:** MUST. **Rationale:** workload config and credentials can be sensitive.
- **Preconditions:** ingest, persist, log, display config or logs.
- **Behavior:** MVP accepts only non-secret env values and references to operator-provisioned secret keys; raw secrets are not persisted by MiniCloud; redact known credentials, tokens, authorization headers and secret-valued fields.
- **Acceptance:** test marker secret is absent from DB logs/errors/events; UI identifies secret refs without revealing values.
- **Failure:** redaction uncertain: deny or omit sensitive detail, never echo.
- **Verification:** seeded secret redaction tests.
- **Story:** US-010. **Links:** security, API, logs.

### SEC-006 — Constrained workload input
- **Priority:** MUST. **Rationale:** users must not obtain node/host privileges via deployment fields.
- **Preconditions:** deployment request.
- **Behavior:** allowlist supported fields; force non-privileged security context; reject privileged, hostPath, hostNetwork, hostPID, hostIPC, arbitrary service account and unrestricted capabilities.
- **Acceptance:** negative request corpus denied; generated object policy test confirms safe defaults.
- **Failure:** admission rejection surfaced as safe deployment failure.
- **Verification:** schema/property and kind admission tests.
- **Story:** US-004. **Links:** FR-005/007.

### SEC-007 — API abuse and request limits
- **Priority:** MUST. **Rationale:** reduce brute force and resource abuse.
- **Preconditions:** exposed API.
- **Behavior:** bounded request body, per-identity/IP login throttles and per-project mutation limits; CORS allowlist; parameterized DB queries; CSRF for cookie auth; security headers.
- **Acceptance:** configured limits return `429` with retry guidance; oversized body rejected; origin/CSRF tests fail closed.
- **Failure:** rate-limit backend failure falls back to conservative local policy or rejects auth-sensitive traffic; no unrestricted fallback.
- **Verification:** API security tests/load test.
- **Story:** US-001. **Links:** API middleware, OPS.

### SEC-008 — Auditable privileged operations
- **Priority:** MUST. **Rationale:** explain administrative changes.
- **Preconditions:** account/project/membership/deployment/quota mutation.
- **Behavior:** append actor, project, action, target, request/correlation ID, timestamp, result; redact payloads; users cannot update/delete audit rows.
- **Acceptance:** mutation commits with corresponding audit event in the same DB transaction.
- **Failure:** audit persistence failure aborts protected mutation.
- **Verification:** transaction tests and append-only permission check.
- **Story:** US-003/US-013. **Links:** `audit_events`, OPS.

### SEC-009 — Safe diagnostics
- **Priority:** MUST. **Rationale:** errors and logs are output channels.
- **Preconditions:** API/worker failure.
- **Behavior:** client sees stable code, safe message, correlation ID; internal detail stays access-controlled; no stack trace/secret in client response.
- **Acceptance:** forced exception tests assert safe envelope and protected internal logs.
- **Failure:** generic internal error without falsely reporting success.
- **Verification:** API error contract and log scanning tests.
- **Story:** US-006. **Links:** API error schema.

## Operational requirements

### OPS-001 — Health and dependency readiness
- **Priority:** MUST. **Rationale:** operators need truthful process/dependency state.
- **Preconditions:** service deployed.
- **Behavior:** separate liveness from readiness; readiness checks required DB and worker signal; optional Kubernetes/metrics capabilities exposed independently.
- **Acceptance:** fault injection changes readiness appropriately and never reports a missing optional metrics provider as deployment success.
- **Failure:** safe component code and correlation ID.
- **Verification:** dependency interruption tests.
- **Story:** US-018. **Links:** FR-020.

### OPS-002 — Structured telemetry
- **Priority:** MUST. **Rationale:** correlate request/job/resource evidence.
- **Preconditions:** request or worker event.
- **Behavior:** structured logs, correlation ID, operation/revision IDs, bounded-cardinality counters/histograms, redaction.
- **Acceptance:** one test operation can be traced across API/worker/logged Kube object identity without exposing credentials.
- **Failure:** telemetry sink outage does not convert deployment to ready or leak details.
- **Verification:** log/metric assertions and redaction.
- **Story:** US-006. **Links:** observability spec.

### OPS-003 — Recovery and runbooks
- **Priority:** MUST. **Rationale:** partial failure must be operable.
- **Preconditions:** worker/DB/Kube recovery.
- **Behavior:** document backup, restore, stale job recovery, reconciliation, migration recovery, and manual safe intervention.
- **Acceptance:** controlled recovery drill records duration, data loss/duplicates, and operator actions.
- **Failure:** unknown state stays visible until reconciled.
- **Verification:** runbook exercise with disposable environment.
- **Story:** US-019. **Links:** deployment and evaluation.

### OPS-004 — Automated quality gate
- **Priority:** MUST. **Rationale:** prevent unverified changes.
- **Preconditions:** code change pushed.
- **Behavior:** CI runs format/lint/type checks, unit/API tests, migration validation, frontend tests, build, dependency/secret scanning; kind integration may be separate job.
- **Acceptance:** required checks fail on injected defect; no claim of security solely from scanner.
- **Failure:** unavailable integration infrastructure marks job blocked/failing, not skipped-success.
- **Verification:** workflow test and run logs.
- **Story:** US-020. **Links:** CI pipeline.

### OPS-005 — Data retention, backup, deletion
- **Priority:** SHOULD. **Rationale:** data lifecycle must be explicit.
- **Preconditions:** operator deployment.
- **Behavior:** define retention for event/audit/log references; backup PostgreSQL with protected storage; project deletion has asynchronous resource cleanup and tombstone.
- **Acceptance:** restore exercise and deletion test account for DB rows and Kubernetes resources; legal retention remains open.
- **Failure:** failed cleanup remains visible and retryable.
- **Verification:** backup/restore and deletion reconciliation drill.
- **Story:** US-002. **Links:** DB and deployment docs.

## Capability coverage and acceptance index

This matrix provides each requested product capability a priority, implementation
expectation, measurable acceptance, and verification path. The requirement
sections above are the detailed source of truth.

| Capability | Priority | Implementation expectation | Acceptance / verification |
| --- | --- | --- | --- |
| Authentication/account management | MUST | FR-001, SEC-001 | Auth lifecycle and token tests |
| Projects/ownership | MUST | FR-002/003, SEC-002/003 | Ownership and cross-project matrix |
| Container image deployment | MUST | FR-004/005 | API + kind image success/failure |
| Deployment/Service | MUST | FR-007 | Inspect actual kind objects/selectors |
| Namespace isolation | MUST | FR-006, SEC-003/004 | Namespace labels, quota and RBAC checks |
| Lifecycle tracking | MUST | FR-008/009 | State-machine and DB recovery tests |
| Rollout/readiness | MUST | FR-008, NFR-002 | Delayed/failing probes in kind |
| Health/failure diagnostics | MUST | FR-012/020, OPS-001 | Fault injection and evidence attribution |
| Manual scaling | MUST | FR-010 | Bounded/concurrent scale tests |
| Automatic scaling | SHOULD | FR-013 + HPA adapter (future) | Real provider required; absent path explicit |
| History/rollback | MUST | FR-009/011 | Revision immutability/rollback integration |
| Logs/events/metrics | MUST logs/events; SHOULD metrics | FR-012/013 | Real Kube sources; unavailable tests |
| Alerts | SHOULD | FR-017 | Test delivery, dedupe, failure and recovery |
| Quotas/CPU-memory limits | MUST | FR-014, SEC-006 | Reject and cluster backstop tests |
| RBAC/audit | MUST | FR-003, SEC-002/008 | Role matrix, transactional audit check |
| REST/OpenAPI | MUST | FR-018 | Contract tests |
| Developer CLI | MUST | FR-015 | CLI E2E and exit behavior |
| Responsive dashboard | MUST | FR-016, NFR-007 | E2E, keyboard and accessibility review |
| Docker local deployment | MUST | NFR-006/009 | Clean setup and measured resource report |
| Automated tests/CI | MUST | OPS-004 | Required workflow checks executed |
| Operational docs | MUST | OPS-003/005 | Runbook restore/deletion exercises |
