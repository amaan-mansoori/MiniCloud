# 08 — REST API Specification

**Status:** Target contract under `/api/v1`; implementation is partial and
OpenAPI is generated from current source. Check the implementation notes below
before relying on a proposed field. All timestamps are UTC RFC 3339. IDs are
opaque UUIDs.

## Common conventions

- Auth: short-lived bearer access token; browser refresh uses secure HttpOnly
  cookie and CSRF/origin defense. CLI stores credential in OS keychain where
  available. Exact flow awaits implementation/security review.
- Every project operation performs server-side membership and role checks
  before data or Kubernetes access.
- Desired-state and long-running mutations (deployment, scale, rollback,
  quota reconciliation, and project deletion) require `Idempotency-Key`
  (opaque random client value); updates require `If-Match: "<generation>"`.
  Same key/body returns the original
  operation; same key/different body returns 409.
- Errors use `{ "error": { "code": "STABLE_CODE", "message": "Safe text",
  "details": [], "correlation_id": "..." } }`. No stack traces, credentials, or
  unauthorized resource data.
- List pagination: `limit` default 50, max 100 (provisional); opaque `cursor`;
  `{items,next_cursor}`. Logs have independent time/byte limits.
- `202 Accepted` means request passed synchronous checks and committed durably;
  it does **not** mean resources are applied or ready. `GET /operations/{id}`
  reports asynchronous execution.
- General codes: `AUTH_REQUIRED`, `AUTH_EXPIRED`, `FORBIDDEN`,
  `NOT_FOUND`, `VALIDATION_ERROR`, `CONFLICT`, `QUOTA_EXCEEDED`,
  `RATE_LIMITED`, `DEPENDENCY_UNAVAILABLE`, `OPERATION_FAILED`,
  `INTERNAL_ERROR`.

## Schemas

`DeploymentSpec`:

```json
{
  "name": "web",
  "image": "registry.example/web:1.2.3",
  "port": 8080,
  "replicas": 2,
  "resources": {
    "cpu_request": "100m",
    "cpu_limit": "500m",
    "memory_request": "128Mi",
    "memory_limit": "512Mi"
  },
  "readiness_probe": { "path": "/ready", "initial_delay_seconds": 5 },
  "environment": [{ "name": "APP_MODE", "value": "prod" }],
  "secret_references": [{ "name": "DB_PASSWORD", "secret": "db", "key": "password" }]
}
```

`Operation`:

```json
{
  "id": "op_uuid",
  "revision_id": "rev_uuid",
  "state": "progressing",
  "accepted_at": "2026-10-11T18:00:00Z",
  "observed_at": "2026-10-11T18:00:08Z",
  "observation_stale": false,
  "desired_generation": 3,
  "observed_generation": 3,
  "ready_replicas": 1,
  "failure": null,
  "links": { "self": "/api/v1/operations/op_uuid" }
}
```

Allowed state transitions are defined once in [architecture](06-architecture.md):
`accepted → queued → applying → progressing → ready`; deadline expiration
produces recoverable `timed_out`, which can later reach `ready` or an observed
terminal `failed` state.

## Authentication endpoints

| Method/path | Auth / permission | Request & response | Status, validation, errors | Idempotency/rate | Requirements |
| --- | --- | --- | --- | --- | --- |
| `POST /auth/register` | None if open enrollment; otherwise invite policy | `{email,password}` → `{user,session}` (credential cookie/token not echoed in JSON if cookie flow) | 201; 400 invalid; 409 duplicate; 429 throttled | No key; strict login/register throttle | FR-001, SEC-001/007 |
| `POST /auth/login` | None | `{email,password}` → `{user,access_token,expires_in}`; refresh via secure cookie | 200; generic 401; 429 | Rate-limit by IP + normalized account key | FR-001, SEC-001 |
| `POST /auth/refresh` | Refresh cookie / CLI credential | CSRF token/origin as applicable → rotated access; old refresh invalid | 200; 401 expired/replayed | Single-use rotation; strict throttle | FR-001, SEC-001 |
| `POST /auth/logout` | Authenticated session | Empty → `{logged_out:true}` | 204; 401 if required by client policy | Repeated logout safe | FR-001 |

