# 12 — Testing Strategy

**Status:** Planned test design. No tests have been executed or pass results
claimed in Phase 1. Test names below are stable planned evidence labels.

## Test pyramid and evidence

| Category | Purpose / level | Setup and mocks | Expected evidence |
| --- | --- | --- | --- |
| Unit | State transitions, validation, quotas, redaction, retry schedule | Pure functions; mock external clock/client only | Deterministic tests for legal/illegal inputs and transitions |
| Service | Authorization, transaction composition, idempotency, policy | Test repository/DB transaction; Kube adapter fake only at service boundary | Commit/rollback and denied access assertions |
| PostgreSQL integration | Constraints, SQLAlchemy queries, row locks, job leases | Disposable PostgreSQL database/schema per run | Applied migrations, uniqueness, transaction and concurrency result |
| Migration | Fresh install and previous schema upgrade | Empty DB + DB populated with prior migration fixtures | Alembic upgrade completes, schema/data checks pass; no production DB |
| API contract | Status/schema/error/OpenAPI consistency | FastAPI test client + test DB/dependency overrides | Contract snapshots, body limits, stable error code/correlation |
| Auth/security | Password/session rotation, permissions, CSRF, rate limits | Test users and explicit roles | Raw secrets absent; generic failure; deny-by-default evidence |
| Kubernetes integration | Actual object apply, rollout, logs/events, quota, recovery | Explicit disposable kind cluster/context and unique namespaces | Captured kind version/context, manifests observed, cleanup log |
| Worker recovery | Lease expiry, duplicate execution, restart | PostgreSQL integration + disposable worker | One effective revision; no false ready; retry evidence |
| Frontend component | State rendering, form errors, keyboard semantics | Vitest/Testing Library; fixture data clearly marked | Loading/empty/error/stale/unavailable/accessibility assertions |
| CLI | Commands, output, exit codes, token handling | HTTP test server; credential store adapter fake in unit tests | Correct 202 vs ready UX, error/correlation output |
| E2E | Registration → project → deploy/status/log/scale/rollback | API/UI + DB + isolated kind | Screenshots/artifacts only from actual test run; preserve failure evidence |
| Performance | API and worker behavior under defined load | Disposable seeded local environment | Raw run data, host/versions/request mix/sample count; not production availability |
| Failure injection | Dependency and worker failures | Controlled DB/Kube stop or network fault only in test environment | Recovery timeline and explicit final state |
| Regression | Prevent fixed bug recurrence | Add focused case at relevant layer | Linked issue/failure analysis and repeatable test |

## Isolation and safety rules

- Each integration run uses a uniquely named disposable DB and namespace, and
  a context explicitly checked against an allowlisted test-cluster name.
- Tests must refuse to run destructive cleanup if context is absent or not
  recognized. Never apply/delete against an arbitrary current context.
- Never use production data. Seed synthetic accounts/projects/images only.
- Cleanup removes only resources carrying unique test-run and project identity
  labels; verify target names before deletion.
- Unit tests may mock Kube API, but a release candidate also needs the real
  kind workflows below.
- Capture versions, command, context, timestamps, logs and test result; failed
  tests remain failures rather than skipped-success.

## Critical scenarios

