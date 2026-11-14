# Worker Kubernetes Credential Runbook

## Scope and trust boundary

The Worker supports explicit local kubeconfig/context configuration and
in-cluster ServiceAccount credentials. Any credential renewal process must
run under a separately trusted operator identity. Never give the Worker
permission to mint credentials or mount an administrator kubeconfig.

Do not copy kubeconfig contents, Worker tokens, or credential-bearing command
output into logs, tickets, or source control. Store local credentials only in
an access-restricted, ignored location.

## Renewal scripts

`New-MiniCloudWorkerKubeconfig.ps1` requests a short-lived token for a
namespace-scoped Worker ServiceAccount. It requires an explicitly supplied
kubeconfig and context, validates token format and expiry, restricts the
temporary file ACL, and atomically replaces the local Worker kubeconfig only
after validation.

`Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1` serializes concurrent calls and
renews when credentials are missing, malformed, expired, or near expiry. It
does not print token contents. These scripts do not configure a scheduler;
an operator must review and authorize any unattended renewal.

Example invocation (replace all placeholders with approved values):

```powershell
.\scripts\Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1 `
  -AdminKubeconfig "<protected-admin-kubeconfig>" `
  -Context "<explicit-cluster-context>" `
  -Namespace "<worker-namespace>" `
  -OutputPath "<ignored-worker-credential-path>" `
  -ApiServer "<cluster-api-endpoint>"
```

Treat any terminating failure as a failure. Do not broaden Worker RBAC or
switch to administrator credentials as a workaround. Resolve the cause under
the trusted operator boundary before retrying.

## Optional scheduling

Scheduling is not installed by this repository. Before enabling it, use a
dedicated least-privilege host identity, protect the source credential and
destination directory, record exit status and sanitized errors, and alert on
repeated failures. Validate behavior across user-profile, ACL, lock, and
cluster API outages. Do not capture credential contents.

## Test limits

Run the Windows-only fixture suite with:

```powershell
.\scripts\Test-MiniCloudWorkerCredentialRenewal.ps1
```

It uses fixture files and simulated `kubectl`/ACL utilities. It tests renewal
control flow but does not contact Kubernetes, alter an operator's credentials,
create a scheduled task, or prove live authentication recovery.
