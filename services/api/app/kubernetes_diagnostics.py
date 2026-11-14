from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException
from yaml import YAMLError

from app.core.config import Settings, settings
from app.kubernetes_names import deployment_resource_name

DEPLOYMENT_ID_LABEL = "minicloud.io/deployment-id"
PROJECT_ID_LABEL = "minicloud.io/project-id"

MAX_PODS = 10
MAX_LOG_BYTES_PER_POD = 64 * 1024
MAX_KUBERNETES_EVENTS = 100

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)([\"']?)([^\s,;\"']+)"
)
_BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*")


class KubernetesDiagnosticsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PodLog:
    pod_name: str
    container: str
    log: str
    truncated: bool


@dataclass(frozen=True)
class KubernetesEvent:
    resource_kind: str
    resource_name: str
    event_type: str
    reason: str | None
    message: str
    created_at: datetime


def redact_diagnostic_text(value: str) -> str:
    value = _BEARER_TOKEN.sub(r"\1[REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)


def build_kubernetes_diagnostics_adapter(
    configured: Settings = settings,
) -> KubernetesDiagnosticsAdapter:
    try:
        if configured.kubernetes_diagnostics_in_cluster:
            configuration = client.Configuration()
            config.load_incluster_config(client_configuration=configuration)
            api_client = client.ApiClient(configuration=configuration)
        else:
            kubeconfig = configured.kubernetes_diagnostics_config_file
            context = configured.kubernetes_diagnostics_context
            if not kubeconfig or not context:
                raise KubernetesDiagnosticsUnavailable(
                    "Kubernetes diagnostics require a dedicated kubeconfig and explicit context."
                )
            if not Path(kubeconfig).is_file():
                raise KubernetesDiagnosticsUnavailable(
                    "The configured Kubernetes diagnostics kubeconfig does not exist."
                )
            api_client = config.new_client_from_config(
                config_file=kubeconfig,
                context=context,
                persist_config=False,
            )
    except KubernetesDiagnosticsUnavailable:
        raise
    except (ConfigException, OSError, YAMLError) as exc:
        raise KubernetesDiagnosticsUnavailable(
            "Kubernetes diagnostics client configuration could not be loaded."
        ) from exc

    return KubernetesDiagnosticsAdapter(
        core_api=client.CoreV1Api(api_client=api_client),
        namespace=configured.kubernetes_namespace,
    )


@lru_cache(maxsize=1)
def get_kubernetes_diagnostics_adapter() -> KubernetesDiagnosticsAdapter:
    return build_kubernetes_diagnostics_adapter()


class KubernetesDiagnosticsAdapter:
    def __init__(self, core_api: client.CoreV1Api, namespace: str) -> None:
        self.core_api = core_api
        self.namespace = namespace

    def _pods(self, project_id: str, deployment_id: str) -> tuple[list[client.V1Pod], bool]:
        response = self.core_api.list_namespaced_pod(
            self.namespace,
            label_selector=(
                f"{PROJECT_ID_LABEL}={project_id},{DEPLOYMENT_ID_LABEL}={deployment_id}"
            ),
            limit=MAX_PODS,
        )
        pods = [
            pod
            for pod in response.items
            if pod.metadata
            and pod.metadata.namespace == self.namespace
            and (pod.metadata.labels or {}).get(PROJECT_ID_LABEL) == project_id
            and (pod.metadata.labels or {}).get(DEPLOYMENT_ID_LABEL) == deployment_id
        ]
        return pods, bool(response.metadata and response.metadata._continue)

    def read_pod_logs(
        self,
        project_id: str,
        deployment_id: str,
        container: str,
        since_seconds: int | None,
        tail_lines: int,
    ) -> tuple[list[PodLog], bool, datetime]:
        pods, pod_limit_reached = self._pods(project_id, deployment_id)
        if not pods:
            raise KubernetesDiagnosticsUnavailable(
                "No Kubernetes Pods are currently available for this deployment."
            )

        logs: list[PodLog] = []
        truncated = pod_limit_reached
        for pod in pods:
            pod_name = pod.metadata.name
            container_names = {item.name for item in (pod.spec.containers if pod.spec else [])}
            if container not in container_names:
                raise ValueError("The requested container is not part of this deployment.")
            content = self.core_api.read_namespaced_pod_log(
                pod_name,
                self.namespace,
                container=container,
                since_seconds=since_seconds,
                tail_lines=tail_lines,
                timestamps=True,
                limit_bytes=MAX_LOG_BYTES_PER_POD,
            )
            encoded = content.encode("utf-8")
            was_truncated = len(encoded) >= MAX_LOG_BYTES_PER_POD
            if was_truncated:
                encoded = encoded[-MAX_LOG_BYTES_PER_POD:]
            logs.append(
                PodLog(
                    pod_name=pod_name,
                    container=container,
                    log=redact_diagnostic_text(encoded.decode("utf-8", errors="replace")),
                    truncated=was_truncated,
                )
            )
            truncated = truncated or was_truncated
        return logs, truncated, datetime.now(UTC)

    def list_kubernetes_events(
        self,
        project_id: str,
        deployment_id: str,
    ) -> tuple[list[KubernetesEvent], bool, datetime]:
        pods, pod_limit_reached = self._pods(project_id, deployment_id)
        if not pods:
            raise KubernetesDiagnosticsUnavailable(
                "No Kubernetes Pods are currently available for this deployment."
            )

        pod_names = {
            pod.metadata.uid: pod.metadata.name
            for pod in pods
            if pod.metadata and pod.metadata.uid and pod.metadata.name
        }
        if not pod_names:
            raise KubernetesDiagnosticsUnavailable(
                "Kubernetes Pod identity is unavailable for this deployment."
            )
        retrieved_at = datetime.now(UTC)
        deployment_name = deployment_resource_name(deployment_id)
        responses = [
            self.core_api.list_namespaced_event(
                self.namespace,
                field_selector="involvedObject.kind=Pod",
                limit=MAX_KUBERNETES_EVENTS,
            ),
            self.core_api.list_namespaced_event(
                self.namespace,
                field_selector=(
                    f"involvedObject.kind=Deployment,involvedObject.name={deployment_name}"
                ),
                limit=MAX_KUBERNETES_EVENTS,
            ),
        ]
        events = []
        for response in responses:
            for event in response.items:
                involved = event.involved_object
                if not involved or involved.namespace != self.namespace:
                    continue
                if involved.kind == "Pod":
                    if involved.uid not in pod_names:
                        continue
                    resource_name = pod_names[involved.uid]
                elif involved.kind == "Deployment" and involved.name == deployment_name:
                    resource_name = deployment_name
                else:
                    continue
                created_at = event.event_time or event.last_timestamp or event.first_timestamp
                if created_at is None and event.metadata:
                    created_at = event.metadata.creation_timestamp
                if created_at is None:
                    continue
                events.append(
                    KubernetesEvent(
                        resource_kind=involved.kind,
                        resource_name=resource_name,
                        event_type=event.type or "Unknown",
                        reason=event.reason,
                        message=redact_diagnostic_text(event.message or ""),
                        created_at=created_at,
                    )
                )
        events.sort(key=lambda item: item.created_at, reverse=True)
        event_limit_reached = any(
            bool(response.metadata and response.metadata._continue)
            for response in responses
        )
        event_limit_reached = event_limit_reached or len(events) > MAX_KUBERNETES_EVENTS
        return (
            events[:MAX_KUBERNETES_EVENTS],
            pod_limit_reached or event_limit_reached,
            retrieved_at,
        )
