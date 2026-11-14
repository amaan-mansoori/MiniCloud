# Worker Operations

The worker is a separate Python process that polls PostgreSQL for queued,
applying, progressing, or timed-out jobs. It claims a bounded batch with
`FOR UPDATE SKIP LOCKED`, a finite lease, and deterministic Kubernetes resource
names. It reconciles Deployment and ClusterIP Service state and observes the
Deployment generation, ready replicas, Pod scheduling, and supported
container failure reasons before persisting a result.

Transient Kubernetes failures are retried with bounded attempts and backoff.
Kubernetes authentication failures pause affected work without consuming the
workload retry budget and mark an optional status file for container health
reporting. Database connectivity failures keep the process alive with bounded
jittered exponential backoff. SIGTERM/SIGINT request graceful loop shutdown.

New deployment and scale jobs persist the API-generated request ID. The
Worker copies it to lifecycle events and includes it in job-specific log
messages so an accepted request can be correlated with reconciliation.
Pre-existing jobs may have no request ID; those logs explicitly use
`not_recorded`. Request IDs are not attached to Kubernetes resources.

## Credentials and safety

Local mode requires both an explicit kubeconfig file and explicit context;
MiniCloud does not select or mutate the current kubectl context. In-cluster
mode uses the worker ServiceAccount. Never mount an administrator kubeconfig
or production credential into a test Worker.

## Verification state

Worker unit tests and adapter tests exist. Historical tests exercised job
processing and explicit configuration-failure reporting against a disposable
database. They do not validate a successful Kubernetes operation or a
least-privilege identity. No claim is made for current-candidate Worker
authorization or full E2E acceptance; see the [current audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md).

For current polling/configuration details, see
[Kubernetes](KUBERNETES.md) and [troubleshooting](TROUBLESHOOTING.md).
