# API Reference

The API is FastAPI, versioned under `/api/v1`, with generated OpenAPI available
at `/openapi.json` and interactive docs at `/docs` in the current app
configuration. Local base URL is `http://127.0.0.1:8001`; the isolated demo
uses `http://127.0.0.1:18017`.

Protected endpoints accept `Authorization: Bearer <access-token>`. Tokens
must not be logged or persisted by clients. Errors use FastAPI `detail`
responses; a universal error envelope is not yet implemented. Every response
includes a server-generated UUID in the `X-Request-ID` header. Client-supplied
request IDs are ignored. The API includes the ID in request completion and
error logs, and CORS exposes the response header to the Web client. The ID is
persisted with newly accepted deployment and scale jobs and their initial
lifecycle events; Worker lifecycle events and job logs reuse that ID. A
replayed idempotent request returns a fresh HTTP request ID but does not
replace the original ID attached to the existing operation. Older jobs and
events remain nullable and may have no recorded request ID. Kubernetes
resource metadata does not currently include request IDs.

## Routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register` | Register and return user/access token |
| POST | `/api/v1/auth/login` | Authenticate and return access token |
| GET | `/api/v1/auth/me` | Read current user |
| POST | `/api/v1/auth/logout` | Revoke the presented token |
| GET/POST | `/api/v1/projects` | Cursor-paginated accessible projects / create a project |
| GET/PATCH | `/api/v1/projects/{project_id}` | Read/update a project |
| GET/PATCH | `/api/v1/projects/{project_id}/quota` | Read/update persisted project resource policy |
| GET/POST | `/api/v1/projects/{project_id}/members` | List/add project members |
| PATCH/DELETE | `/api/v1/projects/{project_id}/members/{user_id}` | Update role/remove member |
| POST | `/api/v1/projects/{project_id}/deployments` | Submit an asynchronous deployment |
| GET | `/api/v1/projects/{project_id}/deployments` | Cursor-paginated project deployments |
| GET | `/api/v1/projects/{project_id}/deployments/{deployment_id}` | Read a deployment |
| PATCH | `/api/v1/projects/{project_id}/deployments/{deployment_id}/scale` | Submit an idempotent replica scaling operation |
| POST | `/api/v1/projects/{project_id}/deployments/{deployment_id}/rollback` | Roll back to a retained revision as a new operation |
| GET | `/api/v1/projects/{project_id}/deployments/{deployment_id}/revisions` | Cursor-paginated immutable desired-state revisions |
| GET | `/api/v1/projects/{project_id}/deployments/{deployment_id}/events` | Read cursor-paginated lifecycle events |
| GET | `/api/v1/projects/{project_id}/deployments/{deployment_id}/logs` | Read bounded, redacted Pod logs |
| GET | `/api/v1/projects/{project_id}/deployments/{deployment_id}/kubernetes-events` | Read filtered Kubernetes Events |
| GET | `/api/v1/operations/{deployment_id}` | Read operation/deployment status |
| GET | `/api/v1/capabilities` | Read real platform capability availability |
| GET | `/health`, `/livez`, `/readyz` | Process and database health |

Project and deployment list routes accept `limit` (1-100, default 50) and an
optional opaque `cursor`. Responses contain `items` and `next_cursor`; pass the
returned cursor unchanged to fetch the next page. Ordering is descending by
creation time and ID so records with equal timestamps remain stable. Invalid
cursors return HTTP 400. Lifecycle events and deployment revisions use the
same cursor contract.

