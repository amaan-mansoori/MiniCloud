# Architecture Decision Records

These ADRs capture **proposed** directions for MiniCloud's implementation.
They are not proof that a component exists. Status remains `Proposed` pending
human approval and implementation evidence.

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-0001](ADR-0001-control-plane-architecture.md) | Modular monolith control plane | Proposed |
| [ADR-0002](ADR-0002-kubernetes-integration.md) | Kubernetes Python client behind constrained adapter | Proposed |
| [ADR-0003](ADR-0003-database-and-persistence.md) | PostgreSQL durable metadata and jobs | Proposed |
| [ADR-0004](ADR-0004-authentication-and-authorization.md) | Password/session model and project authorization | Proposed |
| [ADR-0005](ADR-0005-observability-approach.md) | Structured logs, real observations, optional metrics stack | Proposed |

Decision index: [17-decisions.md](../project-docs/17-decisions.md).

## ADR status convention

- **Proposed:** documented choice pending approval/evidence.
- **Accepted:** approved for implementation; does not imply implemented.
- **Deprecated:** no longer preferred but may still exist.
- **Superseded:** replaced by a later ADR.

Keep rationale, security/operational effects, and reconsideration triggers
synchronized with the index and [traceability matrix](../project-docs/TRACEABILITY.md).