| ID / scenario | Level/setup | Expected result / evidence |
| --- | --- | --- |
| T-DEPLOY-01 successful deployment | API + PostgreSQL + disposable kind, pullable image | 202 operation progresses; actual Deployment generation and ready Pod observed before state ready |
| T-DEPLOY-02 invalid container image syntax | API schema | 422 field validation; no DB job/Kube object |
| T-DEPLOY-03 unpullable image | kind, syntactically valid image that cannot pull | accepted operation later fails with image-pull evidence; never ready |
| T-DEPLOY-04 failed readiness probe | kind test image with wrong probe | progressing then failed/deadline; events available, diagnostic reason |
| T-RECOVERY-01 Kubernetes API unavailable | stop/restrict test API only | queued/retry state; no false ready; recovers after access restored |
| T-RECOVERY-02 PostgreSQL unavailable | disposable DB interruption | pre-commit request fails with no acceptance; worker health not silently green |
| T-RECOVERY-03 worker crash during apply | kill test worker process only | lease expiry/reclaim; same object identity; eventual observed result or explicit failure |
| T-RETRY-01 duplicate request | concurrent identical idempotency key/body | one revision/job; same operation ID returned |
| T-RETRY-02 key/body mismatch | reuse key with modified payload | 409; existing operation unaffected |
| T-SCALE-03 concurrent scale | two ETag-matching requests | single winner, loser 409, deterministic generation |
| T-AUTHZ-01 unauthorized project access | separate users/projects | no deployment/log/event/metric data and no Kube read |
| T-AUTH-04 expired authentication | expired access/refresh records | reject protected operation; no mutation; client prompts/refresh behavior |
| T-QUOTA-01 quota exceeded | policy + Kube ResourceQuota | request rejected before enqueue or cluster backstop failure visible |
| T-ROLLBACK-01 rollback success | at least two retained revisions | new revision references target; readiness observed; prior history preserved |
| T-ROLLBACK-02 rollback failure | target image unavailable | failed new revision; target remains; evidence surfaced |
| T-METRICS-01 provider missing | no metrics API/provider | explicit unavailable; no numeric fake sample; manual scale remains |
| T-OBS-01 missing logs | terminated/removed Pod | typed missing/unavailable outcome, not empty healthy response |
| T-MIGRATION-01 migration failure | deliberately invalid migration in disposable DB | startup/readiness fail explicitly; backup data untouched |

## Category-specific expectations

### Unit and service tests

Exercise image/config allowlist, non-secret env rules, resource quantity parsing,
stable error mapping, state machine, retry limits, lease extension, idempotency
hash, owner invariant, optimistic generation and redaction. Test each service
with allow/deny roles and DB failures. Do not mock authorization in tests whose
purpose is authz.

### Database/API contract tests

Run PostgreSQL, not only SQLite, for transaction/JSONB/locking semantics.
Verify every API response with Pydantic/OpenAPI contract; verify HTTP 202,
terminal operation failure representation, cursor pagination, rate-limit/body
caps, correlation ID, and no stack traces.

### Kubernetes and worker tests

Mocks cover transient unit behavior, not acceptance evidence. Disposable kind
must validate namespace labels/quota, Deployment+Service selectors, rollout
readiness, events/log access, forbidden resource policy, manual scale, rollback,
and reconciliation after crash. Test RBAC using actual service account
permissions. Explicitly record when kind/cluster is unavailable; do not call
the feature validated.

### UI, CLI, E2E

Components test accessible names, tab order, visible focus, status text, no
hardcoded metrics, error/empty/unavailable states. CLI tests 202 vs ready, exit
codes, JSON output, token redaction, expiry, idempotent retry. E2E covers full
registration/project/deploy/status/failure/log/scale/rollback lifecycle against
real integration backend and cluster.

### Security, performance, failure

Threat-derived SEC tests include IDOR route matrix, privilege escalation,
forbidden Pod fields, malicious log/HTML escaping, secret markers, CSRF/CORS,
request sizes, brute-force throttling, DB injection patterns, dependency and
secret scanners. Load testing starts from an explicit request mix and host
budget; reports p50/p95, error rate, concurrency, sample count, warmup, versions.
No test output is interpreted as real-world availability.

### Named planned test catalog

These identifiers are plans, not existing test files or executed cases.