Pod logs and Kubernetes Events require an explicitly configured API
diagnostics identity, separate from the Worker identity. The API returns
explicit HTTP 503 when diagnostics are not configured or the Kubernetes
source is unavailable. Logs are limited to 10 Pods and 64 KiB per Pod, and
Events to 100 records; common credential patterns are redacted. These are
live Kubernetes reads, separate from persisted MiniCloud lifecycle events.
Project quota GET/PATCH is implemented, but cluster application is not:
configured policies report `pending`, and existing projects without a policy
report `unconfigured`. New projects receive the bounded default policy.
Project owners can change policy within the current operator maximum; updates
require `If-Match` and `Idempotency-Key`. Quota and replica limits are checked
before deployment, scale, and rollback jobs are enqueued. The current
per-container requests are 100m CPU/128Mi memory and limits are 500m CPU/512Mi
memory; the project maximum is 2 CPU/2Gi and 4 replicas. No Kubernetes
ResourceQuota, LimitRange, or per-project namespace is reconciled, so this
API admission check is not a Kubernetes quota/backstop claim. Project
deletion and audit read endpoints remain unavailable.

## Project quota policy

`GET /api/v1/projects/{project_id}/quota` is member-authorized and returns the
desired policy, version/ETag, and whether it is still pending or unconfigured.
New projects start at 4 replicas, 2 CPU, and 2Gi memory; their aggregate
requests are derived from the fixed Pod requests. Existing projects are not
silently assigned a policy by migration: an owner must configure one using
`If-Match: "0"`. Policy updates are owner-only, version-checked, idempotent by
project-scoped `Idempotency-Key`, and limited to 4 replicas / 2 CPU / 2Gi.
The API rejects invalid or internally inconsistent quantities and prevents
deployment creation, scaling, or rollback from exceeding the persisted
policy before writing jobs or revisions. The Kubernetes-applied version
remains null until a future namespace/quota reconciler applies it.
The Web dashboard shows the policy state and desired limits, and project owners
can update them there. A `pending` state is not evidence that Kubernetes
ResourceQuota or LimitRange has been applied.

## Deployment submission

The create route validates the request, persists a deployment and job, and
returns HTTP 202. Clients must treat this as accepted work, not readiness.
An optional idempotency key is unique within a project; reuse with a changed
payload returns a conflict. Worker status is observable through the operation
route and lifecycle events. Creation persists revision 1 and links it from
the durable job in the acceptance transaction.

## Scaling

`PATCH /api/v1/projects/{project_id}/deployments/{deployment_id}/scale` accepts
`{"replicas": 3, "expected_replicas": 2, "idempotency_key": "scale-request-1"}`.
Replica counts must be 1–10, the key and expected current count are required,
and the deployment must currently be `ready`. The API locks the deployment
row and rejects stale expected counts, overlapping operations, and requests
for the current replica count with HTTP 409. This prevents a second scale
request based on an outdated replica count from silently replacing the first.
A successful request returns HTTP 202, persists a `scale` job and lifecycle
event, and records the target count before the Worker reconciles the
Kubernetes Deployment. Repeating the same key and payload returns the
existing operation; reusing the key for a different operation or target
returns HTTP 409.

## Revision history and rollback

Deployment creation and each accepted scale request append a snapshot of the
desired name, image, port, and replica count. The revision list is project
authorized, newest first, and cursor-paginated. Existing deployments are
backfilled with a single snapshot of their current configuration; earlier
unrecorded history cannot be reconstructed.

`POST /api/v1/projects/{project_id}/deployments/{deployment_id}/rollback`
requires this body:

```json
{
  "revision_id": "target-revision-uuid",
  "expected_revision_id": "current-revision-uuid",
  "idempotency_key": "unique-request-key"
}
```

The target must belong to the same deployment, the expected revision must
still be current, and the deployment must be ready with no active operation.
The API persists a new revision copied from the target and records its
`rollback_of_revision_id`; it never rewrites or removes revision history. The
new job uses the normal Worker apply/observe path. HTTP 202 means the rollback
was durably accepted, not that Kubernetes readiness was verified. Rollback
idempotency and API/Worker persistence are covered by tests, but successful
Kubernetes rollback readiness remains unverified pending the approved
disposable-cluster test.

The CLI includes request examples and handles tokens in memory:
[CLI guide](CLI.md).
