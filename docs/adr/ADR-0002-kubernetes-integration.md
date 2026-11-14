# ADR-0002: Constrained Kubernetes Client Adapter

- **Status:** Proposed
- **Date:** 2026-10-11

## Context

The control plane must apply supported Deployment/Service resources and observe
rollouts, events, and bounded logs. Users must not receive unrestricted cluster
access. Local kind and Kubernetes client compatibility require explicit testing.

## Decision drivers

- Never implement a competing scheduler.
- Keep Kube API calls constrained, typed, authorized, retry-aware, and testable.
- Avoid shell-output parsing and arbitrary manifest forwarding.
- Minimize control-plane service-account permissions.

## Options considered

1. Kubernetes Python client behind an allowlisted adapter.
2. Shell out to `kubectl` and parse output.
3. Custom controller/operator CRD.
4. Give each user kubeconfig/direct cluster access.

## Decision

Propose official Kubernetes Python client operations encapsulated behind an
adapter with typed resource construction and narrow methods. Do not shell out
from request paths; `kubectl` remains a human diagnostic tool. Do not create a
custom scheduler/controller in MVP; consider a controller only if reconciliation
ownership complexity is demonstrated. Users never receive cluster credentials.

## Positive consequences

- Structured API responses/resource versions and testable boundaries.
- Server enforces project permission and workload allowlist.
- Kubernetes does scheduling and Deployment rollout.

## Negative consequences

- Client package/API version compatibility needs maintenance.
- Adapter needs explicit error, timeout, watch/poll, and retry logic.
- Namespace provisioning may require cluster-scoped permission and separate
  operator path.

## Security and operational implications

Use dedicated service account; namespace-scoped Role where feasible; explicitly
review any cluster-scoped create namespace permission. Reject privileged,
hostPath, host networking and arbitrary ServiceAccount inputs. Check namespace
labels/UID before adopting resources. Namespace is not complete hostile-tenant
isolation. Never grant cluster-admin as a workaround.

## Reconsideration triggers

Required API feature absent from maintained client, measured need for a
controller reconciliation loop, or stronger tenant boundary requiring admission,
network policy, dedicated clusters, or operator-managed provisioning.

## Related requirements and documents

FR-006..008/012/013; SEC-003/004/006; NFR-002/004; [architecture](../project-docs/06-architecture.md),
[security](../project-docs/11-security.md), DEC-002/008.