| ID | Purpose and setup | Expected result |
| --- | --- | --- |
| T-AUTH-01..06 | registration, duplicate, hash verification, login/logout, expiry, refresh rotation/replay | Valid session only; generic invalid credential; raw password/token absent |
| T-PROJ-01..04 | project creation/owner, slug conflict, list scope, namespace setup conflict | Owner established; unauthorized project not exposed; foreign namespace not adopted |
| T-AUTHZ-01..08 | two users/projects across project, deployment, revision, operation, events, logs, metrics, membership | Every unauthorized request denied before Kube read; revocation effective |
| T-STATE-01..06 | legal/illegal transitions, observed generation, ready count, stale source, timeout, failed probe | Only valid transition; no ready from stale/mismatched observation |
| T-HISTORY-01..04 | revision order, cursor, concurrent insert, immutable snapshot | Stable bounded pages and unchanged old desired config |
| T-SCALE-01..05 | bounds, quota, matching ETag, stale ETag, concurrent writers | One accepted generation; stale writer gets conflict |
| T-ROLLBACK-01..05 | successful rollback, failed image, wrong project target, stale generation, duplicate key | New revision references prior; history preserved; readiness observed |
| T-DIAG-01..05 | image-pull event, probe failure, redaction, missing Pod logs, Kube read outage | Sanitized evidence or explicit typed unavailable result |
| T-OBS-01..05 | log/event bounds, no source, source freshness, access check, truncation | Actual source metadata or explicit unavailable; no silent fabricated success |
| T-METRICS-01..04 | provider absent, stale sample, actual sample, provider timeout | Values are real and timestamped or explicitly unavailable |
| T-QUOTA-01..04 | policy bound, API reject, Kube backstop, drift | Exceeded request blocked; drift visible |
| T-CLI-01..06 | auth, deploy 202, wait terminal failure, JSON, token safety, unavailable API | Correct exit/output distinctions and no credential disclosure |
| T-UI-01..06 | keyboard core flow, status labels, empty/error/loading/unavailable, stale auth, no fixture confusion | Accessible truthful state and safe recovery |
| T-RETRY-01..02 | duplicate concurrent request and changed body under same key | One operation for identical request; changed payload 409 |
| T-RECOVERY-01..06 | Kube outage, DB outage, worker crash before/after apply, lease expiry, exhausted retries, foreign object | Recover or fail visibly without false ready/duplicate workload |
| T-SEC-RBAC | inspect rendered service-account permissions and run `kubectl auth can-i` in disposable test cluster | Only reviewed verbs allowed; no cluster-admin |
| T-SEC-WORKLOAD | submit privileged, hostPath, host-network, arbitrary service-account and dangerous capability fields | Requests rejected and generated objects remain constrained |
| T-SEC-SECRET | seed recognizable secret markers in prohibited fields/log/error fixtures | Marker absent from DB, API errors, audit, and structured logs |
| T-SEC-ABUSE | oversized body, repeated login, mutation flood, invalid origin/CSRF | bounded rejection/429; no protected mutation occurs |
| T-SEC-AUDIT | successful and failed privileged change plus audit storage fault | Committed mutation has audit; audit failure prevents commit |
| T-SEC-ERROR | force unhandled internal error using safe test fixture | Stable error envelope; no stack/secret in client response |
| T-SETUP-01..04 | clean local prerequisites, empty DB migration, approved kind context guard, startup/health/teardown | Documented checkpoint reproducible or explicit blocker; no unrelated resources removed |
| T-ALERT-01..04 | rule window, dedupe, delivery failure/retry, recovery | One persisted event; delivery result and resolved condition accurate |
| T-MIGRATION-01 | invalid/failing migration against disposable DB | Startup fails visibly; no destructive fallback or hidden migration |

## Test-data cleanup and reporting

Seed data is namespaced by test run; test accounts and cluster objects are
synthetic. Never use broad namespace deletion, wildcard resource deletion, or
global cluster cleanup. Preserve logs on failure, redact secrets before
artifact upload, and set artifact retention. A report records actual command,
date, commit, tool versions, case count, pass/fail/skip and reason. At Phase 1,
this plan is documentation only; none of these tests has run.
