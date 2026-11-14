from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.core.config import Settings
from app.core.models import Deployment
from app.kubernetes_names import deployment_resource_name
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from services.worker.kubernetes_adapter import (
    DEPLOYMENT_ID_LABEL,
    MANAGED_BY_LABEL,
    PROJECT_ID_LABEL,
    DeploymentObservation,
    KubernetesAuthenticationError,
    KubernetesConfigurationError,
    KubernetesDeploymentAdapter,
    KubernetesReconcileError,
    build_kubernetes_adapter,
)


def make_deployment() -> Deployment:
    return Deployment(
        id="8e9568ce-a261-4b3c-87e5-130a3218aa2c",
        project_id="518a23ab-c6b1-44c5-a4a7-6412bc3618a4",
        owner_id="d1f5da2d-86d1-4dbc-b119-16559fb9b2f1",
        name="web",
        image="nginx:1.27",
        port=8080,
        replicas=2,
        status="accepted",
    )


def make_adapter(
    apps_api: Mock | None = None, core_api: Mock | None = None
) -> KubernetesDeploymentAdapter:
    return KubernetesDeploymentAdapter(
        apps_api=apps_api or Mock(),
        core_api=core_api or Mock(),
        namespace="minicloud",
    )


@pytest.mark.parametrize(
    ("deployment_id", "expected"),
    [
        ("70a4e11e-d723-498f-b82a-12efd57bf0b0", "mc-70a4e11ed723498fb82a12ef"),
        ("ABCDEF12-3456-7890-ABCD-EF1234567890", "mc-abcdef1234567890abcdef12"),
    ],
)
def test_deployment_resource_name_matches_canonical_truncation(deployment_id: str, expected: str):
    assert deployment_resource_name(deployment_id) == expected
    assert KubernetesDeploymentAdapter.resource_name(deployment_id) == expected
    assert len(expected) <= 63
    assert re.fullmatch(r"[a-z0-9-]+", expected)


def test_deployment_resource_name_rejects_short_identifiers():
    with pytest.raises(ValueError, match="at least eight"):
        deployment_resource_name("short")


def make_observed_deployment(
    deployment: Deployment, status: client.V1DeploymentStatus
) -> client.V1Deployment:
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(generation=1),
        spec=client.V1DeploymentSpec(
            replicas=deployment.replicas,
            selector=client.V1LabelSelector(match_labels={DEPLOYMENT_ID_LABEL: deployment.id}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="app",
                            image=deployment.image,
                            ports=[client.V1ContainerPort(container_port=deployment.port)],
                        )
                    ]
                )
            ),
        ),
        status=status,
    )


def test_apply_creates_deployment_and_service_with_safe_labels_and_security():
    apps_api, core_api = Mock(), Mock()
    apps_api.read_namespaced_deployment.side_effect = ApiException(status=404)
    core_api.read_namespaced_service.side_effect = ApiException(status=404)
    adapter = make_adapter(apps_api, core_api)
    deployment = make_deployment()

    adapter.apply(deployment)

    apps_api.create_namespaced_deployment.assert_called_once()
    core_api.create_namespaced_service.assert_called_once()
    body = apps_api.create_namespaced_deployment.call_args.args[1]
    assert body.metadata.namespace == "minicloud"
    assert body.metadata.labels[MANAGED_BY_LABEL] == "minicloud"
    assert body.metadata.labels[PROJECT_ID_LABEL] == deployment.project_id
    assert body.metadata.labels[DEPLOYMENT_ID_LABEL] == deployment.id
    container = body.spec.template.spec.containers[0]
    assert container.image == "nginx:1.27"
    assert container.readiness_probe.tcp_socket.port == 8080
    assert container.resources.requests == {"cpu": "100m", "memory": "128Mi"}
    assert container.resources.limits == {"cpu": "500m", "memory": "512Mi"}
    assert container.security_context.privileged is False
    assert container.security_context.allow_privilege_escalation is False
    assert container.security_context.run_as_non_root is True
    assert container.security_context.capabilities.drop == ["ALL"]
    assert container.security_context.capabilities.add == ["NET_BIND_SERVICE"]
    assert body.spec.template.spec.security_context.seccomp_profile.type == "RuntimeDefault"
    assert body.spec.template.spec.automount_service_account_token is False


