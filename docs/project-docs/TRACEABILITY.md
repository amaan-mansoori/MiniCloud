# Requirements Traceability Matrix

**Status:** Design-level traceability; implementation/test evidence is pending.
Canonical requirement definitions are in [03-requirements.md](03-requirements.md),
stories in [04-user-stories.md](04-user-stories.md), API in
[08-api-spec.md](08-api-spec.md), data in [07-database-design.md](07-database-design.md),
and test plan in [12-testing.md](12-testing.md).

| Requirement | Story | Component | API / interface | DB entity | Planned test | Evaluation |
| --- | --- | --- | --- | --- | --- | --- |
| FR-001 | US-001, US-018 | AuthN | `/auth/*` | users, sessions | T-AUTH-01..06 | Auth lifecycle / authorization correctness |
| FR-002 | US-002 | Project service | `/projects` CRUD | projects, memberships | T-PROJ-01..04 | Functional completeness |
| FR-003 | US-003 | AuthZ/project service | `/projects/{id}/members` | project_memberships, audit_events | T-AUTHZ-01..05 | Authorization matrix |
| FR-004 | US-004 | Deployment service/worker | POST deployments | deployments, revisions, jobs | T-DEPLOY-01/04 | Deployment outcome |
| FR-005 | US-004 | Validation layer | deploy/scale body | revisions | T-DEPLOY-02/03, SEC input corpus | Requirement completeness |
| FR-006 | US-002, US-013 | Namespace provisioner | project/quota APIs | projects, resource_policies | T-PROJ-04, T-QUOTA-01 | Resource-limit enforcement |
| FR-007 | US-004 | Kubernetes adapter | deploy API | deployments, revisions | T-DEPLOY-01/04 | Deployment success |
| FR-008 | US-005, US-006 | Worker/reconciler | operation/detail | revisions, events | T-STATE-01..06 | Time to readiness / truth |
| FR-009 | US-007 | Project/deployment service | list revisions/events | revisions, events | T-HISTORY-01..04 | Functional completeness |
| FR-010 | US-008 | Deployment service/worker | scale POST | revisions, jobs | T-SCALE-01..05 | Concurrency correctness |
| FR-011 | US-009 | Deployment service/worker | rollback POST | revisions, jobs, audit | T-ROLLBACK-01..05 | Recovery correctness |
| FR-012 | US-006, US-010 | Kube adapter/API | logs/events GET | events (logs live) | T-DIAG-01..05, T-OBS-01..05 | Monitoring completeness |
| FR-013 | US-011, US-012 | Metrics adapter | metrics/capabilities | none for MVP samples | T-METRICS-01..04 | Monitoring completeness |
| FR-014 | US-013 | Policy/namespace adapter | quota GET/PATCH | resource_policies | T-QUOTA-01..04 | Resource-limit enforcement |
| FR-015 | US-014 | Typer CLI/API client | API v1 operations | API-backed | T-CLI-01..06 | Usability/reproducibility |
| FR-016 | US-015 | Next.js UI | API v1 operations | API-backed | T-UI-01..06 | Usability/accessibility |
| FR-017 | US-016 | Alert evaluator (SHOULD) | alerts API (future) | alert_rules/events deferred | T-ALERT-01..04 | Monitoring completeness |
| FR-018 | US-014, US-020 | FastAPI routes/schema | `/api/v1`, health | N/A | contract/OpenAPI tests | API correctness |
| FR-019 | US-004, US-008, US-009 | Service/DB | mutation headers | idempotency_records | T-RETRY-01/02 | Retry correctness |
| FR-020 | US-012, US-020 | Health/capability service | `/health/*`, capabilities | N/A | OPS-001 dependency tests | Reproducibility |
| NFR-001 | US-020 | API middleware | all routes | telemetry sink (optional) | load test | API latency |
| NFR-002 | US-005 | Reconciler | operation/detail | revisions/events | T-STATE-01..06 | Deployment accuracy |
| NFR-003 | US-019 | Worker/job service | operation status | deployment_jobs | T-RECOVERY-03 | Recovery/retry |
| NFR-004 | US-019 | Reconciler + adapter | operation/detail | deployments/revisions | failure injection | Consistency/recovery |
| NFR-005 | US-008 | Service/DB | ETag mutation | deployment version/jobs | T-SCALE-03 | Concurrency |
| NFR-006 | US-020 | Compose/kind profiles | N/A | N/A | resource sampling | Local resource use |
| NFR-007 | US-015 | UI component system | browser | N/A | T-UI-01..06, manual a11y | Accessibility |
| NFR-008 | US-020 | Module/build design | all | all | CI/code review | Maintainability |
| NFR-009 | US-020 | Setup/CI | health/deploy | migrations | T-SETUP-01..04 | Reproducibility |
| NFR-010 | US-007, US-010 | API query layer | list/logs/events | indexed rows | T-HISTORY/OBS pagination | API correctness |
| SEC-001 | US-001, US-018 | AuthN/session | `/auth/*` | users, sessions | T-AUTH-01..06 | Authorization correctness |
| SEC-002 | US-003, US-005, US-017 | AuthZ dependency | all project APIs | memberships | T-AUTHZ-01..08 | Authorization correctness |
| SEC-003 | US-017 | Project scope/Kube adapter | nested project APIs | FKs + labels | T-AUTHZ-01..08 | Authorization correctness |
| SEC-004 | US-004 | Kube identity/RBAC | internal adapter | none | T-SEC-RBAC | Security review evidence |
| SEC-005 | US-010 | config/log redactor | deploy/log APIs | nonsecret snapshots | T-SEC-SECRET | Secret protection |
| SEC-006 | US-004 | request validator | deployment API | revision spec | T-SEC-WORKLOAD | Resource-limit/security |
| SEC-007 | US-001 | HTTP middleware | auth/all APIs | rate-limit store TBD | T-SEC-ABUSE | API correctness |
| SEC-008 | US-003, US-009, US-013 | audit service | mutations | audit_events | T-SEC-AUDIT | Functional completeness |
| SEC-009 | US-006, US-014 | error/logging layer | all endpoints/CLI | redacted events | T-SEC-ERROR | Authorization/security |
| OPS-001 | US-018, US-019 | health checks | `/health/*` | DB/worker | dependency fault tests | Recovery |
| OPS-002 | US-006 | structured telemetry | API/worker | lifecycle events | correlation/redaction test | Monitoring completeness |
| OPS-003 | US-019 | runbooks/reconciler | operator surface | jobs/events | recovery drill | Recovery from worker failure |
| OPS-004 | US-020 | GitHub Actions | CI | N/A | workflow validation | Reproducibility |
| OPS-005 | US-002 | retention/backup worker | project delete | project graph/audit | restore/deletion drill | Data recovery |

## Coverage findings and gaps

- Every defined FR/NFR/SEC/OPS item has an intended component and verification
  method; none is left without a design or planned verification path. All are
  **planned**, not implemented evidence.
- Alert delivery, metrics-provider integration, registration policy, session
  exact format, private registry access, namespace bootstrap permissions,
  production TLS/backup, and end-user retention requirements remain design
  decisions/open questions. Their MUST/SHOULD/Could boundaries are explicit.
- The kube RBAC test label `T-SEC-RBAC`, workload input `T-SEC-WORKLOAD`,
  secret redaction `T-SEC-SECRET`, abuse `T-SEC-ABUSE`, audit `T-SEC-AUDIT`,
  and error safety `T-SEC-ERROR` are planned test cases that need concrete
  test files and execution evidence in a later implementation phase.
- Feature with intentionally deferred implementation: FR-013 metrics and
  FR-017 alerts (SHOULD); product must report them unavailable until real source
  and delivery path pass tests.
- Numeric NFR-001 and NFR-006 thresholds are provisional; evaluation requires
  recording actual host/hardware and revising the targets from evidence.
