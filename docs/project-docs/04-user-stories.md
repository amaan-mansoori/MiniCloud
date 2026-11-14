# 04 — User Stories and Use Cases

All stories are proposed user-visible behavior. No story is implemented merely
because it appears here. Requirement IDs resolve in [03-requirements.md](03-requirements.md);
API contracts resolve in [08-api-spec.md](08-api-spec.md); test IDs resolve in
[12-testing.md](12-testing.md).

## Story catalog

### US-001 — Create an account and sign in
- **Story:** As an individual developer, I want to register and authenticate so that my projects are private to my identity.
- **Persona:** Individual developer. **Preconditions:** registration enabled.
- **Main flow:** submit email/password → receive account/session → use protected project endpoint.
- **Alternatives:** sign in with existing credentials; log out and invalidate session.
- **Failures:** duplicate identity, invalid fields, incorrect credentials, throttling, expired session.
- **Given/When/Then:** Given registration is enabled, when valid unique credentials are submitted, then an account is created and no raw password is stored.
- **Authorization:** public registration subject to policy; account operations require self.
- **Requirements:** FR-001, SEC-001, SEC-007. **API:** POST `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/refresh`. **Tests:** T-AUTH-01..06.

### US-002 — Create and find a project
- **Story:** As an individual developer, I want a project workspace so that deployments, ownership, and quotas have a clear scope.
- **Persona:** Developer / owner. **Preconditions:** signed in.
- **Main flow:** create project → become owner → list/get own project.
- **Alternatives:** create with a non-conflicting slug; project namespace provisioning may be pending.
- **Failures:** duplicate slug, namespace conflict, DB/Kubernetes unavailable, cross-project lookup.
- **Given/When/Then:** Given a signed-in user, when they create a valid project, then they become its owner and the project is visible only to authorized members.
- **Authorization:** authenticated create; owner-only update/delete.
- **Requirements:** FR-002, FR-006, SEC-002/003. **API:** POST/GET `/projects`. **Tests:** T-PROJ-01..04.

### US-003 — Manage project membership
- **Story:** As a project owner, I want to add or revoke a member so that a teammate can operate within a bounded project role.
- **Persona:** Owner. **Preconditions:** target account exists.
- **Main flow:** owner adds member → member can deploy/read → owner revokes → next request is denied.
- **Alternatives:** change role; another owner remains.
- **Failures:** member attempts elevation, last-owner removal, unknown user, stale membership.
- **Given/When/Then:** Given owner permission, when an existing user is added as member, then member permissions apply; a non-owner's attempt is denied.
- **Authorization:** owner only.
- **Requirements:** FR-003, SEC-002/008. **API:** `/projects/{id}/members`. **Tests:** T-AUTHZ-01..05.

### US-004 — Deploy a container image
- **Story:** As a developer, I want to submit a supported image and configuration so that MiniCloud can create a workload without me authoring raw Kubernetes resources.
- **Persona:** Member. **Preconditions:** project provisioned, image reachable by cluster.
- **Main flow:** submit image/config/idempotency key → receive operation ID → worker applies revision → user follows status.
- **Alternatives:** local image loaded into kind; valid request whose image cannot be pulled becomes a later failure.
- **Failures:** malformed reference, forbidden workload option, quota violation, Kube unavailable, image pull failure.
- **Given/When/Then:** Given an authorized member and valid supported input, when deploy is submitted, then a durable operation is accepted and response says pending rather than ready.
- **Authorization:** project member; policy may reserve configuration changes to owner.
- **Requirements:** FR-004..008, FR-019, SEC-003..006. **API:** POST `/projects/{id}/deployments`. **Tests:** T-DEPLOY-01..08.

