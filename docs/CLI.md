# MiniCloud CLI

Run the dependency-free CLI from the repository root:

```powershell
python -m packages.minicloud_cli.cli --help
```

Set `MINICLOUD_API_URL` or pass `--api-url`. Authenticated commands require
`--email` or `MINICLOUD_EMAIL`; the CLI prompts for a password and keeps the
access token in memory only. Non-loopback API URLs must use HTTPS. No token or
password is printed.

```powershell
python -m packages.minicloud_cli.cli register --email developer@example.test
$env:MINICLOUD_EMAIL = "developer@example.test"
python -m packages.minicloud_cli.cli platform capabilities
python -m packages.minicloud_cli.cli project list --limit 50
python -m packages.minicloud_cli.cli deploy list --project-id <project-id> --limit 50
python -m packages.minicloud_cli.cli project create --name Demo --slug demo
python -m packages.minicloud_cli.cli project member list --project-id <project-id>
python -m packages.minicloud_cli.cli deploy create --project-id <project-id> --name web --image nginx:1.27-alpine
python -m packages.minicloud_cli.cli deploy scale --project-id <project-id> --deployment-id <deployment-id> --replicas 3 --expected-replicas 2 --idempotency-key scale-<unique-value>
python -m packages.minicloud_cli.cli deploy revisions --project-id <project-id> --deployment-id <deployment-id>
python -m packages.minicloud_cli.cli deploy rollback --project-id <project-id> --deployment-id <deployment-id> --revision-id <target-revision-id> --expected-revision-id <current-revision-id> --idempotency-key rollback-<unique-value>
python -m packages.minicloud_cli.cli deploy status --deployment-id <deployment-id>
python -m packages.minicloud_cli.cli deploy watch --deployment-id <deployment-id> --timeout 300
python -m packages.minicloud_cli.cli deploy events --project-id <project-id> --deployment-id <deployment-id>
python -m packages.minicloud_cli.cli deploy logs --project-id <project-id> --deployment-id <deployment-id> --tail-lines 100
python -m packages.minicloud_cli.cli deploy kubernetes-events --project-id <project-id> --deployment-id <deployment-id>
python -m packages.minicloud_cli.cli --output json deploy status --deployment-id <deployment-id>
```

Readable text is the default; `--output json` must precede the command.
`deploy create` and `deploy scale` wait for the operation and exit
successfully only when status is `ready`; HTTP 202 acceptance alone is not
success. Use `deploy watch` to resume observing a deployment. Terminal
failure, timeout, auth failure, HTTP error, or API unavailability returns a
nonzero code. Rollback requires the selected target revision and the current
revision ID as an optimistic precondition; it waits for the normal readiness
observation. Poll interval is bounded to 0.1-300 seconds and timeout to
(0, 86400] seconds.

Project member list/add/role/remove commands use server-side owner checks.
For scale, read the current `replicas` value from `deploy list` and pass it as
`--expected-replicas`; a stale value is rejected with HTTP 409.
`deploy revisions` returns one cursor-paginated page. Pass `--cursor` with the
returned cursor to continue. Revision snapshots are retained; rollback creates
a new revision rather than overwriting history. Deployment logs and
Kubernetes-event commands read the current bounded Kubernetes source, not
MiniCloud's persisted lifecycle-event history. `deploy logs` accepts
`--container` (default `app`), `--tail-lines` (1-1000, default 100), and
`--since-seconds` (1-86400). Each response is limited by the API to at most 10
Pods and 64 KiB per Pod log; Kubernetes Events are capped at 100. Both commands
require an explicitly configured API diagnostics identity and can return HTTP
503 when Kubernetes diagnostics are unavailable. They do not reuse Worker
credentials or silently return empty data.

Project and deployment list commands return one cursor-paginated page with
`items` and `next_cursor`. Pass the returned cursor to `--cursor` on the next
command to continue; use `--output json` to preserve the cursor value reliably.
