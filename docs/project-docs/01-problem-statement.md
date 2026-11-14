# 01 — Problem Statement

**Status:** Initial product proposal retained for context. Implementation and
current verification status are documented in the
[Resume MVP audit](../../reports/MINICLOUD-RESUME-MVP-AUDIT.md).

## Executive summary

MiniCloud is a proposed educational Kubernetes control plane for developers who
need a guided path from a container image to an observable application. Its
REST API, CLI, and small responsive console will submit bounded application
operations to a control plane that authenticates and authorizes each request,
persists intent in PostgreSQL, executes long-running work asynchronously, and
reports outcomes from observed Kubernetes state.

The problem is not that Kubernetes lacks deployment primitives. The problem is
that a developer new to Kubernetes must coordinate several primitives and
failure signals correctly. MiniCloud intends to simplify a selected workflow
while keeping the underlying infrastructure and failure boundaries visible.
It does not replace Kubernetes, schedule workloads independently, or provide a
complete public cloud.

## Background and context

Kubernetes exposes powerful APIs for workloads, networking, configuration,
resource management, and status observation. That flexibility puts substantial
operational responsibility on the user: construct valid resources, establish
access controls, inspect rollout conditions, find container logs, understand
metrics availability, and recover from partial failure.

**Not verified:** user research, market size, competitor capabilities, and
whether any external developer has this problem in the exact form described.

## Developer problem

For beginners and small teams, a successful `kubectl apply` or accepted API
request does not prove the application is ready. Misconfigured labels,
probes, ports, image references, permissions, or quotas can prevent a rollout.
Logs, Kubernetes events, resource measurements, and rollout state are exposed
through different tools. Rollback requires understanding revision history and
the side effects of applying an earlier desired configuration.

Common pain points to validate with users:

- **Manual operations:** translating application intent into namespace,
  Deployment, Service, configuration, and access-control objects.
- **Configuration errors:** mismatched ports/selectors, invalid probes,
  unavailable images, unsupported fields, and resource values rejected by the
  API server or scheduler.
- **Fragmented diagnostics:** correlating API request, worker attempt, resource
  generation, Pod events, container output, and readiness conditions.
- **Failure ambiguity:** treating request acceptance, resource creation, and
  rollout readiness as one success state.
- **Rollback complexity:** identifying a known-good revision and applying it
  safely while other changes may be in flight.
- **Isolation concerns:** preventing one project member from inspecting or
  mutating another project's records and workloads.

These are design hypotheses, not interview findings.

## Target users and personas

| Persona | Goals | Needs |
| --- | --- | --- |
| Individual developer | Run a containerized side project without learning every low-level operation first | Clear deployment inputs, truthful progress, actionable failure details, logs, and manual scaling |
| Project owner / administrator | Coordinate a small team's applications | Membership roles, project-level isolation, audit history, resource policy, controlled rollback |
| Platform operator | Maintain the control plane and its cluster integration | Least-privilege access, worker recovery, health signals, quotas, reconciliation, safe diagnostics, operational runbooks |

## Current workflow and pain points

An assumed direct workflow is: build/publish an image → choose a cluster and
namespace → write manifests → apply them → wait for Pods → inspect events and
logs → expose a Service/Ingress → monitor → edit manifests to scale or roll
back. Each step can cross a different tool and permission boundary. This
workflow is plausible, but must be validated in user interviews and usability
testing rather than described as universal fact.

## Alternatives and intended educational gap

| Alternative | Strength | Limitation for this project's learning objective |
| --- | --- | --- |
| Direct Kubernetes tools (`kubectl`, manifests, dashboards) | Maximum control and direct understanding of Kubernetes APIs | Requires users to assemble UX, persistence, authorization, asynchronous jobs, and product-level lifecycle tracking themselves |
| Managed container/application platforms | Reduce infrastructure administration and may provide polished workflows | Hide some control-plane implementation and reconciliation decisions that this portfolio project is intended to demonstrate; capabilities and trade-offs differ by provider |
| MiniCloud | A focused, inspectable layer over selected Kubernetes operations | Will not provide the breadth, scale, managed guarantees, or operational maturity of commercial platforms |