Example login:

```http
POST /api/v1/auth/login
Content-Type: application/json
```

```json
{"email":"dev@example.test","password":"correct horse battery"}
```

```json
{"user":{"id":"user_uuid","email":"dev@example.test"},"access_token":"<redacted>","expires_in":900}
```

## Projects and membership

| Method/path | Permission | Request / response | Status / validation | Requirement |
| --- | --- | --- | --- | --- |
| `POST /projects` | Authenticated | `{name,slug}` → project incl. `status=provisioning` | 201; 409 slug; 422 fields; provisioning may be asynchronous | FR-002/006 |
| `GET /projects` | Authenticated | `limit,cursor` → paged memberships/projects | 200 | FR-002 |
| `GET /projects/{project_id}` | Member | Project, role, quota summary | 200/404 | FR-002/SEC-002 |
| `PATCH /projects/{project_id}` | Owner | `{name}` + `If-Match` → updated project | 200/409 stale/403 | FR-002 |
| `DELETE /projects/{project_id}` | Owner | No body → `{operation_id,status:"deleting"}` | 202; cleanup is async; 409 if active policy blocks | FR-002/OPS-005 |
| `GET /projects/{project_id}/members` | Owner/member (member view limited) | Page → member IDs/roles | 200 | FR-003 |
| `POST /projects/{project_id}/members` | Owner | `{user_id,role:"member"}` → membership | 201; 404 user; 409 duplicate | FR-003 |
| `PATCH /projects/{project_id}/members/{user_id}` | Owner | `{role}` → membership | 200; 409 last-owner/stale | FR-003 |
| `DELETE /projects/{project_id}/members/{user_id}` | Owner | Empty → 204 | 204; 409 last owner | FR-003 |
| `GET /projects/{project_id}/quota` | Member | → desired policy, version, applied version/state | 200 | FR-014 |
| `PATCH /projects/{project_id}/quota` | Owner | bounded CPU/memory/replica limits + `If-Match` + `Idempotency-Key` | 200 desired policy committed; 409 stale/idempotency conflict; 422 invalid/inconsistent limits; cluster applied status remains distinct | FR-014 |

Project create example:

```json
{"name":"Demo","slug":"demo"}
```

```json
{"id":"project_uuid","name":"Demo","slug":"demo","role":"owner","status":"provisioning"}
```

Unknown IDs return 404 without confirming another project's existence. Project
delete and namespace cleanup remain visible until Kubernetes confirms matching
objects removed.

## Deployments and operations

| Method/path | Permission | Request / response | Status / validation | Requirement |
| --- | --- | --- | --- | --- |
| `POST /projects/{project_id}/deployments` | Member | Current: bounded name/image/port/replicas plus optional `idempotency_key` → deployment ID, revision ID, state | 202; 422 invalid; 409 idempotency mismatch; 503 DB not committed | FR-004/005/019 |
| `GET /projects/{project_id}/deployments` | Member | `limit,cursor,state` → page | 200 | FR-009 |
| `GET /projects/{project_id}/deployments/{deployment_id}` | Member | Desired spec + observed summary + ETag | 200; 404 | FR-008 |
| `PATCH /projects/{project_id}/deployments/{deployment_id}` | Owner/member by policy | Supported mutable spec fields + key + ETag → operation | 202; 409 stale/in-flight; 422 invalid | FR-004/010/019 |
| `GET /operations/{operation_id}` | Member of owning project | Operation with state/freshness/links | 200; 404 | FR-008 |
| `PATCH /projects/{project_id}/deployments/{deployment_id}/scale` | Member | `{replicas,expected_replicas,idempotency_key}` → operation and new revision | 202; 409 stale/in-flight; 422 bounds | FR-010/019 |
| `POST /projects/{project_id}/deployments/{deployment_id}/rollback` | Member | `{revision_id,expected_revision_id,idempotency_key}` → operation and new revision | 202; 404 target; 409 stale/ineligible | FR-011/019 |
| `GET /projects/{project_id}/deployments/{deployment_id}/revisions` | Member | cursor page of immutable desired snapshots, newest first | 200; invalid cursor 400 | FR-009/010/011 |
| `GET /projects/{project_id}/deployments/{deployment_id}/events` | Member | `since,cursor,limit` → redacted event page | 200; source availability explicit | FR-009/012 |