### US-005 — Track deployment progress
- **Story:** As a developer, I want to inspect a deployment operation so that I know whether it is queued, progressing, ready, or failed.
- **Persona:** Member. **Preconditions:** authorized project and operation ID.
- **Main flow:** retrieve operation → inspect state, revision, observed generation and timestamps → poll with backoff.
- **Alternatives:** observe pending Kube API retry; use last known observation marked stale.
- **Failures:** wrong project, unknown operation, observer outage.
- **Given/When/Then:** Given a new accepted request, when I retrieve status before rollout completion, then it is not labeled ready.
- **Authorization:** project member.
- **Requirements:** FR-008/009, NFR-002, SEC-002/003. **API:** GET `/operations/{id}` and deployment details. **Tests:** T-STATE-01..06.

### US-006 — Diagnose a failed rollout
- **Story:** As a developer, I want the failure reason and related events so that I can correct a broken image or readiness configuration.
- **Persona:** Member. **Preconditions:** operation failed or is progressing abnormally.
- **Main flow:** open revision timeline → inspect sanitized condition/reason → inspect events and, if available, logs.
- **Alternatives:** logs not yet available; show source-unavailable reason and retry guidance.
- **Failures:** stale observation, forbidden project, secret-containing diagnostic.
- **Given/When/Then:** Given a failed rollout, when I open its details, then the status is failed and relevant observed evidence is shown without secrets or stack traces.
- **Authorization:** project member; logs subject to same scope.
- **Requirements:** FR-008/012, SEC-005/009, OPS-002. **API:** status/events/logs. **Tests:** T-DIAG-01..05.

### US-007 — View deployment history
- **Story:** As a project member, I want a chronological revision history so that I can compare changes and choose a rollback target.
- **Persona:** Member. **Preconditions:** at least one revision or accessible project.
- **Main flow:** request cursor page → inspect actor, time, image/config summary, operation outcome → continue using cursor.
- **Alternatives:** empty history; next page during concurrent writes.
- **Failures:** invalid cursor, missing project access.
- **Given/When/Then:** Given recorded revisions, when I request history, then results are newest-first, bounded, and associated with the correct project.
- **Authorization:** project member.
- **Requirements:** FR-009, NFR-010, SEC-002/003. **API:** GET `/deployments/{id}/revisions`. **Tests:** T-HISTORY-01..04.

### US-008 — Scale replicas manually
- **Story:** As a developer, I want to set an allowed replica count so that I can change capacity within project limits.
- **Persona:** Member. **Preconditions:** existing deployment, quota permits.
- **Main flow:** read current generation → submit count + ETag + idempotency key → monitor operation.
- **Alternatives:** current value already matches; user refreshes after conflict.
- **Failures:** over maximum, quota, stale ETag, concurrent scale, Kube outage.
- **Given/When/Then:** Given an active deployment and current ETag, when I request an allowed count, then the desired revision is accepted and observed replicas are reported separately.
- **Authorization:** project member.
- **Requirements:** FR-010, FR-014, NFR-005, SEC-002. **API:** POST `/deployments/{id}/scale`. **Tests:** T-SCALE-01..05.

### US-009 — Roll back a revision
- **Story:** As a project owner, I want to reapply a previously recorded configuration so that I can recover without erasing deployment history.
- **Persona:** Owner. **Preconditions:** eligible retained target revision.
- **Main flow:** select revision → confirm target → submit idempotent request → track new rollback revision.
- **Alternatives:** target same as current; rollback queued behind an active operation.
- **Failures:** deleted target, stale generation, quota/image unavailable, Kube failure.
- **Given/When/Then:** Given an eligible earlier revision, when I request rollback, then a new revision records its source and must pass readiness before being called ready.
- **Authorization:** owner for MVP (proposed).
- **Requirements:** FR-011, FR-019, SEC-002/008. **API:** POST `/deployments/{id}/rollback`. **Tests:** T-ROLLBACK-01..05.

