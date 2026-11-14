# 16 — Future Root README Outline

**Status:** Content plan only. Do not publish claims until each gate below has
evidence. The root README is intentionally not created in Phase 1.

1. **Project introduction:** MiniCloud is an educational Kubernetes control
   plane that simplifies selected operations; not a public cloud.
2. **Problem solved:** link [problem statement](01-problem-statement.md).
3. **Features:** separate implemented, planned, and unavailable; never describe
   planned requirements as product functionality.
4. **Architecture:** include approved Mermaid/system diagram; link
   [architecture](06-architecture.md).
5. **Technology stack:** actual lockfile versions only; link
   [tech-stack rationale](05-tech-stack.md).
6. **Screenshots:** placeholders until generated from actual implementation;
   label any fixture/demo mode.
7. **Quick start / prerequisites:** verified OS/tool versions, Docker engine,
   PostgreSQL, kind setup; exact copy-paste commands tested on clean setup.
8. **Configuration:** list variable names and safe placeholders; no secrets.
9. **Local Kubernetes:** explicit context selection, namespace policy,
   image-load/pull guidance; no automatic context mutation.
10. **Deployment walkthrough:** actual request, 202 response, operation polling,
    readiness verification, failure walkthrough.
11. **API docs:** actual generated OpenAPI URL and error model.
12. **CLI:** installed version and supported commands demonstrated.
13. **Testing:** actual command/output/date, pass/fail counts and skipped
    reasons; never a fake badge.
14. **Observability:** describe metrics/log sources and unavailable behavior;
    do not claim alerts without tested delivery.
15. **Security model:** threat-model summary and local limitations; no
    “secure-by-default” claim without review.
16. **Performance evaluation:** raw reproducible procedure/results and
    limitations; omit section's numeric claims until measured.
17. **Limitations and roadmap:** namespace caveat, no production HA/SLA,
    current known gaps.
18. **Troubleshooting:** Docker, context, image pull, quota, readiness, DB,
    metrics provider.
19. **Contributing:** setup, tests, ADR/log conventions, security reporting.
20. **License and attribution:** only after repository owner selects a license;
    record component/asset notices and dependency licenses.

## Publication evidence gate

Quick-start commands require actual run logs from a clean environment.
Screenshots must come from real UI with fixture labels if applicable.
Deployment success claims require observed Kubernetes readiness; API acceptance
alone is not success. Test badges must point to a real configured workflow.
Benchmark numbers require [evaluation procedure](14-evaluation.md).
Production-readiness claims require separate security, reliability, and
operations evidence and are explicitly not implied by kind.