Deployment submit example response:

```json
{
  "deployment_id": "dep_uuid",
  "revision_id": "rev_uuid",
  "operation_id": "op_uuid",
  "state": "accepted",
  "status_url": "/api/v1/operations/op_uuid"
}
```

Invalid image syntax returns `422 VALIDATION_ERROR` with field detail; syntactic
but unavailable image can return 202 and later fail `IMAGE_PULL_FAILED`.
Kubernetes unavailable after persistence returns 202, then operation remains
queued/progressing with stale observations and retries. Database unavailable
before commit returns 503 and no accepted operation. Quota violation returns
409 `QUOTA_EXCEEDED`. Failed rollout operation is retrieved as 200 with
`state:"failed"` and safe reason; it is not HTTP 500 on every poll.

Rollback example:

```json
{
  "revision_id":"rev_previous",
  "expected_revision_id":"rev_current",
  "idempotency_key":"rollback-request-unique"
}
```

```json
{
  "deployment_id":"dep_uuid",
  "revision_id":"rev_new",
  "job_state":"queued",
  "status":"accepted",
  "last_error":null
}
```

Each deployment create and scale request stores a new immutable desired-state
snapshot; rollback copies a retained snapshot into a new revision and records
`rollback_of_revision_id`. The revision history and rollback request are
project-authorized. The rollback endpoint requires the latest revision ID as
an optimistic concurrency precondition. Database/API/Worker tests do not prove
Kubernetes rollout readiness; that verification remains approval-gated.

## Logs, metrics, health, and capabilities

| Method/path | Auth / permission | Request / response | Status / failure behavior | Requirement |
| --- | --- | --- | --- | --- |
| `GET /projects/{id}/deployments/{dep}/logs` | Member | `container,since_seconds` ≤ 86400, `tail_lines` 1–1000 → up to 10 Pod log records, each capped at 64 KiB, with source time/truncation | 200; invalid parameters/container `422`; member-hidden deployment `404`; unconfigured/forbidden/unavailable Kubernetes source `503` | FR-012 |
| `GET /projects/{id}/deployments/{dep}/kubernetes-events` | Member | Actual Kubernetes Events for Pods selected by project/deployment labels; max 100 records | 200; member-hidden deployment `404`; unconfigured/forbidden/unavailable Kubernetes source `503` | FR-012 |
| `GET /projects/{id}/deployments/{dep}/events` | Member | `limit` 1–100 and optional `(created_at,id)` cursor → persisted lifecycle event items | 200; invalid cursor `400`; non-member `403`; missing deployment `404` | FR-009, FR-012 (partial) |
| `GET /projects/{id}/deployments/{dep}/metrics` | Member | time range capped → timestamped CPU/memory samples + source/freshness | 200; 503 `METRICS_UNAVAILABLE`; empty only when source confirms no sample | FR-013 |
| `GET /health/live` | None/internal network policy | `{status:"ok"}` | 200 if process can answer | FR-020 |
| `GET /health/ready` | None/internal policy | required DB/worker readiness components | 200/503; optional Kube provider separate | OPS-001 |
| `GET /api/v1/capabilities` | Authenticated | supported features/provider availability | 200; no secrets/cluster credentials | FR-020 |

