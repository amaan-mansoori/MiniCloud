# Kubernetes Integration

The adapter targets one configured namespace. It generates deterministic
Deployment and ClusterIP Service resources with MiniCloud ownership labels,
resource requests/limits, restricted Pod security context, and probes. It
does not accept arbitrary Kubernetes manifests. The Worker Role manifest is
`infra/kubernetes/namespace-rbac.yaml`; it permits Deployment and Service
get/create/patch and Pod list only, and does not permit Secret access, delete,
or cluster-wide API access.

The same manifest defines a separate `minicloud-api-diagnostics` ServiceAccount
and Role. The API diagnostics identity is read-only: it may list namespace
Pods and Events and get the `pods/log` subresource. It has no Deployment,
Service, Secret, delete, or cluster-wide permissions. Do not assign the Worker
identity or administrator credentials to the API. The manifest is proposed
configuration only; these privileges have not been tested against a live
cluster.

## Context and credential rules

Local configuration must explicitly set `KUBERNETES_CONFIG_FILE` and
`KUBERNETES_CONTEXT`. The adapter does not select or change the current
`kubectl` context. Do not point the local adapter at an administrator
kubeconfig for least-privilege proof.

Pod-log and Kubernetes Event endpoints use a separate client configuration:
set `KUBERNETES_DIAGNOSTICS_IN_CLUSTER=true` only when the API Pod is assigned
the dedicated diagnostics ServiceAccount, or set both
`KUBERNETES_DIAGNOSTICS_CONFIG_FILE` and
`KUBERNETES_DIAGNOSTICS_CONTEXT` for an explicitly selected local identity.
The diagnostics client never falls back to the Worker identity or current
context; when no diagnostics identity is configured, these endpoints return
HTTP 503 rather than an empty success.

The CLI exposes these reads as `deploy logs` and `deploy kubernetes-events`.
The Web console provides explicit Load Pod logs and Load Kubernetes Events
actions for the selected deployment; it does not fetch diagnostics
automatically. Both surfaces display API errors and truncation metadata. The
API bounds each request to at most 10 Pods/64 KiB per Pod log or 100 Events.
Neither control is evidence of live cluster authorization or successful
diagnostic retrieval; both still require the separate API identity above.

The namespace/Role/RoleBinding YAML is a proposed bootstrap artifact, not
evidence of live cluster state. Apply it only to an explicitly authorized,
disposable test cluster. Do not use it to modify a shared or production
cluster.

## Verification

Offline adapter tests validate object shape and mapping. The opt-in kind
integration test is skipped unless explicitly enabled with a kubeconfig and
context. Current-candidate live Worker reconciliation, denied
Secret/cross-namespace access, and full Kubernetes lifecycle acceptance remain
unverified.

Do not run `kubectl apply`, `delete`, `auth reconcile`, or credential renewal
commands against the current cluster as generic setup steps. See
[deployment](DEPLOYMENT.md) for the required approval boundary.
