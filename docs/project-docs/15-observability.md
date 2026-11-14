# 15 — Observability Specification

**Status:** Proposed telemetry model. No metric, alert, dashboard, or notification
is operational yet.

## Signals and correlation

API and worker emit structured JSON logs with UTC timestamp, severity,
service/component, environment, correlation/request ID, project/deployment/
revision/job IDs when authorized and non-sensitive, attempt, event code, and
duration. Never log passwords, access/refresh tokens, cookies, authorization
headers, raw secret values, full environment, or unbounded user log content.

Generate or validate correlation IDs at the API boundary; propagate to job
record and worker logs. Use operation/revision IDs to connect API acceptance,
DB writes, worker attempt, Kubernetes object UID/generation, and observations.
IDs aid correlation; they do not authorize access.

## Desired, observed, and derived state

- **Desired:** immutable revision config committed in PostgreSQL.
- **Observed:** Kubernetes object UID, metadata generation/observed generation,
  replica counts, conditions, Pod state, timestamps, and source.
- **Derived:** operation ready/failed/progressing conclusion based on desired
  generation and observations.

Expose source timestamp and stale flag. Missing observation is `unknown` or
`stale`, not zero/ready. Kubernetes events and Pod logs are best-effort sources
with finite retention; unavailable sources are explicit.

## Proposed signals

| Signal | Type/source | Labels and safeguards |
| --- | --- | --- |
| HTTP request count/duration | API middleware | method, route template, status class; no user/project ID labels |
| Auth denials/rate limits | API security events | reason category only; avoid email/token labels |
| Job queue depth/age/attempts | PostgreSQL worker queries | state, worker pool; avoid job ID as metric label |
| Deployment operation duration/outcome | DB lifecycle timestamps | outcome/reason bounded enum; operation details in logs |
| Kubernetes call latency/errors | adapter | operation/resource kind/result; no object secrets |
| Resource CPU/memory | real metrics provider or Metrics API | timestamp/unit/source; project access controlled at query layer |
| DB pool/health | DB client | aggregate counters/gauges |
| Stale observations | reconciler | count/age by bounded reason |

## Missing and degraded-source behavior

- **No metrics provider:** return `metrics_unavailable` with capability state;
  render no numeric value and disable HPA claims.
- **Stale samples:** show source sample time and stale label; do not extend last
  value as though current.
- **Unavailable Pod logs:** show `LOG_SOURCE_UNAVAILABLE`/missing Pod source
  and correlation ID, not empty “no logs.”
- **Kubernetes event retention gap:** indicate events may have expired; use
  durable MiniCloud lifecycle events if available.
- **Monitoring outage:** application lifecycle continues where safe; readiness
  observer absence prevents ready transition; alert/telemetry outage is itself
  an operator-visible condition if independent health path exists.

## Alert proposals (SHOULD)

These conditions are hypotheses; evaluator, delivery, and tests must exist
before calling alerts operational.

| Alert | Proposed condition/window | Severity | Recovery/dedup | Runbook |
| --- | --- | --- | --- | --- |
| Rollout stalled | nonterminal operation exceeds rollout deadline (provisional 10 min) | Warning | One incident per operation; resolve on terminal state | inspect operation, events, quota, image, API reachability |
| Worker queue aged | oldest queued job > 2 min for 5 min | Warning | resolve when queue age below threshold for 5 min | worker health, DB connections, Kube retries |
| Observation stale | required workload observation older than 2 min | Warning | recovery after fresh observation; dedupe project/deployment | Kube API/auth/network, reconciler |
| API error elevated | 5xx >5% of requests for 5 min with minimum request count | Critical (local proposed) | recovery for 10 min under threshold | inspect recent deploy/dependencies, correlation IDs |
| Quota exhausted | explicit quota rejection or near-limit only if real usage source available | Warning | per project, resolve below threshold with fresh source | right-size or owner approval |
| Metrics source unavailable | provider health failed 3 consecutive checks | Info/Warning | recover on successful checks; no usage threshold alerts while absent | install/configure/check provider |

Windows/thresholds above are provisional; false positives, sample counts,
delivery channel, suppression, timezone and on-call expectations are open.
Alert delivery SHOULD support retries with dedupe and persisted delivery
outcomes. No email/webhook is configured in Phase 1.

## Prometheus, Grafana, Loki evaluation

Prometheus provides a time-series model and pull-based scraping; useful for
real API/worker and resource metrics, but adds storage/retention and local
memory. Grafana can query and visualize metrics/logs and evaluate alert rules;
its presence does not make a source available or alert delivered. Consider
optional Compose profile after resource-budget measurement. `metrics-server`
serves Kubernetes resource metrics/HPA use; it is not the same as Prometheus
application telemetry.

Loki is deferred: MVP retrieves bounded live Pod logs and Kubernetes events
through a dedicated read-only API identity when configured.
Central indexing introduces retention, access control, storage, and additional
service cost. Reconsider if log search/history becomes a measured need.

## Access, retention, dashboards, runbooks

Only project members can request workload logs/metrics/events. Operator
telemetry can include infrastructure but should avoid tenant content; internal
logs restricted by operator access. Proposed local operational log retention
7 days and audit 90 days are provisional/open; configure actual retention only
after storage/privacy review. Dashboard panels must display source, unit,
sample time, and unavailable state; no decorative gauges or unbacked totals.

Runbooks cover API not ready, PostgreSQL down/migration failed, worker queue
stalled, Kube API inaccessible, ImagePullBackOff, quota denial, failed
readiness, stale metrics, and log source missing. Each runbook distinguishes
safe read-only diagnosis from privileged/destructive action.