`GET /api/v1/capabilities` reports optional capabilities explicitly. Until a
real provider and delivery path are configured, metrics and alerts return
`available: false` with an explanatory reason; no synthetic availability is
reported.

The `/events` endpoint returns persisted MiniCloud lifecycle events. The
separate `/kubernetes-events` endpoint reads actual Kubernetes Events and
filters them to Pod UIDs selected by the authorized deployment labels.
Diagnostics are served by a separately configured read-only API identity and
return an explicit unavailable error when it is not configured. Pod logs have
a 64 KiB-per-Pod cap, bounded tail/time parameters, common credential-pattern
redaction, and truncation metadata. Arbitrary Kubernetes objects/manifests and
Secrets are never returned.

## Alerts (SHOULD; not MVP commitment)

Proposed future `GET/POST/PATCH/DELETE /projects/{id}/alerts` and
`GET /projects/{id}/alerts/events` require project-owner permission, bounded
rule fields, real source, evaluation window, dedupe key, and tested delivery.
Until evaluator and delivery are implemented, capabilities state alerts
unavailable and no UI control implies otherwise. Requirements: FR-017.

## Endpoint example pairs

The contracts above define permissions, status/error behavior, and shared
validation/rate/pagination rules. These abbreviated pairs show a request and
response shape for **each proposed route**. Tokens and secrets are placeholders
and must never be copied into logs. `{id}` values are illustrative UUIDs.

### Authentication examples

| Endpoint | Example request | Example response |
| --- | --- | --- |
| `POST /api/v1/auth/register` | `{"email":"dev@example.test","password":"<input-secret>"}` | `201 {"user":{"id":"u1","email":"dev@example.test"}}` + secure session cookie |
| `POST /api/v1/auth/login` | `{"email":"dev@example.test","password":"<input-secret>"}` | `200 {"user":{"id":"u1","email":"dev@example.test"},"access_token":"<redacted>","expires_in":900}` + refresh cookie |
| `POST /api/v1/auth/refresh` | `Cookie: refresh=<redacted>` + CSRF header if cookie flow | `200 {"access_token":"<redacted>","expires_in":900}` + rotated cookie |
| `POST /api/v1/auth/logout` | `Authorization: Bearer <redacted>` | `204 No Content` |

### Project and membership examples

| Endpoint | Example request | Example response |
| --- | --- | --- |
| `POST /api/v1/projects` | `{"name":"Demo","slug":"demo"}` | `201 {"id":"p1","name":"Demo","slug":"demo","role":"owner","status":"provisioning"}` |
| `GET /api/v1/projects?limit=50` | Bearer auth; query `limit=50` | `200 {"items":[{"id":"p1","slug":"demo","role":"owner","status":"active"}],"next_cursor":null}` |
| `GET /api/v1/projects/p1` | Bearer auth | `200 {"id":"p1","name":"Demo","role":"owner","status":"active","version":1}` |
| `PATCH /api/v1/projects/p1` | `If-Match: "1"`; `{"name":"Demo service"}` | `200 {"id":"p1","name":"Demo service","version":2}` |
| `DELETE /api/v1/projects/p1` | `Idempotency-Key: key-1` | `202 {"operation_id":"op1","status":"deleting"}` |
| `GET /api/v1/projects/p1/members?limit=50` | Bearer auth | `200 {"items":[{"user_id":"u1","role":"owner"}],"next_cursor":null}` |
| `POST /api/v1/projects/p1/members` | `{"user_id":"u2","role":"member"}` | `201 {"project_id":"p1","user_id":"u2","role":"member"}` |
| `PATCH /api/v1/projects/p1/members/u2` | `{"role":"owner"}` | `200 {"project_id":"p1","user_id":"u2","role":"owner"}` |
| `DELETE /api/v1/projects/p1/members/u2` | Bearer auth | `204 No Content` |
| `GET /api/v1/projects/p1/quota` | Bearer auth | `200 {"state":"pending","version":1,"desired":{"max_replicas":4,"cpu_request":"400m","cpu_limit":"2","memory_request":"512Mi","memory_limit":"2Gi"},"applied_version":null}` |
| `PATCH /api/v1/projects/p1/quota` | `If-Match: "1"`, `Idempotency-Key: key-2`; `{"max_replicas":2,"cpu_limit":"1","memory_limit":"1Gi"}` | `200 {"state":"pending","version":2,"desired":{"max_replicas":2,"cpu_request":"200m","cpu_limit":"1","memory_request":"256Mi","memory_limit":"1Gi"},"applied_version":null}` |

