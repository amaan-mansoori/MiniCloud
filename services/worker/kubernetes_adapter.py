from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, settings
from app.core.models import Deployment
from app.kubernetes_names import deployment_resource_name
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from yaml import YAMLError

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
PROJECT_ID_LABEL = "minicloud.io/project-id"
DEPLOYMENT_ID_LABEL = "minicloud.io/deployment-id"


class KubernetesConfigurationError(RuntimeError):
    pass


class KubernetesReconcileError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class KubernetesAuthenticationError(KubernetesReconcileError):
    pass


@dataclass(frozen=True)
class DeploymentObservation:
    ready: bool
    message: str
    terminal: bool = False


def build_kubernetes_adapter(configured: Settings = settings) -> KubernetesDeploymentAdapter:
    try:
        if configured.kubernetes_in_cluster:
            config.load_incluster_config()
        else:
            kubeconfig = configured.kubernetes_config_file or os.getenv("KUBECONFIG")
            context = configured.kubernetes_context
            if not kubeconfig:
                raise KubernetesConfigurationError(
                    "Set KUBERNETES_CONFIG_FILE and KUBERNETES_CONTEXT for local Kubernetes access."
                )
            if not context:
                raise KubernetesConfigurationError(
                    "Set KUBERNETES_CONTEXT explicitly; MiniCloud will not select the current context."
                )
            if not Path(kubeconfig).is_file():
                raise KubernetesConfigurationError("The configured Kubernetes kubeconfig file does not exist.")
            config.load_kube_config(config_file=kubeconfig, context=context, persist_config=False)
    except KubernetesConfigurationError:
        raise
    except (ConfigException, OSError, YAMLError) as exc:
        raise KubernetesConfigurationError("Kubernetes client configuration could not be loaded.") from exc

    return KubernetesDeploymentAdapter(
        apps_api=client.AppsV1Api(),
        core_api=client.CoreV1Api(),
        namespace=configured.kubernetes_namespace,
        rollout_timeout_seconds=configured.kubernetes_rollout_timeout_seconds,
    )


