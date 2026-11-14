# Local Setup

## Requirements

- Windows PowerShell for the documented Compose commands.
- Docker Desktop with Linux containers and Docker Compose v2.
- Python 3.12 or later for the API test suite and CLI.
- Node.js 22 and npm 11 for Web development.

The full Kubernetes workflow additionally needs kubectl, kind, a disposable
cluster, and explicit authorization to create its isolated resources. It has
not been verified for the current candidate.

## Disposable API and Web demo

The supported quick demo is `docker-compose.demo.yml`. It uses PostgreSQL
with temporary storage, a private Compose network, and a loopback-only gateway.
It starts no Worker or Kubernetes resources, so deployment jobs remain queued.
Do not use it to claim a successful deployment.

Before starting, verify Docker is reachable and the ports `13018` and `18017`
are available. From PowerShell at the repository root:

```powershell
$env:MINICLOUD_DEMO_JWT_SECRET = (& python -c "import secrets; print(secrets.token_hex(32))")
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml --profile migration run --build --rm migrate
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml up --build -d api web gateway
```

Open `http://127.0.0.1:13018`. Do not enter real credentials or sensitive
data. The demo Compose file has no bind mounts or named volumes. Shut down
only this named project:

```powershell
docker compose --project-name minicloud-resume-demo --file docker-compose.demo.yml down
Remove-Item Env:\MINICLOUD_DEMO_JWT_SECRET
```

These are setup instructions, not a claim that runtime startup has been
verified. Do not substitute the main Compose configuration when preserving
existing data or services.

## Source development

Install backend dependencies and run the API/CLI tests from the repository
root:

```powershell
python -m pip install -r services\api\requirements.txt
python -m pytest services\api\tests -q
python -m ruff check services\api\app services\api\tests services\api\integration_tests services\worker packages\minicloud_cli
```

Install Web dependencies and run its checks:

```powershell
Set-Location apps\web
npm ci
npm run lint
npm run typecheck
npm run test:e2e
npm run build
```

The Playwright test uses a mocked API and does not verify a deployment.
Return to the repository root before running Python commands.

## Environment and secrets

`.env.example` contains placeholders only. Copy it to an ignored `.env` for
local development and replace every placeholder with a unique, generated
value. Never overwrite an existing `.env`, commit credentials, or place tokens
in command-line arguments, logs, screenshots, or reports. Keep Kubernetes
credentials outside the repository and configure an explicit kubeconfig and
context; MiniCloud does not select or change the current kubectl context.

Migrations require `SERVICE_ROLE=migrate` and a dedicated
`MINICLOUD_MIGRATION_DATABASE_URL`. API and Worker runtime roles must be
separate from the migration role. Do not run migration commands against a
database unless it is disposable or an approved maintenance operation.

## Kubernetes setup

The source Role and ServiceAccount definitions are in
`infra/kubernetes/namespace-rbac.yaml`. They are configuration, not evidence
that a cluster has been provisioned or the permissions have been tested.
Before a real deployment test, use an explicitly approved disposable cluster
and verify positive and negative authorization checks for a dedicated Worker
identity. Never use administrator credentials for Worker validation.