No comparative product research or performance measurements have been run.

## Proposed solution and value proposition

Provide one consistent project/deployment model across a versioned REST API,
CLI, and dashboard. Persist accepted intent before background execution.
Constrain users to supported inputs rather than accepting arbitrary manifests.
Expose operation identifiers and states, then report readiness only after
checking actual Kubernetes observations. Keep logs, metrics, and events tied to
project authorization and show unavailable data explicitly.

**Value proposition:** teach and demonstrate how a modest product layer can
make a useful Kubernetes workflow easier without concealing authorization,
partial failure, or actual workload health.

## Project boundaries

**In scope for the proposed MVP:** local educational deployment; accounts,
projects and memberships; constrained image deployment; namespaced Deployment
and ClusterIP Service; readiness status; PostgreSQL-backed operation history;
manual scaling; revision-based rollback; bounded log/event access; API, CLI,
responsive UI; quotas; tests and reproducible local setup.

**Not an MVP promise:** hostile-tenant isolation, managed cloud infrastructure,
high availability, guaranteed uptime, arbitrary Kubernetes object access,
production-grade secret management, public ingress/TLS, autoscaling without a
working metrics provider, full tracing/log analytics, billing, or AI-controlled
operations. See [PRD](02-prd.md) and [security design](11-security.md).

## Assumptions and constraints

- Kubernetes remains the scheduler and source of workload observations.
- PostgreSQL is the control-plane metadata store; neither it nor Kubernetes can
  join one atomic transaction.
- Background work is required because a rollout outlives a normal API request.
- Initial implementation should remain practical for Windows + Docker + kind.
- Runtime prerequisites and Kubernetes context are environment-specific and
  must be rechecked before any operational procedure.
- This proposal does not assert cloud accounts, an external identity provider,
  registry credentials, user research, or a production deployment target.

## Risks

| Risk | Effect | Proposed response |
| --- | --- | --- |
| Namespace isolation is mistaken for a hostile-tenant boundary | Cluster or neighbor workload compromise | State the educational threat model; restrict workload features and Kubernetes permissions; isolate untrusted users outside MVP |
| PostgreSQL and cluster diverge | Stale or misleading status | Persist intent, reconcile by stable identity/generation, expose uncertainty, and test recovery |
| Local Windows resources are constrained | Slow builds or cluster instability | Keep dependencies optional and measure actual resource usage |
| Scope exceeds one developer's capacity | Incomplete or untested features | Freeze a small MVP, use explicit MUST/SHOULD/COULD priorities, defer extras |
| Unsupported image/configuration behavior is unclear | User-facing failures | Define validation, policy, errors, and acceptance before implementation |
| Product assumptions are unvalidated | Wrong UI/workflow priorities | Conduct user interviews and usability sessions; record findings separately |

## Expected outcomes and measurable indicators

These are proposed evaluation measures, not results or guarantees:

- A clean local setup can be repeated from documented prerequisites.
- Every MUST requirement has a test or concrete inspection procedure.
- A request is never represented as ready until observed rollout readiness is
  true; test this with success and deliberate failure paths.
- Cross-project read and mutation attempts are denied in API and integration
  tests.
- Worker interruption and duplicate requests do not create conflicting active
  revisions in controlled recovery tests.
- Operators and developers can identify the relevant failure cause from
  authorized status, events, and available logs.

Numeric latency/readiness targets belong in the evaluation plan only after
hardware and workload baselines are established.

## Portfolio significance

The project can demonstrate API design, relational modeling, distributed
failure handling, idempotency, Kubernetes integration, authorization,
observability, testing against a real local cluster, accessible UI design, CI,
and transparent engineering trade-offs. The portfolio value depends on
implemented evidence; documentation alone is not evidence that the features
work.
