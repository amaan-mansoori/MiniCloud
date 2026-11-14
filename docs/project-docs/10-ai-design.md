# 10 — AI/ML Specification

**Status:** AI is excluded from the proposed MVP. No model, API, dataset,
integration, evaluation, or result exists.

## Is AI necessary?

No. Core MiniCloud value depends on deterministic authorization, resource
validation, idempotent reconciliation, actual Kubernetes state, and reliable
failure reporting. An LLM does not solve the hard correctness boundaries and
could introduce ungrounded explanations or unsafe actions. AI is not a core
dependency, product claim, or release condition.

## Potential future advisory use cases

- Summarize a failed rollout using authorized structured reason codes and
  selected sanitized events.
- Suggest documentation/runbook steps for known `ImagePullBackOff`,
  scheduling, or probe failure categories.
- Summarize deployment history for a project member.

Any future assistant is advisory-only: it cannot create/update/rollback/scale
resources, authorize users, reveal secrets, execute shell commands, or change
Kubernetes state. A user must initiate any operation through normal validated
API authorization and confirmation. A separate threat model, privacy review,
security review, and ADR are required first.

## Data, privacy, and access control

Potential inputs include operation state/reason codes, bounded Kubernetes
events, redacted logs, resource metadata, and runbook text. These can contain
customer data, internal hostnames, tokens accidentally printed by workloads,
and attacker-controlled text. Only data the requesting actor is already
authorized to view may be supplied. Redaction/minimization occurs before
external processing. Do not send raw secrets, full environment, credentials,
or cross-project context. Retention and provider training/use policies must be
reviewed before choosing any model.

## Threats and controls

- **Prompt injection:** events/logs/image metadata may contain instructions;
  treat as untrusted data, delimit/label evidence, never grant tool access,
  prohibit following embedded commands.
- **Hallucination:** require citations to source event IDs/timestamps; label
  generated content as suggestion; display source evidence alongside it.
- **Incorrect action:** no autonomous execution; deterministic API remains only
  route to mutation and applies existing authorization/limits.
- **Data leakage:** strict project authorization, minimal input, provider
  contract/data retention review, opt-in switch, audit access.
- **Availability/cost:** time/token limits, per-project quotas, circuit breaker;
  core workflow unaffected if model is off.

## Model choice, evaluation, and success criteria if reconsidered

Compare model/provider data handling, region, retention/training terms,
security controls, latency, cost, structured output support, and license.
Evaluate a fixed reviewed dataset of representative success/failure cases with
expert-labeled supported causes. Measure grounded claim precision, unsafe
recommendation rate, unsupported-claim rate, latency p50/p95, token cost per
summary, and refusal behavior. Publish dataset composition and limitations;
never present fabricated benchmark values. Proposed release gates: no
high-severity unsafe action recommendation in the test set, traceable evidence
for every operational claim, and explicit operator/user approval for each
action (which remains deterministic).

Fallback is a normal rule-based reason/event display; if model times out or is
disabled, users retain all core capability. Retention defaults to no model
prompt persistence beyond necessary audit metadata unless separately approved.
