# 11 — Security Requirements and Threat Model

**Status:** Living threat model for an educational local system. This is not
a security certification, penetration-test result, or hostile multi-tenant
guarantee. Implemented controls are distinguished from future requirements.

## Security objectives and boundaries

Protect account credentials, project configuration, deployment operations,
logs/events, session tokens, audit data, and cluster integrity. Authentication
answers “who is the actor?” Authorization answers “may that actor perform this
action on this project/resource?” Kubernetes RBAC constrains the control-plane
identity's API verbs; it does not replace application-level membership checks.
Ordinary users never receive cluster credentials, unrestricted Kubernetes API
access, raw manifests, or cluster-admin.

Namespace isolation groups project workloads and supports quota/policy, but is
not automatically a complete boundary for hostile tenants. Shared kernel,
node, CNI, admission policy, service accounts, secrets, and cluster
configuration remain relevant. MVP is for trusted educational use, not
untrusted public workloads.

## Identity and sessions

The current API uses Argon2 password hashing and bearer JWT access tokens.
Tokens include `sub`, `iat`, `exp`, and `jti`, with a configurable expiry
bounded to 1–1440 minutes (60 minutes by default). Logout stores a SHA-256
fingerprint in PostgreSQL with the token expiry; protected requests check the
revocation table, including for older valid tokens without `jti`; if a `jti`
claim is present, it must be a non-empty string. Expired rows are removed
during a later logout; PostgreSQL uses the narrowly scoped cleanup function
after its migration has been applied, while SQLite tests exercise only the
filtered ORM fallback. There is no periodic cleanup task. Database errors fail
closed and are returned without connection details. Revocation is shared
across API processes using the same database and survives process restarts. A
stolen token remains valid until expiry or logout.
There is no refresh-token flow, browser HttpOnly cookie, or CLI credential
store yet. Password verification returns the same authentication error for
unknown users and wrong passwords.

## Authorization and isolation

Default deny. API route resolves current membership on every project-scoped
request and verifies the object belongs to that project before querying
deployments or Kubernetes state. Membership lifecycle and owner/member
authorization are implemented and covered by tests. Broader hostile-tenant
assurance remains outside the MVP. The MVP has no arbitrary cluster role
binding by end users. Avoid cached role decisions unless invalidation
semantics are tested. DB failure fails closed.

Kubernetes service account is separate from users and uses a namespace-scoped
Role. The current worker has only the permissions listed in
`infra/kubernetes/namespace-rbac.yaml`; it does not receive Secret access,
delete, or cluster-wide permissions. Cluster-level namespace provisioning
requires separately reviewed narrow permission; split operator provisioner
from worker if required permissions otherwise become broad. Audit actual
`can-i` output. Do not bind cluster-admin.
The worker ServiceAccount token is automounted only for the worker identity
because the supported in-cluster client mode requires Kubernetes API
authentication; generated application Pods explicitly disable token
automounting.

## Workload and input constraints

The current generated workload disables privilege escalation and ServiceAccount
token automounting, drops capabilities except `NET_BIND_SERVICE`, sets
`privileged: false`, CPU/memory requests and limits, and uses `RuntimeDefault`
seccomp. Port and typed deployment input validation are covered by tests.
Workloads now require an image configured for non-root execution. Images that
require root are rejected by Kubernetes rather than granted additional
privileges. Read-only-root-filesystem enforcement, image provenance/signature
scanning, admission policy, and project ResourceQuota are future scope. A valid
image reference can still resolve to malicious content; registry/image
supply-chain risk remains.

MVP environment variables are non-secret only; secret values are not accepted
or stored. Users can refer to operator-provisioned Kubernetes Secret keys under
policy. Secrets mounted in workload remain visible to that workload; MiniCloud
cannot protect against malicious app code.

## API and web protections

Compose host bindings are configuration, not proof of effective runtime
exposure. Verify the actual runtime port mappings in the intended environment
before deployment, and apply changes only through an approved recreation and
recovery plan. This repository does not claim that a Compose file alone
remediates already-running containers.

The current API rate-limits login and registration to 5 requests per 60 seconds
per client IP and endpoint by default, with configurable bounds. This fixed
window is in-memory and per process; it resets on restart and is not
distributed. Client identity comes from the direct ASGI peer, not untrusted
forwarded headers. Other protections in this section (including body limits,
CSP, security headers, TLS termination, and expensive-operation throttling)
remain requirements, not claims of implementation.

## Database runtime and migration roles

Compose supplies distinct `MINICLOUD_API_DATABASE_URL` and
`MINICLOUD_WORKER_DATABASE_URL` values to API and worker; migrations use the
separate `MINICLOUD_MIGRATION_DATABASE_URL`. In production, API and worker
configuration requires the matching `minicloud_api_runtime` and
`minicloud_worker_runtime` PostgreSQL role names, rejects standard superuser
names and weak credentials, and rejects cross-service role use. A production
migration service must use credentials distinct from the API runtime URL.
Alembic refuses to run unless `SERVICE_ROLE=migrate` and
`MIGRATION_DATABASE_URL` is set. The Compose migration service is opt-in under
the `migration` profile, and the API does not depend on migration completion;
operators must run it explicitly before an upgrade.

