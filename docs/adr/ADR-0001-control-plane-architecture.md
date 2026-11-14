# ADR-0001: Modular Monolith Control Plane

- **Status:** Proposed
- **Date:** 2026-10-11

## Context

MiniCloud needs a REST API, project/deployment domain rules, asynchronous work,
Kubernetes integration, and clients. The workspace is greenfield; there is no
measured traffic, team topology, or independent component scaling requirement.

## Decision drivers

- Keep local Windows + Docker + kind development manageable.
- Share auth, validation, transaction, and domain logic between API and worker.
- Make retries, DB transactions, and component failures inspectable.
- Avoid unsupported claims of scalability or availability.

## Options considered

1. **Modular monolith:** one codebase with explicit domain modules; API and
   worker may run as separate processes.
2. **Microservices:** separate API, identity, deployment, observer, and alert
   services with network contracts.
3. **Single synchronous API process:** simpler deployment but no durable
   long-running work after process restart.

## Decision

Propose modular monolith boundaries: auth, project/policy, deployment,
repository/persistence, job worker, Kubernetes adapter, observability. Keep
API request handlers short and move rollout execution to durable worker. Run
process roles separately if resource isolation needs it, without introducing
network microservices.

## Positive consequences

- Shared typed domain and authorization logic.
- One release and local setup path.
- Fewer network dependencies and distributed transactions.
- Clear seam for later extraction.

## Negative consequences

- Shared codebase/release cadence and database can couple modules.
- Worker and API contend for local resources.
- Module boundaries require discipline and tests.

## Security and operational implications

Single authorization service helps consistent server-side checks; process
separation can reduce worker exposure only if credentials and network access
are separately configured. Monitor DB pool/queue resource limits. No HA or
independent scaling claim follows from this design.

## Reconsideration triggers

Measured independent scaling need, materially different reliability boundary,
team ownership conflict, or DB/job throughput ceiling demonstrated by evaluation.

## Related requirements and documents

NFR-003/004/006/008, OPS-001/003; [architecture](../project-docs/06-architecture.md),
[tech stack](../project-docs/05-tech-stack.md), DEC-001/006/007.
