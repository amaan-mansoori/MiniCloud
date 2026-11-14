# ADR-0005: Real, Explicit, and Optional Observability

- **Status:** Proposed
- **Date:** 2026-10-11

## Context

Users need trustworthy deployment status, logs, events, metrics, and alerts.
Local environment is resource-constrained; Docker engine previously reported
about 3.54 GiB and host physical memory about 7.42 GiB. Optional sources can be
absent or stale.

## Decision drivers

- Operational claims must come from real source data.
- Surface source timestamp and uncertainty.
- Keep core deploy path possible without full monitoring stack.
- Avoid high local resource overhead until measured.

## Options considered

1. Structured API/worker logs, correlation IDs, persisted lifecycle events,
   bounded live Kube logs/events; optional Prometheus/Grafana.
2. Prometheus, Grafana, and Loki mandatory for all dev environments.
3. Hardcoded/demo metrics to ensure dashboard always has charts.
4. No operational signal beyond success/failure response.

## Decision

Propose option 1. Core logs/status must use actual API/worker/Kubernetes data.
Metrics capability is optional and must show unavailable/stale explicitly.
Prometheus/Grafana may be an opt-in profile after resource measurement; Loki
is deferred until centralized retention/search is justified. Alerts remain
SHOULD until evaluator and delivery are implemented and tested.

## Positive consequences

- Honest readiness and metrics semantics.
- Low default dependency footprint.
- Incremental path to operational dashboards.

## Negative consequences

- Live Kubernetes logs/events may expire and are less searchable.
- Without provider, utilization/autoscaling UI remains unavailable.
- Correlation/redaction and source freshness require deliberate implementation.

## Security and operational implications

Project authorization before log/metric queries; bound responses; redact tokens
and secret markers; avoid high-cardinality tenant IDs in metric labels. A
monitoring outage cannot yield false zero or ready. Alert delivery requires
dedupe, retries, access-controlled configuration, and verified test channel.

## Reconsideration triggers

Demonstrated need for long-term log retention, cross-service diagnosis, or
alerts; validated local memory/storage budget; actual metrics provider and
access controls available.

## Related requirements and documents

FR-012/013/017/020; NFR-002/006; SEC-005/009; OPS-001/002;
[observability](../project-docs/15-observability.md), [evaluation](../project-docs/14-evaluation.md),
DEC-005/010.