def test_default_nginx_runs_non_root_with_writable_runtime_paths_and_http_probes():
    deployment = make_deployment()
    deployment.image = "nginx:1.27.3-alpine"
    deployment.port = 80

    body = make_adapter()._deployment_body(deployment)

    pod_spec = body.spec.template.spec
    container = pod_spec.containers[0]
    assert container.command == ["/usr/sbin/nginx"]
    assert container.args == ["-g", "daemon off;"]
    assert container.security_context.run_as_non_root is True
    assert container.security_context.run_as_user == 101
    assert container.security_context.run_as_group == 101
    assert container.security_context.read_only_root_filesystem is True
    assert container.security_context.allow_privilege_escalation is False
    assert container.security_context.capabilities.drop == ["ALL"]
    assert container.security_context.capabilities.add == ["NET_BIND_SERVICE"]
    assert pod_spec.security_context.fs_group == 101
    assert pod_spec.security_context.seccomp_profile.type == "RuntimeDefault"
    assert pod_spec.automount_service_account_token is False
    assert {volume.name for volume in pod_spec.volumes} == {"nginx-cache", "nginx-run"}
    assert {mount.mount_path for mount in container.volume_mounts} == {
        "/var/cache/nginx",
        "/run",
    }
    assert container.readiness_probe.http_get.path == "/"
    assert container.liveness_probe.http_get.path == "/"


def test_nginx_compatibility_profile_does_not_change_user_selected_port():
    deployment = make_deployment()
    deployment.image = "nginx:1.27.3-alpine"
    deployment.port = 8080

    container = make_adapter()._deployment_body(deployment).spec.template.spec.containers[0]

    assert container.command is None
    assert container.security_context.run_as_user is None
    assert container.volume_mounts is None
    assert container.liveness_probe is None
    assert container.readiness_probe.tcp_socket.port == 8080


def test_apply_patches_owned_resources_idempotently():
    deployment = make_deployment()
    adapter = make_adapter()
    labels = adapter._labels(deployment)
    apps_existing = SimpleNamespace(metadata=client.V1ObjectMeta(labels=labels))
    service_existing = SimpleNamespace(metadata=client.V1ObjectMeta(labels=labels))
    adapter.apps_api.read_namespaced_deployment.return_value = apps_existing
    adapter.core_api.read_namespaced_service.return_value = service_existing

    adapter.apply(deployment)
    adapter.apply(deployment)

    assert adapter.apps_api.api_client.call_api.call_count == 2
    assert adapter.core_api.patch_namespaced_service.call_count == 2
    request = adapter.apps_api.api_client.call_api.call_args
    assert request.args == (
        "/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
        "PATCH",
    )
    assert request.kwargs["header_params"]["Content-Type"] == "application/merge-patch+json"
    assert request.kwargs["header_params"]["Accept"] == "application/json"
    adapter.apps_api.create_namespaced_deployment.assert_not_called()
    adapter.core_api.create_namespaced_service.assert_not_called()


def test_apply_refuses_to_patch_resources_owned_by_another_actor():
    deployment = make_deployment()
    adapter = make_adapter()
    adapter.apps_api.read_namespaced_deployment.return_value = SimpleNamespace(
        metadata=client.V1ObjectMeta(labels={MANAGED_BY_LABEL: "someone-else"})
    )

    with pytest.raises(KubernetesReconcileError, match="ownership labels") as exc_info:
        adapter.apply(deployment)

    assert exc_info.value.retryable is False
    adapter.apps_api.patch_namespaced_deployment.assert_not_called()


def test_observe_reports_ready_only_from_observed_replicas():
    deployment = make_deployment()
    adapter = make_adapter()
    adapter.apps_api.read_namespaced_deployment.return_value = make_observed_deployment(
        deployment,
        client.V1DeploymentStatus(
            observed_generation=4,
            ready_replicas=2,
            available_replicas=2,
        ),
    )
    adapter.core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])

    result = adapter.observe(deployment)

    assert result == DeploymentObservation(True, "Observed 2/2 ready replicas.")


def test_observe_reconciles_replica_drift_to_requested_scale():
    deployment = make_deployment()
    deployment.replicas = 3
    adapter = make_adapter()
    current = make_observed_deployment(
        deployment,
        client.V1DeploymentStatus(observed_generation=1, ready_replicas=2, available_replicas=2),
    )
    current.spec.replicas = 2
    current.metadata.labels = adapter._labels(deployment)
    adapter.apps_api.read_namespaced_deployment.return_value = current
    adapter.core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    adapter.core_api.read_namespaced_service.return_value = SimpleNamespace(
        metadata=client.V1ObjectMeta(labels=adapter._labels(deployment))
    )

    result = adapter.observe(deployment)

    assert result == DeploymentObservation(
        False,
        "Kubernetes workload drift was detected and the desired state was reapplied.",
    )
    adapter.apps_api.api_client.call_api.assert_called_once()
    patched_deployment = adapter.apps_api.api_client.call_api.call_args.kwargs["body"]
    assert patched_deployment.spec.replicas == 3


