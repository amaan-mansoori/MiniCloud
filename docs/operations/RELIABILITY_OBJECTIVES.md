# MiniCloud Reliability Objectives

These are proposed objectives based on measured local evidence, not production
guarantees.

## Proposed objectives

| Objective | Proposed target | Evidence / qualification |
|---|---:|---|
| RPO | 24 hours | Current backups are operator-initiated; no schedule or off-machine copy is configured, so this is a policy target, not achieved evidence |
| Isolated database restore RTO | 15 minutes | One isolated restore completed in 11.74 seconds with a local archive; excludes archive transfer, service restart, migrations, DNS, and validation |
| Full service recovery RTO | Not established; plan for 60 minutes | No approved full outage drill has been run against the live stack |
| Worker transient recovery | Under one worker poll interval plus retry delay | Code uses a 10-second interval and bounded exponential database retry; no live outage injection was performed |
| Kubernetes rollout | Up to configured 600-second timeout | Disposable Kubernetes readiness test passed; image pull and cluster failure timings remain environment-dependent |
| API readiness | HTTP 200 while database is healthy | Current live `/readyz` returned `{"status":"ready","database":true}` |

## Recovery procedures

1. Confirm API readiness, PostgreSQL health, worker health markers, and
   Kubernetes context without changing context.
2. Preserve the live volume and take a validated logical backup to a dedicated
   external path.
3. For database recovery, restore only into a separately named isolated
   PostgreSQL instance first; verify Alembic revision, counts, constraints, and
   application connectivity.
4. Execute any live cutover only under a separate approval and maintenance
   procedure.
5. For worker recovery, inspect authentication and database markers, lease
   state, and sanitized logs; do not replace credentials or RBAC without
   explicit approval.
6. For Kubernetes recovery, reconcile only resources owned by matching
   MiniCloud labels and preserve the protected workload.

## Measured failure behavior

- Worker lease fencing and retry tests passed in the backend suite.
- Disposable PostgreSQL privilege/concurrency tests passed.
- Disposable Kubernetes readiness passed.
- Backup corruption and retention fixtures passed.
- The approved live disposable deployment reached API `ready`, worker job
  `completed`, and Kubernetes Ready 1/1 before exact cleanup.

## Unmeasured or blocked

- Off-machine backup transfer and restore.
- Scheduled backup freshness and achieved RPO.
- Full API/Web/PostgreSQL/Worker/Kubernetes outage recovery.
- Cross-host recovery, DNS/load-balancer behavior, and capacity limits.
- Remote CI and production-like traffic performance.

Do not advertise these objectives as cloud-provider production readiness.