### US-010 — Inspect logs and events
- **Story:** As a developer, I want recent authorized container logs and Kubernetes events so that I can diagnose a workload.
- **Persona:** Member. **Preconditions:** project access, workload identifier.
- **Main flow:** select revision/pod and time window → fetch bounded source output → inspect timestamp/truncation/source.
- **Alternatives:** container restarted; Pod no longer exists; output truncates.
- **Failures:** unavailable Kube API, unauthorized project, logs absent, redaction.
- **Given/When/Then:** Given an authorized workload, when its logs are available, then only bounded real logs are returned; absent source is an explicit unavailable response.
- **Authorization:** project member, server checked.
- **Requirements:** FR-012, NFR-010, SEC-002/005/009. **API:** GET logs/events. **Tests:** T-OBS-01..05.

### US-011 — View resource usage
- **Story:** As a project owner, I want actual CPU/memory samples so that I can assess resource use when a metrics source exists.
- **Persona:** Owner. **Preconditions:** metrics provider configured and sample fresh.
- **Main flow:** open metrics → view values, units, sample time and source.
- **Alternatives:** metrics provider missing/stale → clear unavailable state and setup guidance.
- **Failures:** source timeout or unauthorized workload.
- **Given/When/Then:** Given no metrics provider, when I open metrics, then the product says unavailable and displays no fabricated value.
- **Authorization:** project member; aggregate visibility follows project role.
- **Requirements:** FR-013, SEC-002, OPS-001. **API:** GET `/metrics`. **Tests:** T-METRICS-01..04.

### US-012 — Handle unavailable autoscaling metrics
- **Story:** As a developer, I want autoscaling capability discovery to report when metrics are missing so that I do not mistake configured intent for active scaling.
- **Persona:** Owner. **Preconditions:** user views capability or requests HPA (deferred).
- **Main flow:** inspect capability → see provider status → manual scaling remains available.
- **Alternatives:** provider becomes available later; stale source is shown.
- **Failures:** metrics API unreachable, permission failure.
- **Given/When/Then:** Given a missing metrics provider, when autoscaling capability is queried, then it is unavailable and no HPA is claimed active.
- **Authorization:** authenticated; project-specific detail requires member.
- **Requirements:** FR-013/020. **API:** `/capabilities`, `/metrics`. **Tests:** T-METRICS-01..04.

### US-013 — Configure project resource quota
- **Story:** As a project owner, I want bounded CPU, memory, and replica limits so that a project cannot consume unlimited local resources.
- **Persona:** Owner. **Preconditions:** policy values within operator maximum.
- **Main flow:** submit policy → validate → persist + reconcile Namespace quota/LimitRange → display applied/observed state.
- **Alternatives:** desired/applied policy differ while reconciliation pending.
- **Failures:** invalid values, Kube permission, quota object drift.
- **Given/When/Then:** Given a policy within platform maximum, when owner saves it, then subsequent deployment requests exceeding it are rejected and cluster quota is updated.
- **Authorization:** project owner.
- **Requirements:** FR-014, SEC-002/006, NFR-004. **API:** GET/PATCH `/projects/{id}/quota`. **Tests:** T-QUOTA-01..04.

### US-014 — Operate through the CLI
- **Story:** As a developer, I want CLI commands for the same lifecycle so that I can script development tasks.
- **Persona:** Developer. **Preconditions:** installed CLI, API reachable, credential stored securely.
- **Main flow:** login → choose project → deploy → wait/status → logs/scale/history/rollback.
- **Alternatives:** JSON output or polling interval; API unavailable.
- **Failures:** expired auth prompts re-login; terminal rollout failure returns nonzero; duplicate retry reuses key.
- **Given/When/Then:** Given an authenticated CLI, when deploy returns 202, then CLI shows operation ID and pending state and does not exit success as though ready.
- **Authorization:** same API project policy.
- **Requirements:** FR-015/018/019, SEC-001/009. **API:** all corresponding endpoints. **Tests:** T-CLI-01..06.

