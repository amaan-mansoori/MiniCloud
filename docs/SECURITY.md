# Security Notes

This is an implementation summary for an educational project, not a
penetration-test certification or a claim of production readiness. MiniCloud
is not designed for untrusted public workloads.

## Implemented controls

- Passwords are hashed with Argon2; authentication errors are generic.
- Access tokens have bounded lifetimes. Logout stores token fingerprints for
  revocation in PostgreSQL.
- Project membership and role authorization are checked server-side.
- Deployment input is schema-validated; generated workloads have resource
  bounds and restricted security settings.
- API, Worker, and migration database roles are configured separately.
- The Worker RBAC manifest is namespace-scoped and omits Secret access,
  delete, and cluster-wide permissions. Diagnostics use a separate
  read-only identity.
- Container build files configure non-root runtime users. `.dockerignore`
  excludes environment files and local credential directories.
- Logs and Kubernetes diagnostics are bounded; common credential patterns
  are redacted before diagnostics are returned.

These are source/configuration claims with automated test coverage. They do
not prove live Kubernetes authorization or successful least-privilege
deployment for the current candidate.

## Residual risks and limitations

- Current-candidate least-privilege Kubernetes E2E is not verified.
- There is no refresh-token rotation, browser HttpOnly-cookie session, or
  demonstrated CSRF defense.
- Login throttling is in-memory and per API process; it is not distributed.
- Project resource policies are checked at API admission, but Kubernetes
  Namespace, ResourceQuota, and LimitRange reconciliation is not implemented.
- Namespace separation is not a complete hostile-tenant boundary.
- Image provenance/signature validation and container OS vulnerability
  scanning are not complete.
- Audit-event coverage does not include every failure or Worker lifecycle
  transition; database owners can bypass ordinary trigger protections.
- Metrics, alerts, and a production incident-response process are not
  implemented.

## Secret handling

Do not commit `.env`, kubeconfigs, private keys, tokens, database dumps,
backups, runtime logs containing credentials, or screenshots with sensitive
data. `.env.example` contains placeholders only. Keep real credentials in
ignored files or an appropriate secret manager. Do not pass passwords or
tokens as command-line arguments.

The application does not accept arbitrary secret values as deployment
environment variables. Kubernetes Secret references require operator
provisioning and do not protect a secret from the workload that receives it.

## Verification

The latest recorded dependency audits found no known Python vulnerabilities
and zero npm audit findings; see the [test evidence](../reports/MC-FINAL-TEST-EVIDENCE.md).
Those results are dated evidence, not a guarantee against newly disclosed
issues. Source tests cover configuration redaction, authorization, diagnostics
bounds, and RBAC structure. Live Kubernetes authorization and workload
security remain unverified for the current candidate.

See the [current audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md) for scope
and evidence.
