# ADR-0004: Project-Scoped Authentication and Authorization

- **Status:** Proposed
- **Date:** 2026-10-11

## Context

Accounts use web, CLI, and REST surfaces. Every protected action must be
authorized server-side, project boundaries enforced, and ordinary users kept
away from Kubernetes credentials.

## Decision drivers

- Revocable account sessions and safe browser/CLI credential handling.
- Explicit project owner/member permissions.
- Failure closed when identity or membership data is unavailable.
- Avoid unnecessary external identity provider for educational MVP.

## Options considered

1. Email/password + Argon2id, short-lived access and hashed rotating refresh
   session.
2. External OIDC provider only.
3. Long-lived static API tokens.
4. Kubernetes user credentials forwarded to client.

## Decision

Propose option 1 for MVP: maintainable Argon2id implementation; generic login
failure and rate limiting; short-lived access credential; opaque random refresh
token stored as a hash and rotated, with replay revoking its family. Browser
refresh uses Secure/HttpOnly/SameSite cookie and CSRF/origin protection; CLI
stores it in OS credential storage when available. Every API request resolves
current project membership. Roles are owner/member with default deny. Exact
token encoding, expiry, library, and registration policy remain open pending
security review. No user credential maps directly to Kubernetes RBAC.

## Positive consequences

- Self-contained educational authentication and revocation path.
- Clear project-level authorization model shared by clients.
- No external IdP required for local MVP.

## Negative consequences

- Password reset, email verification, session rotation/reuse, CSRF, and
  credential-store edge cases add engineering burden.
- CLI OS keychain support differs by platform.
- External identity may be more appropriate for real organizations.

## Security and operational implications

Store only password hashes and refresh-token hashes. Do not log credentials.
Enforce membership before resource lookup; never rely on UI hiding. Audit
membership and privileged changes. Session DB outage fails closed. Separate
application role from cluster service-account RBAC.

## Reconsideration triggers

Validated SSO requirement, organizational deployment, security review finding
that custom identity is inappropriate, or cross-platform CLI credential storage
cannot meet acceptable protection.

## Related requirements and documents

FR-001/003; SEC-001..003/007/008; [security](../project-docs/11-security.md),
[API](../project-docs/08-api-spec.md), [database](../project-docs/07-database-design.md),
DEC-004.
