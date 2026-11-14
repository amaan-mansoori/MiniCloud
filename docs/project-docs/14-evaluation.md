# 14 — Evaluation Plan

**Status:** Measurement plan only. No benchmark or user study has been run.
All thresholds below are proposed/provisional and must be reviewed against
actual host, kind resources, and expected workload.

## Evaluation dimensions and procedures

| Metric | Definition / calculation | Collection and environment | Proposed threshold / limitations |
| --- | --- | --- | --- |
| Functional completeness | MUST acceptance cases passing / MUST cases executed; report numerator and denominator | Requirement-linked CI and kind run | Target 100% MUST release cases; test coverage is not product availability |
| Deployment outcome | Ready within deadline / valid deployment attempts; separately count failure class | disposable kind, image pinned by digest, repeated attempts | Establish baseline; do not compare unlike images or host states |
| Time to readiness | accepted timestamp to first verified observed ready generation; report p50/p95 and censored failures | operation DB + Kube observation clock | Provisional p95 ≤ 180s for local small image and warm cluster; exclude image pull setup and publish it |
| API latency | response duration by method/status, p50/p95; mutations measured only until durable acceptance | 5 concurrent clients proposed, warmup, ≥100 requests/route where practical | Provisional read p95 ≤500ms excluding Kube/log routes; not production SLA |
| Error rate | failed requests / requests by class, exclude intentional negative tests separately | structured API metrics/test harness | Report classes and denominator; no single aggregate concealing expected 4xx |
| Worker recovery | crash-to-reclaimed plus operation-completion time; duplicate object/job count | kill disposable worker process after claim/apply boundary | One effective revision/object, no false-ready; environment-dependent time |
| Retry correctness | identical accepted outcomes for transient fault retries; compare job/revision IDs | deterministic fault injection in kind | One logical operation per idempotency key; faults represent only injected cases |
| Authorization | unauthorized access attempts denied / attempts across endpoint-role matrix | API integration, two projects/users | 100% denial expected for defined matrix; does not prove absence of unknown paths |
| Resource enforcement | requests rejected at policy boundary and observed quota behavior | PostgreSQL + kind ResourceQuota | No deployment above enforced cap; cluster/admission config documented |
| Monitoring completeness | supported signals with source, timestamp, freshness, unavailable semantics | fault/provider-present + absent environment | Each signal either actual/attributed or explicit unavailable; not imply total coverage |
| Usability | task success/time/errors and participant feedback | small documented formative session, consent | Establish baseline; sample too small for population inference |
| Accessibility | unresolved automated findings + manual task checklist | automated scan plus keyboard/screen-reader spot check | Proposed no critical/serious blockers for core flow; not formal certification |
| Reproducibility | clean setup attempts reaching same documented checkpoint | separate clean environment/CI | Target 2 independent successful repetitions before publishable walkthrough |
| Maintainability | traceability, migration quality, CI, code-review findings | review checklist | Qualitative; no invented maintainability score |
| Local resource use | peak/idle CPU, host and Docker memory, disk/image growth | same host, measured by system/Docker tools | Proposed base profile ≤3.0GiB Docker-engine memory is provisional against the observed ~3.54GiB engine limit; optional observability stays off; report if infeasible |

## Benchmark procedure

1. Record code revision, OS, Docker Desktop engine, kind/Kubernetes, Python,
   Node, PostgreSQL, image digest, cluster config, and host free memory/disk.
2. Use disposable isolated PostgreSQL database and approved kind context.
3. Warm services/cluster explicitly; retain cold-start observation separately.
4. Run a fixed small image and request mix, at least 20 deploy attempts for
   readiness distribution and at least 100 requests per read route where
   resources permit; report actual sample counts, failures, and confidence/
   variability rather than hiding outliers.
5. Measure accepted latency separately from rollout-ready latency. Track start
   and end timestamps from same clock source.
6. Run injected Kube outage, worker crash, DB unavailable, duplicate request,
   and stale ETag scenarios.
7. Repeat runs at least three times if local capacity permits; report each run
   and median rather than pooled unexplained value.
8. Preserve raw sanitized result files and exact command. No destructive
   cleanup outside labeled test resources.

Targets are hypotheses: change them only with an ADR or explicit evaluation
note and never retroactively imply previously measured success.

## Interpretation limits

Kind adds container/virtualization overhead and lacks production cluster
topology/traffic. A passing integration test measures a scenario, not uptime.
Synthetic HTTP load does not represent real users; small samples have wide
uncertainty. Image pull/cold-cache behavior dominates rollout time and must be
reported. Host resource constraints vary. Test success rate is not real-world
availability; a test metrics sample is not production telemetry.

## Failure-analysis template

```text
Incident/test ID:
Date, revision, environment:
Symptom and user-visible impact:
Reproduction steps and exact command:
Expected vs actual:
Evidence (correlation ID, operation ID, sanitized logs/events):
Timeline:
Root cause (confirmed / hypothesis):
Fix and affected components:
Regression test ID and actual result:
Data loss/duplication/security impact:
Lessons and follow-up owner:
```

## Publication gate

Only publish benchmark numbers with raw procedure, environment, sample count,
date, and limitations. No result is available at documentation phase.
