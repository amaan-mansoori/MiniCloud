# Troubleshooting

## API readiness fails

`/livez` reports process liveness; `/readyz` checks database readiness. A 503
means the API cannot confirm its database. Inspect sanitized API logs and
database health without printing environment variables or connection URLs.

## Deployments remain queued or progressing

HTTP 202 means the request was durably accepted, not that Kubernetes is ready.
Check operation status and lifecycle events, then inspect the Worker health
state and sanitized logs. Verify that Kubernetes credentials and context were
configured explicitly. Do not switch to administrator credentials as a
workaround.

## Web API calls fail

Check the Web build's `NEXT_PUBLIC_API_URL`, the API's configured CORS origin,
and the selected local ports. The API URL is compiled into the Web bundle.
Use browser error UI and sanitized API logs; never include tokens or passwords
in reports.

## Migration failure

Stop and preserve the database. Verify the migration role, ownership, and
grants against a disposable database before retrying. Do not downgrade a
persistent database without reviewing data-loss behavior and an approved
recovery plan.

## Docker and Kubernetes unavailable

If Docker cannot reach its Linux engine, stop runtime checks; do not restart
Docker or remove unrelated containers, images, networks, or volumes. If
Kubernetes authorization fails, preserve the error and review the exact
namespace-scoped permission needed. Do not broaden roles or fall back to
administrator credentials.

See the [setup guide](SETUP.md), [Kubernetes guide](KUBERNETES.md), and
[current verification audit](../reports/MINICLOUD-RESUME-MVP-AUDIT.md).