### Deployment and operation examples

| Endpoint | Example request | Example response |
| --- | --- | --- |
| `POST /api/v1/projects/p1/deployments` | `{"name":"web","image":"nginx:1.27","port":8080,"replicas":2,"idempotency_key":"k1"}` | `202 {"deployment_id":"d1","revision_id":"r1","kubernetes_resource_name":"mc-d1","job_state":"queued","status":"accepted","last_error":null}` |
| `GET /api/v1/projects/p1/deployments?limit=50&state=ready` | Bearer auth | `200 {"items":[{"id":"d1","name":"web","state":"ready","ready_replicas":2}],"next_cursor":null}` |
| `GET /api/v1/projects/p1/deployments/d1` | Bearer auth | `200 {"id":"d1","desired_revision_id":"r1","state":"ready","observed_generation":1,"ready_replicas":2,"observation_stale":false}` |
| `PATCH /api/v1/projects/p1/deployments/d1` | `If-Match: "1"` + key; `{"image":"registry.example/web:1.2.4"}` | `202 {"revision_id":"r2","operation_id":"op4","state":"accepted"}` |
| `GET /api/v1/operations/op3` | Bearer auth; member of owning project | `200 {"id":"op3","revision_id":"r1","state":"progressing","observed_generation":1,"ready_replicas":1,"observation_stale":false}` |
| `PATCH /api/v1/projects/p1/deployments/d1/scale` | `{"replicas":3,"expected_replicas":2,"idempotency_key":"scale-k1"}` | `202 {"deployment_id":"d1","revision_id":"r3","kubernetes_resource_name":"mc-d1","job_state":"queued","status":"accepted","last_error":null}` |
| `POST /api/v1/projects/p1/deployments/d1/rollback` | `{"revision_id":"r1","expected_revision_id":"r3","idempotency_key":"rollback-k1"}` | `202 {"deployment_id":"d1","revision_id":"r4","kubernetes_resource_name":"mc-d1","job_state":"queued","status":"accepted","last_error":null}` |
| `GET /api/v1/projects/p1/deployments/d1/revisions?limit=20` | Bearer auth | `200 {"items":[{"id":"r4","revision_number":4,"name":"web","image":"nginx:1.27","port":8080,"replicas":2,"created_by_user_id":"u1","rollback_of_revision_id":"r1","created_at":"2026-11-14T12:00:00Z"}],"next_cursor":null}` |
| `GET /api/v1/projects/p1/deployments/d1/events?limit=50` | Bearer auth; optional cursor | `200 {"items":[{"id":"e1","event_type":"ready","message":"Observed 1/1 ready replicas.","created_at":"2026-11-14T12:00:00Z"}],"next_cursor":null}` |

### Observability and platform examples

