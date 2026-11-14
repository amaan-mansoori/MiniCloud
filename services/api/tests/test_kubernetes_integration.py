from __future__ import annotations

import os
import time
import uuid

import pytest
from app.core.models import Deployment
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from services.worker.kubernetes_adapter import KubernetesDeploymentAdapter

pytestmark = pytest.mark.skipif(
    os.getenv("MINICLOUD_KUBERNETES_INTEGRATION") != "1",
    reason="Set MINICLOUD_KUBERNETES_INTEGRATION=1 and explicit test kubeconfig/context to run.",
)


def test_real_kubernetes_deployment_reaches_observed_readiness():
    kubeconfig = os.getenv("MINICLOUD_TEST_KUBECONFIG")
    context = os.getenv("MINICLOUD_TEST_CONTEXT")
    if not kubeconfig or not context:
        pytest.fail("MINICLOUD_TEST_KUBECONFIG and MINICLOUD_TEST_CONTEXT must explicitly identify the test cluster.")

    config.load_kube_config(config_file=kubeconfig, context=context, persist_config=False)
    core_api = client.CoreV1Api()
    run_id = uuid.uuid4().hex
    namespace = f"minicloud-it-{run_id[:8]}"
    created_namespace = core_api.create_namespace(
        client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=namespace,
                labels={
                    "app.kubernetes.io/managed-by": "minicloud-integration-test",
                    "minicloud.io/test-run-id": run_id,
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                },
            )
        )
    )

    try:
        deployment_id = str(uuid.uuid4())
        deployment = Deployment(
            id=deployment_id,
            project_id=str(uuid.uuid4()),
            owner_id=str(uuid.uuid4()),
            name="readiness-check",
            image=os.getenv("MINICLOUD_TEST_IMAGE", "nginx:1.27.3-alpine"),
            port=80,
            replicas=1,
            status="accepted",
        )
        adapter = KubernetesDeploymentAdapter(
            apps_api=client.AppsV1Api(),
            core_api=core_api,
            namespace=namespace,
            rollout_timeout_seconds=300,
        )

        adapter.apply(deployment)
        deadline = time.monotonic() + 300
        observation = None
        while time.monotonic() < deadline:
            observation = adapter.observe(deployment)
            if observation.ready or observation.terminal:
                break
            time.sleep(3)
        assert observation is not None and observation.ready, (
            observation.message if observation else "No Kubernetes readiness observation was produced."
        )
    finally:
        try:
            current_namespace = core_api.read_namespace(namespace)
        except ApiException as exc:
            if exc.status == 404:
                current_namespace = None
            else:
                raise

        if current_namespace is not None:
            labels = current_namespace.metadata.labels or {}
            if (
                current_namespace.metadata.uid != created_namespace.metadata.uid
                or labels.get("minicloud.io/test-run-id") != run_id
            ):
                pytest.fail(
                    f"Refusing cleanup because namespace {namespace} no longer matches this test's ownership marker."
                )
            try:
                core_api.delete_namespace(namespace, propagation_policy="Foreground")
            except ApiException as exc:
                if exc.status != 404:
                    raise

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    core_api.read_namespace(namespace)
                except ApiException as exc:
                    if exc.status == 404:
                        break
                    raise
                time.sleep(2)
            else:
                pytest.fail(f"Timed out waiting for integration namespace {namespace} deletion.")