### US-015 — Use the dashboard accessibly
- **Story:** As a developer, I want a responsive dashboard with keyboard-operable lifecycle controls so that I can work without relying on pointer or color alone.
- **Persona:** all users. **Preconditions:** browser session.
- **Main flow:** navigate project/deployment → inspect state → operate supported action → see confirmation/result.
- **Alternatives:** empty/loading/error/forbidden/unavailable state.
- **Failures:** expired auth redirects/re-authenticates without losing safe form state; API errors show correlation ID.
- **Given/When/Then:** Given a keyboard-only user, when they complete deploy/status/rollback navigation, then all controls have visible focus and textual state labels.
- **Authorization:** API remains authoritative.
- **Requirements:** FR-016, NFR-007, SEC-009. **API:** project/deployment APIs. **Tests:** T-UI-01..06.

### US-016 — Receive an actionable alert
- **Story:** As a project owner, I want an alert when a configured real condition persists so that I can investigate without polling continuously.
- **Persona:** Owner/operator. **Preconditions:** supported evaluator and delivery channel configured.
- **Main flow:** define condition/window → evaluator observes data → deduplicated alert event → delivery result visible → recovery closes/resolves incident.
- **Alternatives:** unavailable metric disables rule; delivery retries boundedly.
- **Failures:** duplicate sample, provider outage, channel error.
- **Given/When/Then:** Given an active tested rule and real matching condition through its evaluation window, when evaluator runs, then one deduplicated alert is recorded and its delivery outcome is visible.
- **Authorization:** owner configures project rule; operator configures channel.
- **Requirements:** FR-017, SEC-008, OPS-002. **API:** alerts (SHOULD/future). **Tests:** T-ALERT-01..04.

### US-017 — Prevent cross-project access
- **Story:** As a project owner, I want project resources isolated from other members so that deployments, logs, and configuration are not disclosed or mutated by outsiders.
- **Persona:** Owner. **Preconditions:** two projects and actors.
- **Main flow:** project A actor requests project B object → server denies without revealing sensitive details → audit security event as appropriate.
- **Alternatives:** membership added then access permitted; membership revoked then denied.
- **Failures:** stale cache or missing membership DB → fail closed.
- **Given/When/Then:** Given no membership in project B, when actor requests a deployment/log in B, then no data is returned and no state changes.
- **Authorization:** deny by default; server-side every operation.
- **Requirements:** SEC-002/003, NFR-005. **API:** all project-scoped endpoints. **Tests:** T-AUTHZ-01..08.

### US-018 — Handle expired authentication
- **Story:** As a developer, I want an expired or revoked session to be rejected clearly so that stale credentials cannot continue privileged access.
- **Persona:** any signed-in user. **Preconditions:** session expiry/revocation.
- **Main flow:** request gets 401 → UI/CLI clears invalid session → user signs in → retry safe read or idempotent mutation.
- **Alternatives:** refresh rotates valid token.
- **Failures:** refresh reuse or auth service unavailable; do not replay mutation with a new key.
- **Given/When/Then:** Given an expired token, when a protected request arrives, then it is rejected with `AUTH_EXPIRED`; no project operation runs.
- **Authorization:** valid session required.
- **Requirements:** FR-001, SEC-001/007. **API:** auth refresh + protected APIs. **Tests:** T-AUTH-04..06.

### US-019 — Operate through Kubernetes outage and worker recovery
- **Story:** As a platform operator, I want accepted operations to remain recoverable during API/worker/Kubernetes interruption so that partial failure is visible and safely reconciled.
- **Persona:** Operator. **Preconditions:** persisted job.
- **Main flow:** dependency interruption → health/status show unavailable or stale → worker lease expires → retry reconciles deterministic resources → operation completes/fails truthfully.
- **Alternatives:** retry limit reached → terminal failure/runbook action.
- **Failures:** PostgreSQL unavailable before commit returns no acceptance; after commit Kube failure retains work.
- **Given/When/Then:** Given an accepted job and worker crash, when worker restarts and lease expires, then it resumes without duplicate workload or false readiness.
- **Authorization:** operator recovery actions restricted.
- **Requirements:** NFR-002..005, OPS-001/003. **API:** health, operations. **Tests:** T-RECOVERY-01..06.