@pytest.mark.parametrize(
    ("pod_status", "expected", "terminal"),
    [
        (
            client.V1PodStatus(
                container_statuses=[
                    client.V1ContainerStatus(
                        name="app",
                        image="nginx:1.27",
                        image_id="docker-pullable://nginx@sha256:abc",
                        ready=False,
                        restart_count=0,
                        state=client.V1ContainerState(
                            waiting=client.V1ContainerStateWaiting(reason="ImagePullBackOff")
                        ),
                    )
                ]
            ),
            "Container image could not be pulled; verify the image reference and registry access.",
            True,
        ),
        (
            client.V1PodStatus(
                container_statuses=[
                    client.V1ContainerStatus(
                        name="app",
                        image="nginx:1.27",
                        image_id="docker-pullable://nginx@sha256:abc",
                        ready=False,
                        restart_count=4,
                        state=client.V1ContainerState(
                            waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff")
                        ),
                    )
                ]
            ),
            "Container is restarting repeatedly; inspect the application image and startup behavior.",
            True,
        ),
        (
            client.V1PodStatus(
                conditions=[
                    client.V1PodCondition(
                        type="PodScheduled",
                        status="False",
                        reason="Unschedulable",
                    )
                ]
            ),
            "Pods cannot be scheduled; check namespace quota and available cluster resources.",
            False,
        ),
    ],
)
def test_observe_reports_actionable_pod_failures(pod_status, expected, terminal):
    deployment = make_deployment()
    adapter = make_adapter()
    adapter.apps_api.read_namespaced_deployment.return_value = make_observed_deployment(
        deployment,
        client.V1DeploymentStatus(observed_generation=1),
    )
    adapter.core_api.list_namespaced_pod.return_value = SimpleNamespace(
        items=[client.V1Pod(status=pod_status)]
    )

    result = adapter.observe(deployment)

    assert result.ready is False
    assert result.message == expected
    assert result.terminal is terminal


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(401, False), (403, False), (404, False), (429, True), (503, True)],
)
def test_apply_classifies_api_errors_without_reflecting_raw_response(status_code, retryable):
    adapter = make_adapter()
    adapter.apps_api.read_namespaced_deployment.side_effect = ApiException(
        status=status_code,
        reason="private detail that must not be reflected",
    )

    with pytest.raises(KubernetesReconcileError) as exc_info:
        adapter.apply(make_deployment())

    assert exc_info.value.retryable is retryable
    assert "private detail" not in str(exc_info.value)
    assert isinstance(exc_info.value, KubernetesAuthenticationError) is (status_code == 401)


def test_kubernetes_client_reloads_rotated_kubeconfig_on_adapter_rebuild(tmp_path):
    kubeconfig = tmp_path / "worker.kubeconfig"
    configured = Settings(
        app_env="test",
        kubernetes_in_cluster=False,
        kubernetes_config_file=str(kubeconfig),
        kubernetes_context="minicloud-worker",
    )
    config_document = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "test-cluster",
                "cluster": {
                    "server": "https://127.0.0.1:6443",
                    "certificate-authority-data": "ZmFrZS1jZXJ0",
                },
            }
        ],
        "contexts": [
            {
                "name": "minicloud-worker",
                "context": {"cluster": "test-cluster", "user": "minicloud-worker"},
            }
        ],
        "current-context": "minicloud-worker",
        "users": [{"name": "minicloud-worker", "user": {"token": "credential-before-rotation"}}],
    }
    kubeconfig.write_text(yaml.safe_dump(config_document), encoding="utf-8")

    first_adapter = build_kubernetes_adapter(configured)
    first_configuration = first_adapter.apps_api.api_client.configuration
    assert first_configuration.api_key["authorization"] == "Bearer credential-before-rotation"

    config_document["users"][0]["user"]["token"] = "credential-after-rotation"
    kubeconfig.write_text(yaml.safe_dump(config_document), encoding="utf-8")
    second_adapter = build_kubernetes_adapter(configured)

    assert (
        second_adapter.apps_api.api_client.configuration.api_key["authorization"]
        == "Bearer credential-after-rotation"
    )
    assert first_configuration.api_key["authorization"] == "Bearer credential-before-rotation"