| Endpoint | Example request | Example response |
| --- | --- | --- |
| `GET /api/v1/projects/p1/deployments/d1/logs?tail_lines=100` | Bearer auth; bounded `container`/`since_seconds` optional | `200 {"items":[{"pod_name":"web-abc","container":"app","log":"2026-11-14T12:00:00Z server started","truncated":false}],"truncated":false,"source_time":"2026-11-14T12:00:01Z"}` |
| `GET /api/v1/projects/p1/deployments/d1/kubernetes-events` | Bearer auth; API diagnostics identity configured | `200 {"items":[{"resource_kind":"Pod","resource_name":"web-abc","event_type":"Warning","reason":"BackOff","message":"Back-off restarting failed container","created_at":"2026-11-14T12:00:00Z"}],"truncated":false,"source_time":"2026-11-14T12:00:01Z"}` |
| `GET /api/v1/projects/p1/deployments/d1/metrics?window=5m` | Bearer auth | `503 {"error":{"code":"METRICS_UNAVAILABLE","message":"No metrics source is configured.","details":[],"correlation_id":"req1"}}` |
| `GET /health/live` | No credentials; internal routing policy | `200 {"status":"ok"}` |
| `GET /health/ready` | No credentials; internal routing policy | `503 {"status":"not_ready","components":{"database":"unavailable","worker":"unknown"}}` |
| `GET /api/v1/capabilities` | Authenticated | `200 {"api_version":"v1","metrics":{"available":false,"status":"unavailable","reason":"No metrics provider is configured."},"alerts":{"available":false,"status":"unavailable","reason":"Alert delivery is not implemented."}}` |

### Alert examples (SHOULD/future)

| Endpoint | Example request | Example response |
| --- | --- | --- |
| `GET /api/v1/projects/p1/alerts?limit=50` | Owner/member read permission | `200 {"items":[],"next_cursor":null,"available":false}` |
| `POST /api/v1/projects/p1/alerts` | Owner; `{"name":"stalled rollout","condition":"rollout_stalled","window_seconds":300,"severity":"warning"}` | `201 {"id":"a1","enabled":true,"delivery_status":"not_configured"}` |
| `PATCH /api/v1/projects/p1/alerts/a1` | Owner; `{"enabled":false}` | `200 {"id":"a1","enabled":false}` |
| `DELETE /api/v1/projects/p1/alerts/a1` | Owner | `204 No Content` |
| `GET /api/v1/projects/p1/alerts/events?limit=50` | Owner/member read permission | `200 {"items":[],"next_cursor":null}` |

## Error examples

Authorization failure:

```json
{"error":{"code":"FORBIDDEN","message":"You do not have access to this project.","details":[],"correlation_id":"req_123"}}
```

Unavailable Kubernetes infrastructure:

```json
{"error":{"code":"DEPENDENCY_UNAVAILABLE","message":"Kubernetes observations are temporarily unavailable.","details":[{"component":"kubernetes","retryable":true}],"correlation_id":"req_124"}}
```

Synchronous validation errors identify field and allowed constraint, not secret
values. Timeout errors include retryability and operation ID only after durable
acceptance. A failed rollout is operation data, not an accepted-success
response. Rate limiting returns 429 and bounded `Retry-After`. Internal traces
are logged only in protected redacted telemetry.

## Concurrency, versioning, security

Mutation requests include `If-Match` generation for stale-write detection and
`Idempotency-Key` for network retries. A duplicate identical request returns
same operation. Mismatched payload key is 409. API v1 preserves response
compatibility; breaking contract requires `/api/v2` or documented deprecation.
Enforce auth before project-scoped reads; parameterized DB operations; body
limits; per-account/project throttles; CORS allowlist; CSRF for cookie flows.
Correlation IDs are generated/validated safely and never used as authorization.

New projects start with a 2 CPU / 2Gi / 4-replica project policy. Existing
projects are left unconfigured by migration; an owner creates their policy
with `If-Match: "0"`. These maximum values are operator policy, not cluster
capacity guarantees. Current Pod requests are 100m CPU/128Mi memory and limits
are 500m CPU/512Mi memory. API deployment, scale, and rollback admission
checks reject requests above the configured policy before enqueue. The API
does not yet reconcile per-project Namespaces, ResourceQuota, or LimitRange;
`applied_version` remains null and the response explicitly reports `pending`.