### US-020 — Reproduce setup and verify changes
- **Story:** As a contributor, I want documented local setup and automated checks so that another developer can repeat the educational workflow.
- **Persona:** developer/contributor. **Preconditions:** supported local host.
- **Main flow:** follow setup → run migrations → run tests → start services → deploy to isolated kind context.
- **Alternatives:** metrics/alert services omitted; capability shown unavailable.
- **Failures:** missing Docker/context/registry stops with actionable blocker, not fake success.
- **Given/When/Then:** Given clean documented prerequisites, when contributor runs setup and tests, then commands either pass with output evidence or fail explicitly at the unavailable dependency.
- **Authorization:** operator controls cluster context; no implicit context switch.
- **Requirements:** NFR-006/008/009, OPS-004. **API:** health/capabilities. **Tests:** T-SETUP-01..04.

## Detailed use cases

### UC-001 — Registration through first deployment
**Actors:** Individual developer, API, PostgreSQL, worker, Kubernetes.
**Preconditions:** registration enabled; API/DB healthy; project cluster configured;
image registry reachable. **Trigger:** developer starts onboarding.

1. Developer registers and authenticates.
2. API validates and hashes credentials, creates account and session.
3. Developer creates project; transaction creates owner membership and project.
4. Namespace provisioning job creates namespace/quota with deterministic project
   identity; developer polls project status until it is active.
5. Developer submits image, port, probes, resources,
   replicas, and idempotency key.
6. API authenticates, checks membership/policy, validates request, and commits
   revision/job/audit together; returns `202` and operation URL.
7. Worker leases job, applies Deployment and Service, records observations.
8. Client polls operation until `ready` or `failed`.

**Alternatives:** if deployment is attempted while namespace provisioning, API
returns retryable `409 PROJECT_NOT_READY`; it does not accept a deployment job
that cannot yet run. It must not apply into a missing/foreign namespace.
**Failure cases:** duplicate request returns same
operation; invalid image syntax returns 422; unpullable valid image fails
asynchronously. **Postconditions:** revision and evidence persist; readiness
reflects cluster observation. **Authorization:** account self; project member.
**Acceptance:** tests T-AUTH-01, T-PROJ-01, T-DEPLOY-01/02, T-STATE-01.
**Requirements:** FR-001/002/004/006/008/019, SEC-001/002/003.

### UC-002 — Failed rollout diagnosis
**Actors:** Member, UI/CLI, API, Kubernetes.
**Preconditions:** revision progressing or failed. **Trigger:** user opens
deployment detail.

1. API checks member access before querying any Kubernetes resource.
2. UI shows desired image/config separately from observed generation and
   readiness.
3. User selects events; API returns bounded project-scoped event records.
4. User opens logs; API rechecks permission, selects known container/Pod,
   bounds time/bytes, redacts sensitive values, and reports source freshness.
5. If source is unavailable, UI shows the exact unavailable category and
   correlation ID rather than an empty healthy view.
6. User may submit a corrected deployment as a new revision.

**Alternatives:** Pod has terminated; event retention elapsed; metrics are
missing. **Failure cases:** Kube API timeout, revoked membership, secret
redaction uncertainty. **Postconditions:** no state mutation from diagnostics.
**Acceptance:** T-DIAG-01..05 and T-AUTHZ-01..08. **Requirements:** FR-008/012,
SEC-002/005/009, NFR-010.

### UC-003 — Scale with concurrency
**Actors:** two project members, API, worker, Kubernetes.
**Preconditions:** deployment exists; both read generation `g`; quota permits at
least one request. **Trigger:** both submit scale concurrently.

1. Each request includes expected generation/ETag and unique idempotency key.
2. API validates replica bounds and current project quota.
3. First transaction reserves next desired revision and active job using
   optimistic compare-and-swap.
4. Second request receives `409` with current generation; it does not enqueue a
   competing write.
5. Worker applies the winning desired replica count and observes actual replicas.