class KubernetesDeploymentAdapter:
    def __init__(
        self,
        apps_api: client.AppsV1Api,
        core_api: client.CoreV1Api,
        namespace: str,
        rollout_timeout_seconds: int = 600,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", namespace) or len(namespace) > 63:
            raise ValueError("Kubernetes namespace must be a valid lowercase DNS label.")
        self.apps_api = apps_api
        self.core_api = core_api
        self.namespace = namespace
        self.rollout_timeout_seconds = rollout_timeout_seconds

    @staticmethod
    def resource_name(deployment_id: str) -> str:
        return deployment_resource_name(deployment_id)

    def _labels(self, deployment: Deployment) -> dict[str, str]:
        return {
            MANAGED_BY_LABEL: "minicloud",
            PROJECT_ID_LABEL: deployment.project_id,
            DEPLOYMENT_ID_LABEL: deployment.id,
        }

    def _deployment_body(self, deployment: Deployment) -> client.V1Deployment:
        name = self.resource_name(deployment.id)
        labels = self._labels(deployment)
        selector = {DEPLOYMENT_ID_LABEL: deployment.id}
        image_name = deployment.image.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
        is_default_nginx = image_name == "nginx" and deployment.port == 80
        container = client.V1Container(
            name="app",
            image=deployment.image,
            ports=[client.V1ContainerPort(container_port=deployment.port)],
            readiness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/", port=deployment.port)
                if is_default_nginx
                else None,
                tcp_socket=None
                if is_default_nginx
                else client.V1TCPSocketAction(port=deployment.port),
                initial_delay_seconds=2,
                period_seconds=5,
                failure_threshold=3,
            ),
            liveness_probe=(
                client.V1Probe(
                    http_get=client.V1HTTPGetAction(path="/", port=deployment.port),
                    initial_delay_seconds=5,
                    period_seconds=10,
                    failure_threshold=3,
                )
                if is_default_nginx
                else None
            ),
            resources=client.V1ResourceRequirements(
                requests={"cpu": "100m", "memory": "128Mi"},
                limits={"cpu": "500m", "memory": "512Mi"},
            ),
            security_context=client.V1SecurityContext(
                privileged=False,
                allow_privilege_escalation=False,
                run_as_non_root=True,
                capabilities=client.V1Capabilities(
                    drop=["ALL"],
                    add=["NET_BIND_SERVICE"],
                ),
                run_as_user=101 if is_default_nginx else None,
                run_as_group=101 if is_default_nginx else None,
                read_only_root_filesystem=True if is_default_nginx else None,
            ),
        )
        if is_default_nginx:
            container.command = ["/usr/sbin/nginx"]
            container.args = ["-g", "daemon off;"]
            container.volume_mounts = [
                client.V1VolumeMount(name="nginx-cache", mount_path="/var/cache/nginx"),
                client.V1VolumeMount(name="nginx-run", mount_path="/run"),
            ]
        volumes = (
            [
                client.V1Volume(name="nginx-cache", empty_dir=client.V1EmptyDirVolumeSource()),
                client.V1Volume(name="nginx-run", empty_dir=client.V1EmptyDirVolumeSource()),
            ]
            if is_default_nginx
            else None
        )
        return client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(name=name, namespace=self.namespace, labels=labels),
            spec=client.V1DeploymentSpec(
                replicas=deployment.replicas,
                selector=client.V1LabelSelector(match_labels=selector),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={**labels, **selector}),
                    spec=client.V1PodSpec(
                        security_context=client.V1PodSecurityContext(
                            fs_group=101 if is_default_nginx else None,
                            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                        ),
                        volumes=volumes,
                        containers=[container],
                        automount_service_account_token=False,
                    ),
                ),
            ),
        )

    def _service_body(self, deployment: Deployment) -> client.V1Service:
        name = self.resource_name(deployment.id)
        labels = self._labels(deployment)
        return client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(name=name, namespace=self.namespace, labels=labels),
            spec=client.V1ServiceSpec(
                type="ClusterIP",
                selector={DEPLOYMENT_ID_LABEL: deployment.id},
                ports=[client.V1ServicePort(port=deployment.port, target_port=deployment.port)],
            ),
        )

    def _verify_ownership(
        self,
        existing: client.V1ObjectMeta,
        expected_labels: dict[str, str],
        kind: str,
    ) -> None:
        labels = existing.labels or {}
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise KubernetesReconcileError(
                f"Refusing to modify the existing {kind}; its MiniCloud ownership labels do not match.",
                retryable=False,
            )

    @staticmethod
    def _api_error(exc: ApiException, action: str) -> KubernetesReconcileError:
        code = exc.status
        if code == 401:
            return KubernetesAuthenticationError(
                f"Kubernetes authentication failed for the {action} request (HTTP 401); "
                "renew the worker credential. The job is paused without consuming its retry budget.",
                retryable=False,
            )
        if code == 403:
            return KubernetesReconcileError(
                f"Kubernetes denied the {action} request (HTTP {code}); verify the worker's namespace Role.",
                retryable=False,
            )
        if code == 404:
            return KubernetesReconcileError(
                "Kubernetes namespace or workload resource was not found; verify the configured namespace and bootstrap resources.",
                retryable=False,
            )
        if code in (408, 429, 500, 502, 503, 504) or code is None:
            return KubernetesReconcileError(
                f"Kubernetes temporarily failed the {action} request"
                + (f" (HTTP {code})." if code is not None else "."),
                retryable=True,
            )
        return KubernetesReconcileError(
            f"Kubernetes rejected the {action} request" + (f" (HTTP {code})." if code else "."),
            retryable=False,
        )

    def verify_credentials(self) -> None:
        try:
            self.core_api.read_namespaced_service(
                "minicloud-worker-credential-check",
                self.namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                return
            raise self._api_error(exc, "validate worker credentials") from exc

    def _apply_deployment(self, body: client.V1Deployment) -> None:
        name = body.metadata.name
        try:
            existing = self.apps_api.read_namespaced_deployment(name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise self._api_error(exc, "read Deployment") from exc
            try:
                self.apps_api.create_namespaced_deployment(self.namespace, body)
            except ApiException as create_exc:
                if create_exc.status == 409:
                    raise KubernetesReconcileError("Deployment creation conflicted with a concurrent writer; retry safely.", retryable=True) from create_exc
                raise self._api_error(create_exc, "create Deployment") from create_exc
            return

        self._verify_ownership(existing.metadata, body.metadata.labels, "Deployment")
        try:
            self.apps_api.api_client.call_api(
                "/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
                "PATCH",
                path_params={"namespace": self.namespace, "name": name},
                header_params={
                    "Accept": "application/json",
                    "Content-Type": "application/merge-patch+json",
                },
                body=body,
                response_type="V1Deployment",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
                _request_timeout=30,
            )
        except ApiException as exc:
            raise self._api_error(exc, "update Deployment") from exc

    def _apply_service(self, body: client.V1Service) -> None:
        name = body.metadata.name
        try:
            existing = self.core_api.read_namespaced_service(name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise self._api_error(exc, "read Service") from exc
            try:
                self.core_api.create_namespaced_service(self.namespace, body)
            except ApiException as create_exc:
                if create_exc.status == 409:
                    raise KubernetesReconcileError("Service creation conflicted with a concurrent writer; retry safely.", retryable=True) from create_exc
                raise self._api_error(create_exc, "create Service") from create_exc
            return

        self._verify_ownership(existing.metadata, body.metadata.labels, "Service")
        try:
            self.core_api.patch_namespaced_service(name, self.namespace, body)
        except ApiException as exc:
            raise self._api_error(exc, "update Service") from exc

    def apply(self, deployment: Deployment) -> None:
        try:
            self._apply_deployment(self._deployment_body(deployment))
            self._apply_service(self._service_body(deployment))
        except KubernetesReconcileError:
            raise
        except ApiException as exc:
            raise self._api_error(exc, "reconcile workload") from exc

    @staticmethod
    def _pod_failure(pods: list[client.V1Pod]) -> tuple[str, bool] | None:
        for pod in pods:
            if pod.status is None:
                continue
            for condition in pod.status.conditions or []:
                if (
                    condition.type == "PodScheduled"
                    and condition.status == "False"
                    and condition.reason == "Unschedulable"
                ):
                    return (
                        "Pods cannot be scheduled; check namespace quota and available cluster resources.",
                        False,
                    )
            for container_status in pod.status.container_statuses or []:
                waiting = container_status.state.waiting if container_status.state else None
                if waiting is None:
                    continue
                if waiting.reason in {"ImagePullBackOff", "ErrImagePull"}:
                    return (
                        "Container image could not be pulled; verify the image reference and registry access.",
                        True,
                    )
                if waiting.reason == "CrashLoopBackOff":
                    return (
                        "Container is restarting repeatedly; inspect the application image and startup behavior.",
                        True,
                    )
        return None

    def observe(self, deployment: Deployment) -> DeploymentObservation:
        name = self.resource_name(deployment.id)
        try:
            current = self.apps_api.read_namespaced_deployment(name, self.namespace)
            pods = self.core_api.list_namespaced_pod(
                self.namespace,
                label_selector=f"{DEPLOYMENT_ID_LABEL}={deployment.id}",
            ).items
        except ApiException as exc:
            if exc.status == 404:
                self.apply(deployment)
                return DeploymentObservation(False, "Kubernetes workload was missing and is being recreated.")
            raise self._api_error(exc, "observe Deployment") from exc

        current_containers = current.spec.template.spec.containers if current.spec and current.spec.template.spec else []
        current_container = current_containers[0] if current_containers else None
        current_ports = current_container.ports if current_container else []
        current_port = current_ports[0].container_port if current_ports else None
        if (
            current.spec.replicas != deployment.replicas
            or current_container is None
            or current_container.image != deployment.image
            or current_port != deployment.port
        ):
            self.apply(deployment)
            return DeploymentObservation(False, "Kubernetes workload drift was detected and the desired state was reapplied.")

        status = current.status
        if (
            status
            and status.observed_generation is not None
            and current.metadata.generation is not None
            and status.observed_generation < current.metadata.generation
        ):
            return DeploymentObservation(False, "Waiting for Kubernetes to observe the latest Deployment generation.")

        if status and status.conditions:
            for condition in status.conditions:
                if condition.type == "Progressing" and condition.reason == "ProgressDeadlineExceeded":
                    return DeploymentObservation(False, "Kubernetes reported that the rollout exceeded its progress deadline.", terminal=True)

        pod_failure = self._pod_failure(pods)
        if pod_failure:
            message, terminal = pod_failure
            return DeploymentObservation(False, message, terminal=terminal)

        ready_replicas = status.ready_replicas or 0 if status else 0
        available_replicas = status.available_replicas or 0 if status else 0
        if ready_replicas >= deployment.replicas and available_replicas >= deployment.replicas:
            return DeploymentObservation(True, f"Observed {ready_replicas}/{deployment.replicas} ready replicas.")
        return DeploymentObservation(False, f"Waiting for ready replicas ({ready_replicas}/{deployment.replicas}).")
