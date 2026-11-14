# ADR-0003: PostgreSQL for Durable Control-Plane State

- **Status:** Proposed
- **Date:** 2026-10-11

## Context

MiniCloud must durably record users, project ownership, desired revisions,
idempotency keys, job leases, audits, and observed status. Updates need
relational constraints and transactional acceptance.

## Decision drivers

- Atomic commit of DB-side intent, job, idempotency record, and audit.
- Foreign-key and unique constraints for ownership and duplicate protection.
- Suitable local Docker development.
- Clear database/Kubernetes partial-failure model.

## Options considered

1. PostgreSQL with SQLAlchemy/Alembic.
2. SQLite as primary store.
3. Document database.
4. PostgreSQL plus external queue immediately.

## Decision

Propose PostgreSQL as control-plane metadata store, SQLAlchemy 2 typed mappings,
and Alembic reviewed migrations. Persist durable job rows in PostgreSQL for MVP.
Do not store raw passwords, tokens, workload secrets, or unbounded logs.

## Positive consequences

- Transactions, constraints, indexes, and concurrency primitives align with
  ownership/revisions/jobs.
- One state store supports local reproducible setup and audit/history.
- Avoid introducing a broker without measured need.

## Negative consequences

- Backup, migration, connection-pool, upgrade, and recovery work is required.
- Queue polling can add DB load and share failure domain with API metadata.
- PostgreSQL and Kube state still cannot share atomic transaction.

## Security and operational implications

Least-privileged DB account, parameterized queries, network isolation,
redacted logs, encrypted/protected backups, tested restore, retention policy.
Commit desired intent before Kube call; reconcile partial apply using stable
identity/observed generation. Never hold transaction open during network call.

## Reconsideration triggers

Measured queue throughput/latency exceeds safe DB capacity, operational
deployment requires managed external queue, or data/query patterns change with
evidence. Any broker addition requires cost/benefit ADR.

## Related requirements and documents

FR-004/009/019; NFR-003..005/009; SEC-001/003/008; OPS-005;
[database](../project-docs/07-database-design.md), [architecture](../project-docs/06-architecture.md),
DEC-003/006/007.