The `scripts/grant_minicloud_runtime.sql` helper provisions separate login
roles. The API role receives `users` SELECT/INSERT; `projects`
SELECT/INSERT/UPDATE; `project_memberships` SELECT/INSERT/UPDATE/DELETE;
`deployments` and
`deployment_jobs` SELECT/INSERT/UPDATE; `deployment_events` INSERT; and
`token_revocations` SELECT/INSERT plus EXECUTE on the cleanup function. The
worker role receives only `deployments` and `deployment_jobs` SELECT/UPDATE
and `deployment_events` INSERT. It cannot access user, project, membership,
revocation, migration, or cleanup-function data. The API role can delete
project-membership rows only; neither runtime role has sequence access, table
truncation, schema creation, table ownership, or `alembic_version` access. The
helper refuses to run while the legacy shared
`minicloud_runtime` role exists; review its grants and credentials separately
before any transition. The non-login
`minicloud_revocation_maintenance` function owner has DELETE on
`token_revocations` and column-level SELECT on `expires_at` only (needed to
evaluate the expiry predicate), plus schema USAGE needed by the fixed-path
function.
The function deletes only rows whose expiry has passed and grants no execution
to `PUBLIC`; the role migration refuses to adopt a pre-existing maintenance
role. The grant procedure inspects and does not rewrite existing maintenance
ACLs or PUBLIC ACLs.

The disposable PostgreSQL integration workflow
(`scripts\Test-MiniCloudPostgresIntegration.ps1`) applies migrations and
grants only in isolated test resources and verifies role grants, function
ownership/ACLs, and API/Worker database-role behavior. It does not establish
the configuration or compliance of any persistent deployment. Production
ownership, TLS, backups, and restore testing require operator-specific
procedures and independent verification.

## Audit, logs, backups, retention

Record actor, project, action, target, result, timestamp, correlation ID for
security-relevant mutation. Audit payload is allowlisted/redacted; append-only
to normal app role. Structured API/worker logs omit credentials, cookies,
authorization headers, secret env values, passwords, and raw refresh token.
Treat user Pod logs as untrusted and redact known secret values; redaction is
not perfect, so log access remains project-protected and bounded.

Backups include personal/project data: restrict access, encrypt where supported,
rotate/expire under policy, and test restore. Retention values are provisional;
privacy/legal requirements must be determined before production.

## Threat model

| Asset | Threat actor / attack surface | Mitigations | Residual risk |
| --- | --- | --- | --- |
| Project records/logs | Malicious authenticated user guesses IDs (IDOR) | Server membership checks before fetch; scoped queries; negative tests | Coding mistakes remain possible |
| Deployment permissions | User escalates member to owner or injects resource | Role matrix, allowlisted schema, audit, DB constraints | Operator-level compromise out of scope |
| Cluster | Malicious image attempts privileged/host access | Reject host/privileged options, least-privilege SA, quotas/admission where available | Kernel/runtime/CNI vulnerabilities; namespace alone insufficient |
| CPU/memory/node capacity | User creates many/large workloads | Per-project quota, replica bounds, global operator limits, rate limiting | Aggregate cross-project capacity planning remains operator duty |
| Secrets | App prints env/token or API reflects it | No raw secret storage, secret refs, redaction, output caps, project ACL | Workload can exfiltrate secrets it is given |
| Sessions | Token theft/replay, CSRF, XSS | Bounded JWT expiry and PostgreSQL-backed logout revocation | No refresh-token rotation, browser cookie/CSRF protection, or CSP claim |
| Authentication | Brute force/account enumeration | Generic errors, throttles, password hashing, audit | Distributed abuse requires operational tuning |
| API | Abuse/malformed oversized request | Schema/body limits, rate limits, timeouts, bounded log queries | Volumetric network attacks require upstream controls |
| Desired/observed status | Forged/stale DB state or foreign Kube object | Observe UID/generation, stale markers, reconciliation, identity labels | Manual cluster edits can create drift |
| Worker identity | Compromised worker uses broad permissions | Scoped SA, secrets handling, network restriction, audit, process isolation | Required write permissions are still powerful |
| PostgreSQL | Injection/credential misuse | Parameterized queries, least-priv DB roles, network boundary, backups | DB admin compromise can expose metadata |
| Dependency/build | Vulnerable package/image or CI secret leak | Lockfiles, dependency/container scan, secret scan, pinned CI actions, review | Scanner false negatives; supply chain |
| Audit/backup | Insider deletes or exposes evidence | Separate admin roles, retention, backup protection | No tamper-proof external archive in MVP |

## Security testing and response

Map threats to SEC requirements and T-SEC cases in [testing](12-testing.md).
Test cross-project denial on every endpoint; input corpus for forbidden fields;
secret marker scans across DB/API/log; rate/size enforcement; role permission
review; worker/Kube identity check; dependency/secret scanning in CI. Security
incident process and production hardening are not yet implemented; document
containment, credential rotation, evidence preservation, notification, and
recovery before any external deployment.

## Open security decisions

Registration policy, refresh-token flow, exact Argon2 library/parameters,
cluster-scoped namespace provisioner design, Kubernetes NetworkPolicy support,
admission policy availability, private registry auth, image signature/scanning,
data retention jurisdiction, TLS terminator, external secret manager, and
hostile-tenant threat model.