**Alternatives:** exact same request/key returns original operation. **Failure:**
Kubernetes outage retains queued work; quota drift blocks. **Postconditions:**
one winning revision, auditable loser conflict. **Acceptance:** T-SCALE-03 and
T-RETRY-02. **Requirements:** FR-010/019, NFR-005, SEC-002.

### UC-004 — Roll back a revision
**Actors:** owner, API, worker, Kubernetes.
**Preconditions:** retained eligible revision `r_old`; authorized project.
**Trigger:** owner selects rollback.

1. API validates target belongs to deployment/project and confirms expected
   current generation.
2. One DB transaction records an immutable new revision with source pointer
   `rollback_of=r_old`, a job, and audit event.
3. API returns 202; history remains unchanged.
4. Worker applies the copied desired configuration under the new revision ID.
5. Readiness observer waits for the new generation and ready replicas.

**Alternatives:** active operation causes explicit conflict/queue policy.
**Failure:** old image no longer available; revision remains failed with reason.
**Postconditions:** rollback attempt is first-class history, not destructive
history rewrite. **Acceptance:** T-ROLLBACK-01..05. **Requirements:** FR-011,
FR-019, SEC-002/008.

### UC-005 — Metrics and autoscaling are unavailable
**Actors:** project member, API, optional metrics adapter.
**Preconditions:** cluster lacks a functioning provider. **Trigger:** user opens
metrics or autoscaling capability.

1. API probes configured provider capability or uses health from its adapter.
2. Response states `metrics_unavailable`, has no sample values, and explains
   whether source is missing, stale, or unreachable.
3. UI leaves manual scaling available if deployment/API is healthy.
4. No HPA status or utilization is fabricated.

**Failure:** capability probe itself times out and is reported unknown.
**Postconditions:** deployment readiness unaffected. **Acceptance:** T-METRICS-01..04.
**Requirements:** FR-013/020, OPS-001.

### UC-006 — Kubernetes infrastructure becomes unavailable
**Actors:** operator, API, worker, PostgreSQL, Kubernetes API.
**Preconditions:** at least one durable job. **Trigger:** Kube API/network outage.

1. API commits no success for requests whose DB commit failed.
2. For committed work, worker retry deadline/backoff applies; last observation
   becomes stale and dependency health degrades.
3. API still returns durable operation ID and `progressing`/retryable state
   rather than ready.
4. After Kube connectivity returns, worker retries against deterministic
   namespace/object identity and desired generation.
5. Reconciler verifies actual observed readiness or reaches terminal failure.

**Alternatives:** retry budget exhausted—operator sees failed/exhausted status.
**Failure:** foreign object identity mismatch stops reconciliation for review.
**Acceptance:** T-RECOVERY-01..04. **Requirements:** NFR-002..004, OPS-001/003.

### UC-007 — Expired credentials and safe retry
**Actors:** CLI/UI user, auth middleware, API.
**Preconditions:** access token expired. **Trigger:** protected operation.

1. API rejects token with generic structured 401 and `AUTH_EXPIRED`.
2. Client refreshes only if refresh token remains valid and rotates it, else
   prompts login.
3. Safe reads may be retried after refresh.
4. Mutations use same idempotency key; reused key with changed body gets 409.

**Failure:** revoked/replayed refresh fails closed; auth outage does not permit
request. **Acceptance:** T-AUTH-04..06, T-RETRY-01. **Requirements:** FR-001/019,
SEC-001/007.

### UC-008 — Deny cross-project log access
**Actors:** authenticated actor from project A, API, Kubernetes adapter.
**Preconditions:** deployment exists only in project B, actor has no B role.
**Trigger:** actor guesses deployment/revision/Pod ID and requests logs.

1. API resolves resource ownership and membership before contacting Kubernetes.
2. API returns stable `404` (or policy-approved `403`) without revealing
   existence, namespace, image, or log contents.
3. No Kubernetes request is issued for unauthorized resource.
4. Security/audit telemetry records a safe denial signal without credentials.

**Failure:** membership DB unavailable means fail closed. **Acceptance:**
T-AUTHZ-01..08. **Requirements:** SEC-002/003/009.
