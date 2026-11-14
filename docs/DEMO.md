# MiniCloud Portfolio Demo

## Prerequisites and safe local profile

The only currently documented local startup is the disposable API/Web
Compose profile in [SETUP.md](SETUP.md). It requires Windows PowerShell,
Docker Desktop with Linux containers, Docker Compose v2, and free loopback
ports `13018` and `18017`. Check Docker and available disk/RAM first; do not
change Docker settings, stop unrelated containers, or use the default Compose
stack. The default stack is not a test environment and references the protected
PostgreSQL volume.

Read-only preflight commands (do not proceed if Docker cannot reach its Linux
engine, a required port is occupied, or available resources are insufficient):

```powershell
docker version
docker info --format 'CPUs={{.NCPU}} RAMBytes={{.MemTotal}}'
docker ps --format '{{.Names}} {{.Status}}'
docker network ls
Get-PSDrive C | Select-Object Name,Free
```

Inspect any port conflict and existing resources without stopping or removing
them. If Docker cannot be reached, stop the runtime check and do not attempt
cleanup.

The safe profile uses tmpfs PostgreSQL, no named volume, unique
`minicloud-resume-*` image tags, and no Worker. Its Compose configuration has
been validated, but its runtime startup and browser flow have **not** been
verified in the current environment because the Docker Linux engine is
unavailable. It can exercise API/Web request acceptance only; it cannot
produce a Kubernetes deployment or Ready Pod.

After Docker availability and a fresh safety preflight, the documented
commands are:

```powershell
$env:MINICLOUD_DEMO_JWT_SECRET = (& python -c "import secrets; print(secrets.token_hex(32))")
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml --profile migration run --build --rm migrate
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml up --build -d api web gateway
```

Open `http://127.0.0.1:13018` and use only a disposable account and data. A
deployment accepted in this profile remains queued. It is not a complete
deployment demo and must not be recorded as one. The profile's shutdown
commands are:

```powershell
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml down
Remove-Item Env:\MINICLOUD_DEMO_JWT_SECRET
```

These are instructions, not a claim that runtime startup/shutdown passed. The
Compose file has no bind mounts or named volumes; do not add `--volumes`.

## What is verified now

- Backend/API/Worker/CLI automated tests passed locally; see
  [TESTING.md](TESTING.md) and the [audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md).
- One Playwright dashboard test passed against a **mocked API**. It is not
  evidence of a backend, Worker, or Kubernetes workflow.
- Compose files parsed successfully; no services were started.
- No current-candidate Ready deployment, scale, rollback, diagnostics query,
  or genuine screenshot has been verified.

## Recording status

This is a recording script and screenshot checklist, not evidence that a
successful deployment is currently available. No screenshots or video have
been captured. Do not show a fabricated Ready status or describe a queued job
as deployed. Record this walkthrough only after the approved isolated cluster
run succeeds with the exact current API and Worker candidates.

## 2–4 minute walkthrough

| Time | Show and say |
|---|---|
| 0:00–0:15 | Introduce the problem and MiniCloud as a personal portfolio project; clarify that it is not production-ready. |
| 0:15–0:45 | Explain the architecture: Next.js dashboard, FastAPI control plane, PostgreSQL durable job store, Worker, and Kubernetes API. |
| 0:45–1:15 | Sign in with a disposable test account, show the dashboard, and create a project through the real API. |
| 1:15–1:55 | Create a deployment from `nginxinc/nginx-unprivileged:1.27-alpine` with the tested port and one replica. Show the persisted accepted request, then the actual Deployment and Pod reaching Ready. Do not skip or replace the wait with a static screen. |
| 1:55–2:25 | Scale from one to two replicas. Show the API-accepted operation, actual desired/ready replica counts, and scale lifecycle events. |
| 2:25–2:55 | Show revision history, initiate rollback with its confirmation/precondition, and verify the actual Kubernetes specification, new revision, readiness, and API/Web state. |
| 2:55–3:15 | Use the CLI to show deployment status, revisions, and one relevant diagnostics command. Do not display tokens or credentials. |
| 3:15–3:30 | Close with engineering highlights, trade-offs, and features deferred from the portfolio MVP. |

If any stage fails, keep the failure visible and describe it accurately. Do not
splice in a different cluster, account, image, deployment, or previously
captured success as if it were the same run.

## Genuine screenshot checklist

Capture only real UI states from the successful current-candidate workflow,
with personal data and credentials excluded:

- [ ] Dashboard and project overview.
- [ ] Deployment creation form with image, port, and replica configuration.
- [ ] Deployment detail showing the actual Ready state and timestamps.
- [ ] Pod/replica status showing one replica, then two after scale convergence.
- [ ] Revision history and the resulting rollback state.
- [ ] Lifecycle-event timeline with request correlation visible where safe.
- [ ] Bounded logs and Kubernetes Events from the actual deployment.

Save screenshots under `docs/assets/` only after capture; do not add mock-state
screenshots or placeholder images.

## Kubernetes acceptance, CLI flow, and troubleshooting

The Kubernetes acceptance flow is **blocked pending explicit approval** and
Docker availability. Do not apply RBAC, create a namespace/cluster, or run the
Worker until authorized. The exact isolated operation, identities, resource
impact, verification, and rollback are recorded in the
[audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md).

Once that gate is cleared and the actual candidate workflow succeeds, use the
CLI commands documented in [CLI.md](CLI.md): project creation, deployment
create/status/watch, scaling with `--expected-replicas`, revision listing,
rollback with `--expected-revision-id`, and bounded logs/events. Until then,
these CLI operations have automated test coverage but are not a live cluster
demo.

Troubleshooting:

- Docker engine unavailable: stop; do not restart Docker Desktop or clean up
  existing resources.
- Compose migration/API not ready: retain the error and inspect only the
  disposable `minicloud-resume-demo` project; do not use the protected default
  database volume.
- Kubernetes permission denied: capture the exact API resource/verb and stop;
  do not broaden the Role or fall back to administrator credentials.
- No Ready Pod: show the truthful state, not a fabricated success. Do not
  capture a Ready/scale/rollback screenshot until observed state and API
  readback agree.