def test_malformed_kubeconfig_is_rejected_without_exposing_contents(tmp_path):
    kubeconfig = tmp_path / "malformed.kubeconfig"
    kubeconfig.write_text("apiVersion: [invalid\nprivate-token-fragment", encoding="utf-8")
    configured = Settings(
        app_env="test",
        kubernetes_in_cluster=False,
        kubernetes_config_file=str(kubeconfig),
        kubernetes_context="minicloud-worker",
    )

    with pytest.raises(KubernetesConfigurationError) as exc_info:
        build_kubernetes_adapter(configured)

    assert "private-token-fragment" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status_code", "expected_error", "retryable"),
    [
        (401, KubernetesAuthenticationError, False),
        (403, KubernetesReconcileError, False),
        (None, KubernetesReconcileError, True),
        (503, KubernetesReconcileError, True),
    ],
)
def test_credential_probe_distinguishes_authentication_and_network_failures(
    status_code,
    expected_error,
    retryable,
):
    adapter = make_adapter()
    adapter.core_api.read_namespaced_service.side_effect = ApiException(status=status_code)

    with pytest.raises(expected_error) as exc_info:
        adapter.verify_credentials()

    assert exc_info.value.retryable is retryable


def test_credential_probe_treats_missing_probe_service_as_authenticated():
    adapter = make_adapter()
    adapter.core_api.read_namespaced_service.side_effect = ApiException(status=404)

    adapter.verify_credentials()

    adapter.core_api.read_namespaced_service.assert_called_once_with(
        "minicloud-worker-credential-check",
        "minicloud",
    )


def test_missing_local_kubeconfig_fails_without_selecting_a_current_context(monkeypatch):
    monkeypatch.delenv("KUBECONFIG", raising=False)
    configured = Settings(
        app_env="test",
        kubernetes_in_cluster=False,
        kubernetes_config_file=None,
        kubernetes_context=None,
    )

    with pytest.raises(KubernetesConfigurationError, match="KUBERNETES_CONFIG_FILE"):
        build_kubernetes_adapter(configured)


def test_local_kubeconfig_requires_an_explicit_context(tmp_path):
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    configured = Settings(
        app_env="test",
        kubernetes_in_cluster=False,
        kubernetes_config_file=str(kubeconfig),
        kubernetes_context=None,
    )

    with pytest.raises(KubernetesConfigurationError, match="KUBERNETES_CONTEXT"):
        build_kubernetes_adapter(configured)


def test_worker_rbac_is_namespaced_and_does_not_grant_secrets_or_cluster_roles():
    manifest = Path(__file__).resolve().parents[3] / "infra" / "kubernetes" / "namespace-rbac.yaml"
    documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    kinds = {document["kind"] for document in documents}
    roles = {
        document["metadata"]["name"]: document
        for document in documents
        if document["kind"] == "Role"
    }
    role_bindings = {
        document["metadata"]["name"]: document
        for document in documents
        if document["kind"] == "RoleBinding"
    }
    service_accounts = {
        document["metadata"]["name"]: document
        for document in documents
        if document["kind"] == "ServiceAccount"
    }
    namespace = next(document for document in documents if document["kind"] == "Namespace")
    worker_role = roles["minicloud-worker"]
    worker_binding = role_bindings["minicloud-worker"]
    worker_service_account = service_accounts["minicloud-worker"]
    diagnostics_role = roles["minicloud-api-diagnostics"]
    diagnostics_binding = role_bindings["minicloud-api-diagnostics"]
    diagnostics_service_account = service_accounts["minicloud-api-diagnostics"]

    assert kinds == {"Namespace", "ServiceAccount", "Role", "RoleBinding"}
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/warn"] == "restricted"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/audit"] == "restricted"
    assert worker_role["metadata"]["namespace"] == "minicloud"
    assert worker_binding["metadata"]["namespace"] == "minicloud"
    assert worker_binding["roleRef"]["kind"] == "Role"
    assert worker_service_account["automountServiceAccountToken"] is True
    assert all("secrets" not in rule["resources"] for rule in worker_role["rules"])
    assert all("delete" not in rule["verbs"] for rule in worker_role["rules"])
    assert all(rule["resources"] != ["secrets"] for rule in worker_role["rules"])
    permissions = {
        resource: set(rule["verbs"])
        for rule in worker_role["rules"]
        for resource in rule["resources"]
    }
    assert permissions == {
        "deployments": {"get", "create", "patch"},
        "services": {"get", "create", "patch"},
        "pods": {"list"},
    }
    assert diagnostics_binding["metadata"]["namespace"] == "minicloud"
    assert diagnostics_binding["roleRef"]["kind"] == "Role"
    assert diagnostics_service_account["automountServiceAccountToken"] is True
    diagnostics_permissions = {
        resource: set(rule["verbs"])
        for rule in diagnostics_role["rules"]
        for resource in rule["resources"]
    }
    assert diagnostics_permissions == {
        "pods": {"list"},
        "pods/log": {"get"},
        "events": {"list"},
    }
    assert "deployments" not in diagnostics_permissions
    assert "services" not in diagnostics_permissions
    assert "secrets" not in diagnostics_permissions
