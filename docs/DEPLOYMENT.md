# Deployment and Release

## Local API and Web profile

Use the isolated `docker-compose.demo.yml` described in [setup](SETUP.md) for
API/Web exploration. It uses ephemeral PostgreSQL storage and a loopback-only
gateway. No Worker or Kubernetes cluster is started, so deployment jobs
remain queued. Runtime startup has not been verified in the latest
assessment.

The main Compose file includes PostgreSQL, API, Worker, and Web services.
Review its environment variables, storage, network, and Kubernetes credential
configuration before using it. Do not start it against data or services that
must be preserved.

## Database migrations

Alembic migrations run with `SERVICE_ROLE=migrate` and a dedicated
`MINICLOUD_MIGRATION_DATABASE_URL`; API and Worker runtime roles are separate.
The API does not migrate the database at startup. Run migrations only against
a disposable database or under an approved maintenance plan with verified
backup and rollback procedures.

Migrations that remove audit or revision history are destructive to that
history. Review migration downgrade behavior and test it using disposable
data before any persistent database change.

## Kubernetes prerequisites

Before running a Worker against Kubernetes, provision an isolated namespace
and dedicated identity, then verify the namespace Role, ServiceAccount,
RoleBinding, and positive and negative `kubectl auth can-i` checks. The
manifest at `infra/kubernetes/namespace-rbac.yaml` is proposed configuration,
not evidence of live permissions.

Historical application evidence used administrator credentials and does not
prove least privilege. Current-candidate Ready status, scaling, rollback, and
diagnostics have not been verified with a dedicated Worker identity. See the
[Kubernetes guide](KUBERNETES.md) and [current audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md).

## Release status

The project is **NOT COMPLETE** and is not claimed to be production-ready.
The API persists deployment requests and returns HTTP 202 for accepted work;
that response is not a readiness signal. Scaling uses an expected-replica
precondition, and rollback creates a new revision with an expected-revision
precondition. Live Kubernetes convergence remains unverified.
