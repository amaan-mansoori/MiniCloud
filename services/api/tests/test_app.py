from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from app.core.config import Settings, load_settings, settings
from app.core.db import Base, get_db
from app.core.models import (
    AuditEvent,
    Deployment,
    DeploymentEvent,
    DeploymentJob,
    DeploymentRevision,
    Project,
    ProjectMembership,
    ProjectResourcePolicy,
    ProjectResourcePolicyMutation,
    TokenRevocation,
    User,
)
from app.core.rate_limit import FixedWindowRateLimiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    password_hasher,
    verify_password,
)
from app.kubernetes_diagnostics import (
    DEPLOYMENT_ID_LABEL,
    MAX_LOG_BYTES_PER_POD,
    PROJECT_ID_LABEL,
    KubernetesDiagnosticsAdapter,
    KubernetesDiagnosticsUnavailable,
    build_kubernetes_diagnostics_adapter,
)
from app.kubernetes_names import deployment_resource_name
from app.main import app, auth_rate_limiter, run_database_migrations
from app.routers.auth import _delete_expired_token_revocations
from app.schemas import DeploymentCreate
from fastapi.testclient import TestClient
from kubernetes import client as kubernetes_client
from kubernetes.client.exceptions import ApiException
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.worker.kubernetes_adapter import (
    DeploymentObservation,
    KubernetesAuthenticationError,
    KubernetesConfigurationError,
    KubernetesReconcileError,
)
from services.worker.main import _claim_jobs, _process_job, _rollout_started_at, process_jobs

engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_auth_rate_limiter():
    auth_rate_limiter.clear()


def test_register_and_login():
    register = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "secret1234"},
    )
    assert register.status_code == 201, register.text
    payload = register.json()
    assert payload["user"]["email"] == "alice@example.com"
    assert "access_token" in payload

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "secret1234"},
    )
    assert login.status_code == 200, login.text

    bad_login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong-pass"},
    )
    assert bad_login.status_code == 401


def test_password_verification_handles_expected_failures_and_propagates_unexpected_errors(
    monkeypatch,
):
    password_hash = hash_password("correct-password")

    assert verify_password("wrong-password", password_hash) is False
    assert verify_password("wrong-password", "not-an-argon2-hash") is False

    def fail_verification(*_args):
        raise RuntimeError("Argon2 backend unavailable")

    monkeypatch.setattr(type(password_hasher), "verify", fail_verification)
    with pytest.raises(RuntimeError, match="Argon2 backend unavailable"):
        verify_password("correct-password", password_hash)


def test_create_project_and_deployment():
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "secret1234"},
    )
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project = client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "slug": "demo-project"},
        headers=headers,
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    deployment = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "web", "image": "nginx:latest", "port": 8080, "replicas": 2},
        headers=headers,
    )
    assert deployment.status_code == 202, deployment.text
    body = deployment.json()
    assert body["status"] == "accepted"

    listing = client.get(f"/api/v1/projects/{project_id}/deployments", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1
    with TestingSessionLocal() as db:
        audit_actions = {
            event.action
            for event in db.query(AuditEvent).filter(AuditEvent.project_id == project_id).all()
        }
    assert audit_actions == {"project.created", "deployment.created"}


def test_project_quota_defaults_are_persisted_and_enforced_before_enqueue():
    suffix = uuid.uuid4().hex[:8]
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"quota-owner-{suffix}@example.com", "password": "secure-test-password"},
    )
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Quota project", "slug": f"quota-{suffix}"},
        headers=headers,
    ).json()
    project_id = project["id"]

    quota_path = f"/api/v1/projects/{project_id}/quota"
    quota = client.get(quota_path, headers=headers)
    assert quota.status_code == 200
    assert quota.headers["ETag"] == '"1"'
    assert quota.json() == {
        "state": "pending",
        "version": 1,
        "desired": {
            "max_replicas": 4,
            "cpu_request": "400m",
            "cpu_limit": "2",
            "memory_request": "512Mi",
            "memory_limit": "2Gi",
        },
        "applied_version": None,
        "updated_at": quota.json()["updated_at"],
    }

    rejected = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "over-limit", "image": "nginx:1.27", "replicas": 5},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert "project's configured" in rejected.json()["detail"]
    with TestingSessionLocal() as db:
        assert db.query(Deployment).filter(Deployment.project_id == project_id).count() == 0
        assert db.query(DeploymentJob).filter(DeploymentJob.project_id == project_id).count() == 0

    with TestingSessionLocal() as db:
        policy = db.query(ProjectResourcePolicy).filter_by(project_id=project_id).one()
        assert policy.max_replicas == 4
        assert policy.cpu_limit_millicores == 2000
        assert policy.memory_limit_bytes == 2 * 1024**3
        assert policy.applied_version is None


def test_project_quota_update_is_owner_only_versioned_and_idempotent():
    suffix = uuid.uuid4().hex[:8]
    owner_registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"quota-update-owner-{suffix}@example.com", "password": "secure-test-password"},
    )
    member_registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"quota-update-member-{suffix}@example.com", "password": "secure-test-password"},
    )
    stranger_registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"quota-update-stranger-{suffix}@example.com", "password": "secure-test-password"},
    )
    owner_headers = {
        "Authorization": f"Bearer {owner_registration.json()['access_token']}",
    }
    member_headers = {
        "Authorization": f"Bearer {member_registration.json()['access_token']}",
    }
    stranger_headers = {
        "Authorization": f"Bearer {stranger_registration.json()['access_token']}",
    }
    project = client.post(
        "/api/v1/projects",
        json={"name": "Quota update", "slug": f"quota-update-{suffix}"},
        headers=owner_headers,
    ).json()
    project_id = project["id"]
    quota_path = f"/api/v1/projects/{project_id}/quota"
    added = client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"email": f"quota-update-member-{suffix}@example.com"},
        headers=owner_headers,
    )
    assert added.status_code == 201

    assert client.get(quota_path, headers=member_headers).status_code == 200
    hidden = client.get(quota_path, headers=stranger_headers)
    assert hidden.status_code == 404
    forbidden = client.patch(
        quota_path,
        json={"max_replicas": 2, "cpu_limit": "1", "memory_limit": "1Gi"},
        headers={
            **member_headers,
            "If-Match": '"1"',
            "Idempotency-Key": f"quota-member-{suffix}",
        },
    )
    assert forbidden.status_code == 403

    patch_headers = {
        **owner_headers,
        "If-Match": '"1"',
        "Idempotency-Key": f"quota-owner-{suffix}",
    }
    payload = {"max_replicas": 2, "cpu_limit": "1", "memory_limit": "1Gi"}
    updated = client.patch(quota_path, json=payload, headers=patch_headers)
    assert updated.status_code == 200, updated.text
    assert updated.headers["ETag"] == '"2"'
    assert updated.json()["state"] == "pending"
    assert updated.json()["version"] == 2
    assert updated.json()["desired"] == {
        "max_replicas": 2,
        "cpu_request": "200m",
        "cpu_limit": "1",
        "memory_request": "256Mi",
        "memory_limit": "1Gi",
    }

    replay = client.patch(quota_path, json=payload, headers=patch_headers)
    assert replay.status_code == 200
    assert replay.json() == updated.json()
    conflict = client.patch(
        quota_path,
        json={**payload, "max_replicas": 1},
        headers=patch_headers,
    )
    assert conflict.status_code == 409
    stale = client.patch(
        quota_path,
        json=payload,
        headers={**patch_headers, "Idempotency-Key": f"quota-stale-{suffix}"},
    )
    assert stale.status_code == 409

    with TestingSessionLocal() as db:
        policy = db.query(ProjectResourcePolicy).filter_by(project_id=project_id).one()
        assert policy.version == 2
        assert policy.updated_by_user_id == owner_registration.json()["user"]["id"]
        mutations = (
            db.query(ProjectResourcePolicyMutation)
            .filter_by(project_id=project_id)
            .all()
        )
        assert len(mutations) == 1
        audit = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.project_id == project_id,
                AuditEvent.action == "project.quota_updated",
            )
            .one()
        )
        assert audit.actor_user_id == owner_registration.json()["user"]["id"]


def test_legacy_project_requires_an_explicit_policy_and_quota_bounds_are_validated():
    suffix = uuid.uuid4().hex[:8]
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"legacy-quota-{suffix}@example.com", "password": "secure-test-password"},
    )
    user_id = registration.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project_id = str(uuid.uuid4())
    with TestingSessionLocal() as db:
        db.add(Project(id=project_id, name="Legacy quota", slug=f"legacy-quota-{suffix}", owner_id=user_id))
        db.add(ProjectMembership(project_id=project_id, user_id=user_id, role="owner"))
        db.commit()

    quota_path = f"/api/v1/projects/{project_id}/quota"
    quota = client.get(quota_path, headers=headers)
    assert quota.status_code == 200
    assert quota.json() == {
        "state": "unconfigured",
        "version": None,
        "desired": None,
        "applied_version": None,
        "updated_at": None,
    }
    blocked = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "legacy-app", "image": "nginx:1.27"},
        headers=headers,
    )
    assert blocked.status_code == 409

    invalid = client.patch(
        quota_path,
        json={"max_replicas": 4, "cpu_limit": "1", "memory_limit": "1Gi"},
        headers={
            **headers,
            "If-Match": '"0"',
            "Idempotency-Key": f"invalid-quota-{suffix}",
        },
    )
    assert invalid.status_code == 422
    invalid_quantity = client.patch(
        quota_path,
        json={"max_replicas": 1, "cpu_limit": "not-a-quantity", "memory_limit": "1Gi"},
        headers={
            **headers,
            "If-Match": '"0"',
            "Idempotency-Key": f"invalid-quantity-{suffix}",
        },
    )
    assert invalid_quantity.status_code == 422
    configured = client.patch(
        quota_path,
        json={"max_replicas": 2, "cpu_limit": "1", "memory_limit": "1Gi"},
        headers={
            **headers,
            "If-Match": '"0"',
            "Idempotency-Key": f"valid-quota-{suffix}",
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["version"] == 1
    assert configured.json()["state"] == "pending"


def test_scale_and_rollback_cannot_exceed_the_project_quota():
    suffix = uuid.uuid4().hex[:8]
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"quota-ops-{suffix}@example.com", "password": "secure-test-password"},
    )
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Quota operations", "slug": f"quota-ops-{suffix}"},
        headers=headers,
    ).json()
    project_id = project["id"]
    created = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "quota-app", "image": "nginx:1.27", "replicas": 4},
        headers=headers,
    )
    assert created.status_code == 202
    deployment_id = created.json()["deployment_id"]

    with TestingSessionLocal() as db:
        deployment = db.query(Deployment).filter_by(id=deployment_id).one()
        deployment.status = "ready"
        create_job = (
            db.query(DeploymentJob)
            .filter_by(deployment_id=deployment_id, operation_type="deploy")
            .one()
        )
        create_job.state = "completed"
        db.commit()

    scale_path = (
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/scale"
    )
    scaled = client.patch(
        scale_path,
        json={
            "replicas": 1,
            "expected_replicas": 4,
            "idempotency_key": f"quota-reduce-{suffix}",
        },
        headers=headers,
    )
    assert scaled.status_code == 202, scaled.text
    with TestingSessionLocal() as db:
        deployment = db.query(Deployment).filter_by(id=deployment_id).one()
        deployment.status = "ready"
        scale_job = (
            db.query(DeploymentJob)
            .filter_by(deployment_id=deployment_id, operation_type="scale")
            .one()
        )
        scale_job.state = "completed"
        current_revision = (
            db.query(DeploymentRevision)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentRevision.revision_number.desc())
            .first()
        )
        initial_revision = (
            db.query(DeploymentRevision)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentRevision.revision_number)
            .first()
        )
        assert current_revision is not None
        assert initial_revision is not None
        current_revision_id = current_revision.id
        initial_revision_id = initial_revision.id
        db.commit()

    quota_path = f"/api/v1/projects/{project_id}/quota"
    tightened = client.patch(
        quota_path,
        json={"max_replicas": 1, "cpu_limit": "500m", "memory_limit": "512Mi"},
        headers={
            **headers,
            "If-Match": '"1"',
            "Idempotency-Key": f"quota-tighten-{suffix}",
        },
    )
    assert tightened.status_code == 200, tightened.text

    over_limit_scale = client.patch(
        scale_path,
        json={
            "replicas": 2,
            "expected_replicas": 1,
            "idempotency_key": f"quota-over-scale-{suffix}",
        },
        headers=headers,
    )
    assert over_limit_scale.status_code == 422

    over_limit_rollback = client.post(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/rollback",
        json={
            "revision_id": initial_revision_id,
            "expected_revision_id": current_revision_id,
            "idempotency_key": f"quota-over-rollback-{suffix}",
        },
        headers=headers,
    )
    assert over_limit_rollback.status_code == 422

    with TestingSessionLocal() as db:
        assert (
            db.query(DeploymentJob)
            .filter_by(deployment_id=deployment_id)
            .count()
            == 2
        )
        assert (
            db.query(DeploymentRevision)
            .filter_by(deployment_id=deployment_id)
            .count()
            == 2
        )


def test_project_name_and_slug_lengths_are_checked_after_normalization():
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"project-validation-{uuid.uuid4().hex}@example.com",
            "password": "secure-test-password",
        },
    )
    assert registration.status_code == 201, registration.text
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project_path = "/api/v1/projects"

    assert (
        client.post(
            project_path,
            json={"name": "  ", "slug": "  "},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            project_path,
            json={"name": " x ", "slug": " ab "},
            headers=headers,
        ).status_code
        == 422
    )

    slug = f"project-validation-{uuid.uuid4().hex}"
    created = client.post(
        project_path,
        json={"name": " Demo Project ", "slug": f" {slug.upper()} "},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["name"] == "Demo Project"
    assert project["slug"] == slug

    project_url = f"{project_path}/{project['id']}"
    assert (
        client.patch(
            project_url,
            json={"name": "  "},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(
            project_url,
            json={"name": " x "},
            headers=headers,
        ).status_code
        == 422
    )
    updated = client.patch(
        project_url,
        json={"name": " Updated Project "},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Updated Project"


def test_project_and_deployment_lists_use_stable_cursor_pagination():
    suffix = uuid.uuid4().hex
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"pagination-{suffix}@example.com", "password": "secure-test-password"},
    )
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    projects = []
    for index in range(3):
        response = client.post(
            "/api/v1/projects",
            json={"name": f"Page {index}", "slug": f"page-{index}-{suffix}"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        projects.append(response.json())

    project_id = projects[0]["id"]
    deployments = []
    for index in range(3):
        response = client.post(
            f"/api/v1/projects/{project_id}/deployments",
            json={"name": f"page-app-{index}", "image": "nginx:1.27-alpine"},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        deployments.append(response.json()["deployment_id"])

    tied_timestamp = datetime(2026, 11, 13, tzinfo=UTC)
    with TestingSessionLocal() as db:
        for project in projects:
            db.query(Project).filter(Project.id == project["id"]).update(
                {Project.created_at: tied_timestamp}
            )
        for deployment_id in deployments:
            db.query(Deployment).filter(Deployment.id == deployment_id).update(
                {Deployment.created_at: tied_timestamp}
            )
        db.commit()

    project_path = "/api/v1/projects"
    first_projects = client.get(project_path, headers=headers, params={"limit": 2})
    assert first_projects.status_code == 200, first_projects.text
    project_page_one = first_projects.json()
    assert len(project_page_one["items"]) == 2
    assert project_page_one["next_cursor"]
    second_projects = client.get(
        project_path,
        headers=headers,
        params={"limit": 2, "cursor": project_page_one["next_cursor"]},
    ).json()
    project_ids = [item["id"] for item in project_page_one["items"] + second_projects["items"]]
    assert project_ids == sorted(project_ids, reverse=True)
    assert set(project_ids) == {project["id"] for project in projects}
    assert second_projects["next_cursor"] is None
    assert (
        client.get(project_path, headers=headers, params={"cursor": "invalid"}).status_code == 400
    )

    deployment_path = f"/api/v1/projects/{project_id}/deployments"
    first_deployments = client.get(deployment_path, headers=headers, params={"limit": 2})
    assert first_deployments.status_code == 200, first_deployments.text
    deployment_page_one = first_deployments.json()
    assert len(deployment_page_one["items"]) == 2
    assert deployment_page_one["next_cursor"]
    second_deployments = client.get(
        deployment_path,
        headers=headers,
        params={"limit": 2, "cursor": deployment_page_one["next_cursor"]},
    ).json()
    deployment_ids = [
        item["id"] for item in deployment_page_one["items"] + second_deployments["items"]
    ]
    assert deployment_ids == sorted(deployment_ids, reverse=True)
    assert set(deployment_ids) == set(deployments)
    assert second_deployments["next_cursor"] is None
    assert (
        client.get(deployment_path, headers=headers, params={"cursor": "invalid"}).status_code
        == 400
    )


def test_invalid_login_and_malformed_auth():
    client.post(
        "/api/v1/auth/register", json={"email": "carol@example.com", "password": "secret1234"}
    )

    invalid = client.post(
        "/api/v1/auth/login", json={"email": "carol@example.com", "password": "bad-pass"}
    )
    assert invalid.status_code == 401

    no_token = client.get("/api/v1/projects", headers={})
    assert no_token.status_code == 401

    bad_token = client.get("/api/v1/projects", headers={"Authorization": "Bearer bad-token"})
    assert bad_token.status_code == 401


def test_cross_user_project_and_invalid_deployment_denied():
    alice = client.post(
        "/api/v1/auth/register", json={"email": "dave@example.com", "password": "secret1234"}
    ).json()
    bob = client.post(
        "/api/v1/auth/register", json={"email": "erin@example.com", "password": "secret1234"}
    ).json()

    project = client.post(
        "/api/v1/projects",
        json={"name": "Alpha", "slug": "alpha"},
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert project.status_code == 201
    project_id = project.json()["id"]

    hidden = client.get(
        f"/api/v1/projects/{project_id}", headers={"Authorization": f"Bearer {bob['access_token']}"}
    )
    missing = client.get(
        "/api/v1/projects/nonexistent-project",
        headers={"Authorization": f"Bearer {bob['access_token']}"},
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()

    bad_deploy = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "", "image": "nginx:latest", "port": 70000, "replicas": 0},
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert bad_deploy.status_code == 422


def test_cross_user_cannot_list_or_read_project_deployments_or_operations():
    owner = client.post(
        "/api/v1/auth/register",
        json={"email": "deployment-owner@example.com", "password": "secret1234"},
    ).json()
    stranger = client.post(
        "/api/v1/auth/register",
        json={"email": "deployment-stranger@example.com", "password": "secret1234"},
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    stranger_headers = {"Authorization": f"Bearer {stranger['access_token']}"}

    project = client.post(
        "/api/v1/projects",
        json={"name": "Private deployment project", "slug": "private-deployment-project"},
        headers=owner_headers,
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    deployment = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={"name": "private-app", "image": "nginxinc/nginx-unprivileged:1.27-alpine"},
        headers=owner_headers,
    )
    assert deployment.status_code == 202
    deployment_id = deployment.json()["deployment_id"]

    hidden_deployments = client.get(
        f"/api/v1/projects/{project_id}/deployments",
        headers=stranger_headers,
    )
    missing_project_deployments = client.get(
        "/api/v1/projects/nonexistent-project/deployments",
        headers=stranger_headers,
    )
    assert hidden_deployments.status_code == missing_project_deployments.status_code == 404
    assert hidden_deployments.json() == missing_project_deployments.json()

    hidden_deployment = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}",
        headers=stranger_headers,
    )
    assert hidden_deployment.status_code == 404
    assert hidden_deployment.json() == {"detail": "Project not found"}

    hidden_operation = client.get(
        f"/api/v1/operations/{deployment_id}",
        headers=stranger_headers,
    )
    missing_operation = client.get(
        "/api/v1/operations/nonexistent-deployment", headers=stranger_headers
    )
    assert hidden_operation.status_code == missing_operation.status_code == 404
    assert hidden_operation.json() == missing_operation.json()


def test_deployment_events_are_authorized_and_cursor_paginated():
    suffix = uuid.uuid4().hex
    owner = client.post(
        "/api/v1/auth/register",
        json={"email": f"events-owner-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    stranger = client.post(
        "/api/v1/auth/register",
        json={"email": f"events-stranger-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    stranger_headers = {"Authorization": f"Bearer {stranger['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Event project", "slug": f"events-{suffix}"},
        headers=owner_headers,
    ).json()
    deployment = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={"name": "event-app", "image": "nginx:1.27-alpine"},
        headers=owner_headers,
    ).json()
    deployment_id = deployment["deployment_id"]
    db = TestingSessionLocal()
    event_time = datetime.now(UTC) + timedelta(seconds=1)
    db.add_all(
        [
            DeploymentEvent(
                deployment_id=deployment_id,
                event_type="progressing",
                message="Progressing event",
                created_at=event_time,
            ),
            DeploymentEvent(
                deployment_id=deployment_id,
                event_type="ready",
                message="Ready event",
                created_at=event_time + timedelta(seconds=1),
            ),
        ]
    )
    db.commit()
    db.close()
    path = f"/api/v1/projects/{project['id']}/deployments/{deployment_id}/events"

    first_page = client.get(path, headers=owner_headers, params={"limit": 2})
    assert first_page.status_code == 200, first_page.text
    first_body = first_page.json()
    assert [event["event_type"] for event in first_body["items"]] == ["ready", "progressing"]
    assert first_body["next_cursor"]

    second_page = client.get(
        path,
        headers=owner_headers,
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )
    assert second_page.status_code == 200, second_page.text
    assert [event["event_type"] for event in second_page.json()["items"]] == ["accepted"]
    assert second_page.json()["next_cursor"] is None

    assert client.get(path, headers=owner_headers, params={"cursor": "invalid"}).status_code == 400
    hidden_events = client.get(path, headers=stranger_headers)
    missing_project_events = client.get(
        f"/api/v1/projects/nonexistent-project/deployments/{deployment_id}/events",
        headers=stranger_headers,
    )
    assert hidden_events.status_code == missing_project_events.status_code == 404
    assert hidden_events.json() == missing_project_events.json()
    assert (
        client.get(
            f"/api/v1/projects/{project['id']}/deployments/not-a-deployment/events",
            headers=owner_headers,
        ).status_code
        == 404
    )


def test_duplicate_deployment_submission_is_rejected():
    user = client.post(
        "/api/v1/auth/register", json={"email": "frank@example.com", "password": "secret1234"}
    ).json()
    project = client.post(
        "/api/v1/projects",
        json={"name": "Bravo", "slug": "bravo"},
        headers={"Authorization": f"Bearer {user['access_token']}"},
    ).json()

    first = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={"name": "api", "image": "nginx:latest", "port": 8080, "replicas": 2},
        headers={"Authorization": f"Bearer {user['access_token']}"},
    )
    second = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={"name": "api", "image": "nginx:latest", "port": 8080, "replicas": 2},
        headers={"Authorization": f"Bearer {user['access_token']}"},
    )
    assert first.status_code == 202
    assert second.status_code == 409


def test_deployment_idempotency_key_returns_existing_operation_and_rejects_changed_payload():
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "idempotency@example.com", "password": "secret1234"},
    ).json()
    headers = {"Authorization": f"Bearer {registration['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Idempotency project", "slug": "idempotency-project"},
        headers=headers,
    ).json()
    path = f"/api/v1/projects/{project['id']}/deployments"
    payload = {
        "name": "idempotent-app",
        "image": "nginx:1.27",
        "port": 8080,
        "replicas": 2,
        "idempotency_key": "request-once-001",
    }

    blank_key = client.post(
        path,
        json={**payload, "idempotency_key": "   "},
        headers=headers,
    )
    first = client.post(path, json=payload, headers=headers)
    repeated = client.post(path, json=payload, headers=headers)
    changed = client.post(path, json={**payload, "replicas": 3}, headers=headers)

    assert blank_key.status_code == 422
    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["deployment_id"] == first.json()["deployment_id"]
    first_request_id = first.headers["X-Request-ID"]
    assert repeated.headers["X-Request-ID"] != first_request_id
    assert changed.status_code == 409

    other_project = client.post(
        "/api/v1/projects",
        json={"name": "Other idempotency project", "slug": "other-idempotency-project"},
        headers=headers,
    ).json()
    other_path = f"/api/v1/projects/{other_project['id']}/deployments"
    other_first = client.post(other_path, json=payload, headers=headers)
    other_repeated = client.post(other_path, json=payload, headers=headers)
    assert other_first.status_code == 202
    assert other_repeated.status_code == 202
    assert other_first.json()["deployment_id"] != first.json()["deployment_id"]
    assert other_repeated.json()["deployment_id"] == other_first.json()["deployment_id"]

    db = TestingSessionLocal()
    persisted_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.project_id == project["id"],
            DeploymentJob.idempotency_key == payload["idempotency_key"],
        )
        .one()
    )
    accepted_event = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == first.json()["deployment_id"],
            DeploymentEvent.event_type == "accepted",
        )
        .one()
    )
    assert persisted_job.request_id == first_request_id
    assert accepted_event.request_id == first_request_id
    db.close()

    listing = client.get(f"/api/v1/projects/{project['id']}/deployments", headers=headers)
    matching = [row for row in listing.json()["items"] if row["name"] == "idempotent-app"]
    assert len(matching) == 1


def test_scale_deployment_is_authorized_idempotent_and_records_operation():
    suffix = uuid.uuid4().hex
    owner = client.post(
        "/api/v1/auth/register",
        json={"email": f"scale-owner-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    stranger = client.post(
        "/api/v1/auth/register",
        json={"email": f"scale-stranger-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    stranger_headers = {"Authorization": f"Bearer {stranger['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Scale project", "slug": f"scale-{suffix}"},
        headers=owner_headers,
    ).json()
    deployment = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={
            "name": "scale-app",
            "image": "nginx:1.27",
            "replicas": 2,
            "idempotency_key": f"create-{suffix}",
        },
        headers=owner_headers,
    ).json()
    deployment_id = deployment["deployment_id"]
    scale_path = f"/api/v1/projects/{project['id']}/deployments/{deployment_id}/scale"

    db = TestingSessionLocal()
    stored_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    create_job = db.query(DeploymentJob).filter(DeploymentJob.deployment_id == deployment_id).one()
    stored_deployment.status = "ready"
    create_job.state = "completed"
    db.commit()
    db.close()

    payload = {
        "replicas": 3,
        "expected_replicas": 2,
        "idempotency_key": f"scale-{suffix}",
    }
    first = client.patch(scale_path, json=payload, headers=owner_headers)
    repeated = client.patch(scale_path, json=payload, headers=owner_headers)
    changed = client.patch(
        scale_path,
        json={**payload, "replicas": 4},
        headers=owner_headers,
    )
    stale = client.patch(
        scale_path,
        json={
            "replicas": 4,
            "expected_replicas": 2,
            "idempotency_key": f"stale-scale-{suffix}",
        },
        headers=owner_headers,
    )
    unauthorized = client.patch(scale_path, json=payload, headers=stranger_headers)
    missing_key = client.patch(
        scale_path,
        json={"replicas": 3, "expected_replicas": 2},
        headers=owner_headers,
    )
    missing_expected_replicas = client.patch(
        scale_path,
        json={"replicas": 3, "idempotency_key": f"missing-expected-{suffix}"},
        headers=owner_headers,
    )
    invalid_replicas = client.patch(
        scale_path,
        json={
            "replicas": 11,
            "expected_replicas": 2,
            "idempotency_key": f"invalid-{suffix}",
        },
        headers=owner_headers,
    )

    assert first.status_code == 202, first.text
    assert first.json()["job_state"] == "queued"
    assert first.json()["status"] == "accepted"
    assert repeated.status_code == 202
    scale_request_id = first.headers["X-Request-ID"]
    assert repeated.headers["X-Request-ID"] != scale_request_id
    assert repeated.json()["deployment_id"] == deployment_id
    assert changed.status_code == 409
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Deployment replica count changed; refresh before retrying"
    assert unauthorized.status_code == 404
    assert missing_key.status_code == 422
    assert missing_expected_replicas.status_code == 422
    assert invalid_replicas.status_code == 422

    db = TestingSessionLocal()
    stored_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    jobs = db.query(DeploymentJob).filter(DeploymentJob.deployment_id == deployment_id).all()
    events = db.query(DeploymentEvent).filter(DeploymentEvent.deployment_id == deployment_id).all()
    assert stored_deployment.replicas == 3
    assert stored_deployment.status == "accepted"
    audit_events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.project_id == project["id"],
            AuditEvent.action == "deployment.scale_requested",
        )
        .all()
    )
    assert len(audit_events) == 1
    assert audit_events[0].details == {
        "from_replicas": 2,
        "to_replicas": 3,
        "revision_id": next(job.revision_id for job in jobs if job.operation_type == "scale"),
    }
    assert len(jobs) == 2
    scale_job = next(job for job in jobs if job.operation_type == "scale")
    assert scale_job.target_replicas == 3
    assert scale_job.idempotency_key == payload["idempotency_key"]
    assert scale_job.request_id == scale_request_id
    scale_event = next(event for event in events if event.event_type == "scale_accepted")
    assert scale_event.message == "Scaling requested from 2 to 3 replicas."
    assert scale_event.request_id == scale_request_id
    db.close()


def test_rollback_creates_an_immutable_idempotent_revision_and_restores_configuration():
    suffix = uuid.uuid4().hex
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"rollback-owner-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    headers = {"Authorization": f"Bearer {registration['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Rollback project", "slug": f"rollback-{suffix}"},
        headers=headers,
    ).json()
    deployment = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={
            "name": "rollback-app",
            "image": "nginx:1.27",
            "port": 8081,
            "replicas": 2,
            "idempotency_key": f"create-{suffix}",
        },
        headers=headers,
    )
    assert deployment.status_code == 202, deployment.text
    deployment_id = deployment.json()["deployment_id"]
    initial_revision_id = deployment.json()["revision_id"]
    deployment_path = f"/api/v1/projects/{project['id']}/deployments/{deployment_id}"

    db = TestingSessionLocal()
    stored_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    create_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.deployment_id == deployment_id,
        )
        .one()
    )
    stored_deployment.status = "ready"
    create_job.state = "completed"
    db.commit()
    db.close()

    scaled = client.patch(
        f"{deployment_path}/scale",
        json={
            "replicas": 3,
            "expected_replicas": 2,
            "idempotency_key": f"scale-{suffix}",
        },
        headers=headers,
    )
    assert scaled.status_code == 202, scaled.text
    scaled_revision_id = scaled.json()["revision_id"]

    db = TestingSessionLocal()
    stored_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    scale_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.deployment_id == deployment_id,
            DeploymentJob.operation_type == "scale",
        )
        .one()
    )
    stored_deployment.status = "ready"
    scale_job.state = "completed"
    db.commit()
    db.close()

    foreign_deployment = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={
            "name": "other-app",
            "image": "nginx:1.26",
            "idempotency_key": f"other-{suffix}",
        },
        headers=headers,
    ).json()
    foreign_target = client.post(
        f"{deployment_path}/rollback",
        json={
            "revision_id": foreign_deployment["revision_id"],
            "expected_revision_id": scaled_revision_id,
            "idempotency_key": f"foreign-target-{suffix}",
        },
        headers=headers,
    )
    stranger = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"rollback-stranger-{suffix}@example.com",
            "password": "secure-test-password",
        },
    ).json()
    unauthorized = client.post(
        f"{deployment_path}/rollback",
        json={
            "revision_id": initial_revision_id,
            "expected_revision_id": scaled_revision_id,
            "idempotency_key": f"unauthorized-{suffix}",
        },
        headers={"Authorization": f"Bearer {stranger['access_token']}"},
    )

    payload = {
        "revision_id": initial_revision_id,
        "expected_revision_id": scaled_revision_id,
        "idempotency_key": f"rollback-{suffix}",
    }
    rollback = client.post(f"{deployment_path}/rollback", json=payload, headers=headers)
    replay = client.post(f"{deployment_path}/rollback", json=payload, headers=headers)
    conflicting_replay = client.post(
        f"{deployment_path}/rollback",
        json={**payload, "revision_id": scaled_revision_id},
        headers=headers,
    )
    stale = client.post(
        f"{deployment_path}/rollback",
        json={
            "revision_id": initial_revision_id,
            "expected_revision_id": scaled_revision_id,
            "idempotency_key": f"stale-rollback-{suffix}",
        },
        headers=headers,
    )

    assert rollback.status_code == 202, rollback.text
    assert foreign_target.status_code == 404
    assert unauthorized.status_code == 404
    assert rollback.json()["status"] == "accepted"
    assert rollback.json()["revision_id"] != initial_revision_id
    assert replay.status_code == 202
    assert replay.json()["revision_id"] == rollback.json()["revision_id"]
    assert conflicting_replay.status_code == 409
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Deployment revision changed; refresh before retrying"

    db = TestingSessionLocal()
    restored = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    revisions = (
        db.query(DeploymentRevision)
        .filter(DeploymentRevision.deployment_id == deployment_id)
        .order_by(DeploymentRevision.revision_number)
        .all()
    )
    rollback_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.idempotency_key == payload["idempotency_key"],
        )
        .one()
    )
    rollback_event = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "rollback_accepted",
        )
        .one()
    )
    audit = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.project_id == project["id"],
            AuditEvent.action == "deployment.rollback_requested",
        )
        .one()
    )
    assert (restored.name, restored.image, restored.port, restored.replicas) == (
        "rollback-app",
        "nginx:1.27",
        8081,
        2,
    )
    assert [revision.revision_number for revision in revisions] == [1, 2, 3]
    assert revisions[-1].id == rollback.json()["revision_id"]
    assert revisions[-1].rollback_of_revision_id == initial_revision_id
    assert rollback_job.operation_type == "rollback"
    assert rollback_job.revision_id == revisions[-1].id
    assert rollback_event.request_id == rollback.headers["X-Request-ID"]
    assert audit.details["rollback_of_revision_id"] == initial_revision_id
    db.close()

    history = client.get(
        f"{deployment_path}/revisions?limit=2",
        headers=headers,
    )
    assert history.status_code == 200
    assert [item["revision_number"] for item in history.json()["items"]] == [3, 2]
    assert history.json()["next_cursor"]
    next_page = client.get(
        f"{deployment_path}/revisions",
        params={"limit": 2, "cursor": history.json()["next_cursor"]},
        headers=headers,
    )
    assert next_page.status_code == 200
    assert [item["revision_number"] for item in next_page.json()["items"]] == [1]


def test_scale_requires_ready_deployment_and_rejects_overlapping_operations():
    suffix = uuid.uuid4().hex
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"scale-conflict-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    headers = {"Authorization": f"Bearer {registration['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Scale conflict", "slug": f"scale-conflict-{suffix}"},
        headers=headers,
    ).json()
    deployment = client.post(
        f"/api/v1/projects/{project['id']}/deployments",
        json={"name": "scale-app", "image": "nginx:1.27"},
        headers=headers,
    ).json()
    path = f"/api/v1/projects/{project['id']}/deployments/{deployment['deployment_id']}/scale"
    payload = {
        "replicas": 2,
        "expected_replicas": 1,
        "idempotency_key": f"scale-{suffix}",
    }
    not_ready = client.patch(path, json=payload, headers=headers)
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"] == "Deployment must be ready before it can be scaled"

    db = TestingSessionLocal()
    stored = db.query(Deployment).filter(Deployment.id == deployment["deployment_id"]).one()
    stored.status = "ready"
    active_job = (
        db.query(DeploymentJob)
        .filter(DeploymentJob.deployment_id == deployment["deployment_id"])
        .one()
    )
    active_job.state = "progressing"
    db.commit()
    db.close()

    overlapping = client.patch(
        path,
        json={**payload, "idempotency_key": f"overlap-{suffix}"},
        headers=headers,
    )
    assert overlapping.status_code == 409
    assert overlapping.json()["detail"] == "Another deployment operation is already in progress"


def test_expired_token_is_rejected():
    assert settings.jwt_secret_key is not None
    expired = jwt.encode(
        {"sub": "expired@example.com", "exp": datetime.now(UTC) - timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_access_tokens_generate_jti_and_reject_malformed_present_jti():
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "jti@example.com", "password": "secure-test-password"},
    )
    payload = decode_access_token(registration.json()["access_token"])
    assert isinstance(payload["jti"], str)
    assert payload["jti"]

    assert settings.jwt_secret_key is not None
    malformed = jwt.encode(
        {
            "sub": "jti@example.com",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "jti": "",
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {malformed}"},
    )
    assert response.status_code == 401


def test_strict_jwt_secret_validation():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://app:secure-password@localhost/minicloud",
            jwt_secret_key="short",
        )


def test_settings_repr_does_not_disclose_jwt_secret():
    secret = "test-only-secret-not-displayed-123456"
    configured = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret_key=secret,
    )

    assert secret not in repr(configured)
    assert secret not in repr(configured.model_dump())
    assert "database_url" not in configured.model_dump()
    assert "migration_database_url" not in configured.model_dump()


def test_api_service_cannot_run_database_migrations():
    with pytest.raises(RuntimeError, match="SERVICE_ROLE=migrate"):
        run_database_migrations()


def test_configuration_rejects_weak_jwt_secret_and_unsupported_algorithm():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            jwt_secret_key="a" * 40,
        )
    with pytest.raises(ValueError, match="jwt_algorithm"):
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            jwt_secret_key="test-only-minicloud-signing-key-32chars",
            jwt_algorithm="none",
        )


def test_production_configuration_rejects_known_jwt_defaults_and_unbounded_expiry():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://app:secure-password@localhost/minicloud",
            jwt_secret_key="dev-minicloud-jwt-secret-key-2026",
        )
    with pytest.raises(ValueError, match="ACCESS_TOKEN_EXPIRE_MINUTES"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://app:secure-password@localhost/minicloud",
            jwt_secret_key="a-secure-test-only-signing-key-value",
            access_token_expire_minutes=1441,
        )


def test_production_configuration_rejects_sqlite_and_common_passwords():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="sqlite+pysqlite:///./minicloud.db",
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://minicloud_runtime:password@localhost/minicloud",
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )


def test_production_api_rejects_postgres_superuser_but_migration_role_can_use_it():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            service_role="api",
            database_url="postgresql://postgres:unique-strong-password@localhost/minicloud",
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )

    with pytest.raises(ValueError, match="dedicated api database role"):
        Settings(
            _env_file=None,
            app_env="production",
            service_role="api",
            database_url=(
                "postgresql+psycopg://minicloud_worker_runtime:"
                "test-only-runtime-password-123@localhost/minicloud"
            ),
            migration_database_url=None,
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )

    with pytest.raises(ValueError, match="dedicated worker database role"):
        Settings(
            _env_file=None,
            app_env="production",
            service_role="worker",
            database_url=(
                "postgresql+psycopg://minicloud_api_runtime:"
                "test-only-runtime-password-123@localhost/minicloud"
            ),
            migration_database_url=None,
            jwt_secret_key="",
        )

    api_settings = Settings(
        _env_file=None,
        app_env="production",
        service_role="api",
        database_url=(
            "postgresql+psycopg://minicloud_api_runtime:"
            "test-only-runtime-password-123@localhost/minicloud"
        ),
        migration_database_url=None,
        jwt_secret_key="a-secure-test-only-signing-key-value",
    )
    worker_settings = Settings(
        _env_file=None,
        app_env="production",
        service_role="worker",
        database_url=(
            "postgresql+psycopg://minicloud_worker_runtime:"
            "test-only-runtime-password-123@localhost/minicloud"
        ),
        migration_database_url=None,
        jwt_secret_key="",
    )
    assert api_settings.service_role == "api"
    assert worker_settings.service_role == "worker"

    migration_settings = Settings(
        _env_file=None,
        service_role="migrate",
        app_env="development",
        jwt_secret_key="",
        database_url="postgresql://minicloud_runtime:unique-runtime-password@localhost/minicloud",
        migration_database_url="postgresql://postgres:unique-admin-password@localhost/minicloud",
    )
    assert migration_settings.jwt_secret_key is None


def test_production_migration_url_requires_distinct_credentials():
    with pytest.raises(ValueError, match="credentials distinct"):
        Settings(
            _env_file=None,
            app_env="production",
            service_role="migrate",
            jwt_secret_key="",
            database_url="postgresql+psycopg://minicloud_runtime:runtime-test-password-012345@localhost/minicloud",
            migration_database_url="postgresql+psycopg://minicloud_runtime:runtime-test-password-012345@localhost/minicloud",
        )


def test_production_migration_url_rejects_same_role_with_different_passwords():
    with pytest.raises(ValueError, match="credentials distinct"):
        Settings(
            _env_file=None,
            app_env="production",
            service_role="migrate",
            jwt_secret_key="",
            database_url=(
                "postgresql+psycopg://minicloud_runtime_test:"
                "test-only-runtime-password-123@localhost/minicloud"
            ),
            migration_database_url=(
                "postgresql+psycopg://minicloud_runtime_test:"
                "test-only-migration-password-456@localhost/minicloud"
            ),
        )


def test_production_migration_url_accepts_distinct_credentials():
    configured = Settings(
        _env_file=None,
        app_env="production",
        service_role="migrate",
        jwt_secret_key="",
        database_url="postgresql+psycopg://minicloud_runtime:runtime-test-password-012345@localhost/minicloud",
        migration_database_url="postgresql+psycopg://minicloud_migrator:migration-test-password-67890@localhost/minicloud",
    )
    assert configured.service_role == "migrate"


def test_production_configuration_rejects_short_database_password():
    with pytest.raises(ValueError, match="weak database credential"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+psycopg://minicloud_runtime:weakpass@localhost/minicloud",
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )


def test_production_configuration_rejects_missing_secret_without_echoing_values(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secure-password@localhost/minicloud")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError) as error:
        load_settings()

    assert "JWT_SECRET_KEY" in str(error.value)
    assert "secure-password" not in str(error.value)


def test_production_configuration_rejects_placeholder_database_password():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://app:replace-with-password@localhost/minicloud",
            jwt_secret_key="a-secure-test-only-signing-key-value",
        )


def test_fixed_window_rate_limiter_blocks_until_window_expires():
    now = [100.0]
    limiter = FixedWindowRateLimiter(limit=2, window_seconds=10, clock=lambda: now[0])

    assert limiter.consume("client:/login") is None
    assert limiter.consume("client:/login") is None
    assert limiter.consume("client:/login") == 10
    now[0] += 10
    assert limiter.consume("client:/login") is None


def test_authentication_rate_limit_returns_429_and_recovers():
    for _ in range(settings.auth_rate_limit_max_requests):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "invalid-password"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "invalid-password"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert uuid.UUID(limited.headers["X-Request-ID"]).version == 4

    auth_rate_limiter.clear()
    recovered = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "invalid-password"},
    )
    assert recovered.status_code == 401


def test_request_id_is_server_generated_and_returned():
    response = client.get("/", headers={"X-Request-ID": "client-supplied-value"})

    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id).version == 4
    assert request_id != "client-supplied-value"


def test_cors_exposes_request_id_header_to_web_clients():
    origin = settings.cors_origins.split(",")[0].strip()
    response = client.get(
        "/",
        headers={"Origin": origin},
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Expose-Headers"] == "X-Request-ID"
    assert uuid.UUID(response.headers["X-Request-ID"]).version == 4


def test_auth_rate_limit_scopes_direct_client_and_endpoint_not_forwarded_headers():
    first_client = TestClient(app, client=("192.0.2.10", 10001))
    second_client = TestClient(app, client=("192.0.2.11", 10002))

    for _ in range(settings.auth_rate_limit_max_requests):
        response = first_client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "invalid-password"},
            headers={"X-Forwarded-For": "198.51.100.99"},
        )
        assert response.status_code == 401

    assert (
        first_client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "invalid-password"},
        ).status_code
        == 429
    )
    assert (
        second_client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "invalid-password"},
            headers={"X-Forwarded-For": "198.51.100.99"},
        ).status_code
        == 401
    )
    assert first_client.post("/api/v1/auth/register", json={}).status_code == 422
    assert first_client.post("/api/v1/auth/logout").status_code == 401


def test_validation_errors_do_not_echo_submitted_password():
    secret_marker = "sensitive-password-marker-for-regression"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "x", "password": secret_marker},
    )

    assert response.status_code == 422
    assert secret_marker not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_logout_revokes_only_presented_token():
    first = client.post(
        "/api/v1/auth/register",
        json={"email": "logout-one@example.com", "password": "secret1234"},
    ).json()["access_token"]
    second = client.post(
        "/api/v1/auth/register",
        json={"email": "logout-two@example.com", "password": "secret1234"},
    ).json()["access_token"]

    assert (
        client.get("/api/v1/projects", headers={"Authorization": f"Bearer {first}"}).status_code
        == 200
    )
    logged_out = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {first}"})
    assert logged_out.status_code == 200
    assert logged_out.json() == {"logged_out": True}
    assert (
        client.get("/api/v1/projects", headers={"Authorization": f"Bearer {first}"}).status_code
        == 401
    )
    assert (
        client.get("/api/v1/projects", headers={"Authorization": f"Bearer {second}"}).status_code
        == 200
    )

    with TestingSessionLocal() as db:
        record = db.query(TokenRevocation).one()
        assert record.token_hash != first
        assert len(record.token_hash) == 64


def test_expired_revocation_cleanup_preserves_unexpired_records():
    expired_hash = "e" * 64
    unexpired_hash = "u" * 64
    with TestingSessionLocal() as db:
        db.add_all(
            [
                TokenRevocation(
                    token_hash=expired_hash,
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                ),
                TokenRevocation(
                    token_hash=unexpired_hash,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        )
        db.commit()

        _delete_expired_token_revocations(db)
        db.commit()

        remaining = {record.token_hash for record in db.query(TokenRevocation).all()}
        assert expired_hash not in remaining
        assert unexpired_hash in remaining

        db.query(TokenRevocation).filter(TokenRevocation.token_hash == unexpired_hash).delete()
        db.commit()


def test_postgresql_revocation_cleanup_calls_database_function(monkeypatch):
    statements = []

    with TestingSessionLocal() as db:
        monkeypatch.setattr(
            db,
            "get_bind",
            lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        )
        monkeypatch.setattr(
            db,
            "execute",
            lambda statement: statements.append(str(statement)),
        )

        _delete_expired_token_revocations(db)

    assert len(statements) == 1
    assert "public.minicloud_cleanup_expired_token_revocations()" in statements[0]


def test_logout_cleanup_database_failure_returns_server_error():
    token = client.post(
        "/api/v1/auth/register",
        json={"email": "logout-cleanup-failure@example.com", "password": "secret1234"},
    ).json()["access_token"]

    def fail_revocation_cleanup(*execution):
        statement = str(execution[2]).lstrip().upper()
        if statement.startswith("DELETE FROM TOKEN_REVOCATIONS"):
            raise OperationalError(
                "revocation cleanup failed",
                {},
                RuntimeError("database unavailable"),
            )

    isolated_client = TestClient(app, raise_server_exceptions=False)
    event.listen(engine, "before_cursor_execute", fail_revocation_cleanup)
    try:
        response = isolated_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_revocation_cleanup)

    assert response.status_code == 503
    assert "database unavailable" not in response.text


def test_revocation_storage_failure_fails_closed(caplog):
    token = client.post(
        "/api/v1/auth/register",
        json={"email": "revocation-failure@example.com", "password": "secret1234"},
    ).json()["access_token"]

    class UnavailableSession:
        def query(self, model):
            raise OperationalError(
                "revocation lookup failed", {}, RuntimeError("database unavailable")
            )

    def unavailable_db():
        yield UnavailableSession()

    app.dependency_overrides[get_db] = unavailable_db
    try:
        isolated_client = TestClient(app, raise_server_exceptions=False)
        response = isolated_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    request_id = response.headers["X-Request-ID"]
    assert f"request_id={request_id}" in caplog.text


def test_unexpected_error_is_sanitized_and_correlated(caplog):
    secret_marker = "unexpected-internal-error-marker"

    def fail_db_dependency():
        raise RuntimeError(secret_marker)

    app.dependency_overrides[get_db] = fail_db_dependency
    try:
        isolated_client = TestClient(app, raise_server_exceptions=False)
        response = isolated_client.get("/readyz")
    finally:
        app.dependency_overrides[get_db] = override_get_db

    request_id = response.headers["X-Request-ID"]
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert uuid.UUID(request_id).version == 4
    assert secret_marker not in response.text
    assert f"request_id={request_id}" in caplog.text
    assert secret_marker not in caplog.text


def test_readiness_returns_unready_without_logging_database_details(caplog):
    secret_marker = "DATABASE_PASSWORD_SECRET_MARKER"

    def fail_readiness(*execution):
        if str(execution[2]).strip().upper() == "SELECT 1":
            raise OperationalError(
                "readiness failed",
                {},
                RuntimeError(secret_marker),
            )

    event.listen(engine, "before_cursor_execute", fail_readiness)
    try:
        response = client.get("/readyz")
    finally:
        event.remove(engine, "before_cursor_execute", fail_readiness)

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": False}
    assert secret_marker not in response.text
    assert secret_marker not in caplog.text


def test_capabilities_reports_unavailable_optional_providers_truthfully():
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "capabilities@example.com", "password": "secret1234"},
    )
    token = registration.json()["access_token"]
    response = client.get(
        "/api/v1/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "metrics": {
            "available": False,
            "status": "unavailable",
            "reason": "No metrics provider is configured.",
        },
        "alerts": {
            "available": False,
            "status": "unavailable",
            "reason": "Alert delivery is not implemented.",
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Bad Name", "image": "nginx:latest"},
        {"name": "app", "image": "nginx:latest && whoami"},
        {"name": "app", "image": "nginx:latest", "manifest": "kind: Pod"},
    ],
)
def test_deployment_schema_rejects_invalid_names_images_and_untyped_fields(payload):
    with pytest.raises(ValueError):
        DeploymentCreate.model_validate(payload)


def _new_worker_sessionmaker(monkeypatch):
    worker_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=worker_engine)
    worker_session_local = sessionmaker(autocommit=False, autoflush=False, bind=worker_engine)
    monkeypatch.setattr("services.worker.main.SessionLocal", worker_session_local)
    return worker_session_local


def _create_worker_job(name: str, session_local) -> tuple[str, str]:
    db = session_local()
    user = User(email=f"{name}@example.com", password_hash="hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    project = Project(name=name, slug=name, owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role="owner"))
    deployment = Deployment(
        project_id=project.id,
        owner_id=user.id,
        name="test-app",
        image="nginx:latest",
        port=8080,
        replicas=1,
        status="accepted",
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    job = DeploymentJob(
        deployment_id=deployment.id,
        project_id=project.id,
        state="queued",
        attempts=0,
        max_attempts=3,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    result = deployment.id, job.id
    db.close()
    return result


def test_worker_only_marks_ready_after_observation_reports_all_replicas_ready(monkeypatch, caplog):
    caplog.set_level("INFO", logger="services.worker.main")
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-readiness", session_local)
    request_id = str(uuid.uuid4())
    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    job.request_id = request_id
    db.commit()
    db.close()

    class FakeAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0
            self.ready = False

        def apply(self, deployment):
            self.apply_calls += 1

        def observe(self, deployment):
            if self.ready:
                return DeploymentObservation(True, "Observed 1/1 ready replicas.")
            return DeploymentObservation(False, "Waiting for ready replicas (0/1).")

    adapter = FakeAdapter()
    process_jobs(adapter=adapter)

    db = session_local()
    first_state = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    applying_events = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type.in_(["applying", "progressing"]),
        )
        .all()
    )
    assert first_state.status == "progressing"
    assert adapter.apply_calls == 1
    assert applying_events
    assert all(event.request_id == request_id for event in applying_events)
    db.close()

    adapter.ready = True
    process_jobs(adapter=adapter)

    db = session_local()
    final_state = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert final_state.status == "ready"
    assert final_job.state == "completed"
    ready_event = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "ready",
        )
        .one()
    )
    assert ready_event.request_id == request_id
    assert f"request_id={request_id}" in caplog.text
    db.close()


def test_worker_reconciles_scale_job_until_requested_replicas_are_observed(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-scale", session_local)
    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment.replicas = 3
    job.operation_type = "scale"
    job.target_replicas = 3
    job.request_id = "scale-request-worker-test"
    db.commit()
    db.close()

    class ScaleAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.applied_replicas = None

        def apply(self, target):
            self.applied_replicas = target.replicas

        def observe(self, target):
            assert target.replicas == 3
            return DeploymentObservation(True, "Observed 3/3 ready replicas.")

    adapter = ScaleAdapter()
    process_jobs(adapter=adapter)

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    events = (
        db.query(DeploymentEvent)
        .filter(DeploymentEvent.deployment_id == deployment_id)
        .order_by(DeploymentEvent.created_at.asc())
        .all()
    )
    assert adapter.applied_replicas == 3
    assert final_deployment.replicas == 3
    assert final_deployment.status == "ready"
    assert final_job.state == "completed"
    assert any(event.message == "Applying the requested scale to 3 replicas." for event in events)
    assert any(event.event_type == "ready" for event in events)
    worker_events = [event for event in events if event.event_type in {"applying", "ready"}]
    assert worker_events
    assert all(event.request_id == "scale-request-worker-test" for event in worker_events)
    db.close()


def test_worker_rollout_start_prefers_durable_job_timestamp():
    job = DeploymentJob(
        deployment_id="deployment-id",
        project_id="project-id",
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        started_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    deployment = Deployment(
        id="deployment-id",
        project_id="project-id",
        owner_id="owner-id",
        name="web",
        image="nginx:1.27",
        port=8080,
        replicas=1,
        updated_at=datetime(2026, 11, 4, tzinfo=UTC),
    )

    assert _rollout_started_at(job, deployment) == job.started_at


def test_worker_timeout_deadline_does_not_reset_when_observation_message_changes(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-stable-rollout-deadline", session_local)

    class ChangingObservationAdapter:
        rollout_timeout_seconds = 1

        def __init__(self):
            self.observations = 0

        def apply(self, deployment):
            pass

        def observe(self, deployment):
            self.observations += 1
            return DeploymentObservation(
                False,
                f"Waiting for ready replicas ({self.observations}/3).",
            )

    adapter = ChangingObservationAdapter()
    process_jobs(adapter=adapter)

    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    rollout_started_at = datetime.now(UTC) - timedelta(seconds=5)
    job.started_at = rollout_started_at
    deployment.updated_at = datetime.now(UTC)
    db.commit()
    db.close()

    process_jobs(adapter=adapter)

    db = session_local()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    assert adapter.observations == 2
    assert final_job.state == "timed_out"
    assert final_job.started_at.replace(tzinfo=UTC) == rollout_started_at
    assert final_deployment.status == "timed_out"
    db.close()


def test_worker_rollout_timeout_starts_at_current_attempt_not_old_deployment_creation(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-old-deployment-retry", session_local)
    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment.created_at = datetime.now(UTC) - timedelta(days=2)
    job.updated_at = datetime.now(UTC) - timedelta(seconds=5)
    db.commit()
    db.close()

    class PendingAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pass

        def observe(self, deployment):
            return DeploymentObservation(False, "Waiting for ready replicas (0/1).")

    process_jobs(adapter=PendingAdapter())

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    attempt_events = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "applying",
        )
        .all()
    )
    assert final_deployment.status == "progressing"
    assert final_job.state == "progressing"
    assert final_job.attempts == 0
    assert len(attempt_events) == 1
    db.close()


def test_worker_reconciles_late_readiness_after_rollout_timeout(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-expired-rollout-attempt", session_local)
    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment.status = "progressing"
    deployment.updated_at = datetime.now(UTC) - timedelta(minutes=30)
    job.state = "progressing"
    job.started_at = datetime.now(UTC) - timedelta(minutes=30)
    db.commit()
    db.close()

    class PendingAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pytest.fail("A progressing rollout must not reapply its workload.")

        def observe(self, deployment):
            return DeploymentObservation(False, "Waiting for ready replicas (0/1).")

    process_jobs(adapter=PendingAdapter())

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert final_deployment.status == "timed_out"
    assert final_deployment.last_error == (
        "Kubernetes rollout exceeded MiniCloud's configured readiness timeout."
    )
    assert final_job.state == "timed_out"
    assert final_job.attempts == 0
    timeout_events = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "timed_out",
        )
        .count()
    )
    assert timeout_events == 1
    db.close()

    def worker_db_override():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    original_db_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = worker_db_override
    try:
        db = session_local()
        deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
        user = db.query(User).filter(User.id == deployment.owner_id).one()
        token = create_access_token(user.email)
        db.close()
        status_response = client.get(
            f"/api/v1/operations/{deployment_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "timed_out"
        assert status_response.json()["job_state"] == "timed_out"
    finally:
        app.dependency_overrides[get_db] = original_db_override

    class TemporarilyUnavailableAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pytest.fail("A timed-out rollout must observe, not recreate, its workload.")

        def observe(self, deployment):
            raise KubernetesReconcileError(
                "Kubernetes temporarily failed to observe the Deployment (HTTP 503).",
                retryable=True,
            )

    process_jobs(adapter=TemporarilyUnavailableAdapter())

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert final_deployment.status == "timed_out"
    assert final_job.state == "timed_out"
    assert final_job.attempts == 0
    db.close()

    class LateReadyAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pytest.fail("A timed-out rollout must observe, not recreate, its workload.")

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    process_jobs(adapter=LateReadyAdapter())

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    event_types = [
        event.event_type
        for event in db.query(DeploymentEvent)
        .filter(DeploymentEvent.deployment_id == deployment_id)
        .order_by(DeploymentEvent.created_at, DeploymentEvent.id)
    ]
    assert final_deployment.status == "ready"
    assert final_deployment.last_error is None
    assert final_job.state == "completed"
    assert event_types.count("timed_out") == 1
    assert "recovered_after_timeout" in event_types
    assert event_types.count("ready") == 1
    db.close()


def test_worker_preserves_timed_out_state_when_kubernetes_configuration_is_unavailable(
    monkeypatch,
):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-timeout-config-outage", session_local)
    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment.status = "timed_out"
    deployment.last_error = "Kubernetes rollout exceeded MiniCloud's configured readiness timeout."
    job.state = "timed_out"
    job.last_error = deployment.last_error
    db.commit()
    db.close()

    monkeypatch.setattr(
        "services.worker.main.build_kubernetes_adapter",
        lambda *_: (_ for _ in ()).throw(
            KubernetesConfigurationError("Kubernetes configuration unavailable.")
        ),
    )
    process_jobs()

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    unavailable_events = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "reconciliation_unavailable",
        )
        .count()
    )
    assert final_deployment.status == "timed_out"
    assert final_job.state == "timed_out"
    assert final_job.lease_expires_at is None
    assert unavailable_events == 1
    db.close()


def test_worker_retries_transient_apply_error_with_bounded_attempts(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-retry", session_local)

    class FlakyAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0

        def apply(self, deployment):
            self.apply_calls += 1
            if self.apply_calls == 1:
                raise KubernetesReconcileError(
                    "Kubernetes temporarily failed (HTTP 503).", retryable=True
                )

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    adapter = FlakyAdapter()
    process_jobs(adapter=adapter)

    db = session_local()
    retried = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert retried.state == "queued"
    assert retried.attempts == 1
    assert retried.last_error == "Kubernetes temporarily failed (HTTP 503)."
    db.close()

    process_jobs(adapter=adapter)
    assert adapter.apply_calls == 1

    db = session_local()
    retried = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    deployment.updated_at = datetime.now(UTC) - timedelta(seconds=10)
    db.commit()
    db.close()
    process_jobs(adapter=adapter)

    db = session_local()
    final_state = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert final_state.status == "ready"
    assert final_job.state == "completed"
    assert adapter.apply_calls == 2
    db.close()


def test_worker_stops_after_retry_budget_is_exhausted(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-retry-exhaustion", session_local)

    class AlwaysFlakyAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0

        def apply(self, deployment):
            self.apply_calls += 1
            raise KubernetesReconcileError(
                "Kubernetes temporarily failed (HTTP 503).", retryable=True
            )

        def observe(self, deployment):
            pytest.fail("A deployment should not be observed after apply failed.")

    adapter = AlwaysFlakyAdapter()
    for _ in range(3):
        db = session_local()
        deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
        deployment.updated_at = datetime.now(UTC) - timedelta(minutes=2)
        db.commit()
        db.close()
        process_jobs(adapter=adapter)

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert adapter.apply_calls == 3
    assert final_deployment.status == "failed"
    assert final_job.state == "failed"
    assert final_job.attempts == final_job.max_attempts
    assert "Retry limit reached." in final_deployment.last_error
    db.close()


def test_worker_persists_terminal_rollout_failure(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-terminal-rollout", session_local)

    class FailedRolloutAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pass

        def observe(self, deployment):
            return DeploymentObservation(
                False,
                "Kubernetes reported that the rollout exceeded its progress deadline.",
                terminal=True,
            )

    process_jobs(adapter=FailedRolloutAdapter())

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert final_deployment.status == "failed"
    assert (
        final_deployment.last_error
        == "Kubernetes reported that the rollout exceeded its progress deadline."
    )
    assert final_job.state == "failed"
    assert final_job.attempts == 0
    db.close()


def test_worker_pauses_on_authentication_failure_and_recovers_without_retry_budget(
    monkeypatch,
    tmp_path,
    caplog,
):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-auth-recovery", session_local)
    status_path = tmp_path / "auth-blocked"
    monkeypatch.setenv("WORKER_AUTH_STATUS_FILE", str(status_path))
    secret_value = "must-never-appear-in-logs"

    class ExpiredCredentialAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            raise KubernetesAuthenticationError(
                "Kubernetes authentication failed for the create Deployment request "
                "(HTTP 401); renew the worker credential.",
                retryable=False,
            )

        def observe(self, deployment):
            pytest.fail("A rejected credential must not trigger readiness observation.")

    process_jobs(adapter=ExpiredCredentialAdapter())

    db = session_local()
    paused_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    paused_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert paused_deployment.status == "progressing"
    assert "HTTP 401" in paused_deployment.last_error
    assert paused_job.state == "queued"
    assert paused_job.attempts == 0
    assert paused_job.lease_expires_at is None
    assert status_path.exists()
    db.close()
    assert secret_value not in caplog.text

    class RenewedCredentialAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pass

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    db = session_local()
    paused_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    paused_deployment.updated_at = datetime.now(UTC) - timedelta(seconds=60)
    db.commit()
    db.close()
    process_jobs(adapter=RenewedCredentialAdapter())

    db = session_local()
    recovered_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    recovered_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    events = db.query(DeploymentEvent).filter(DeploymentEvent.deployment_id == deployment_id).all()
    assert recovered_deployment.status == "ready"
    assert recovered_job.state == "completed"
    assert recovered_job.attempts == 0
    assert not status_path.exists()
    assert any(event.event_type == "authentication_recovered" for event in events)
    db.close()


def test_credential_probe_pauses_before_claiming_jobs(monkeypatch, tmp_path):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-auth-probe", session_local)
    status_path = tmp_path / "auth-blocked"
    monkeypatch.setenv("WORKER_AUTH_STATUS_FILE", str(status_path))

    class RejectedCredentialAdapter:
        def verify_credentials(self):
            raise KubernetesAuthenticationError(
                "Kubernetes authentication failed for the credential check request (HTTP 401).",
                retryable=False,
            )

    monkeypatch.setattr(
        "services.worker.main.build_kubernetes_adapter",
        lambda *_: RejectedCredentialAdapter(),
    )
    process_jobs()

    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert deployment.status == "progressing"
    assert "HTTP 401" in deployment.last_error
    assert job.state == "queued"
    assert job.attempts == 0
    assert job.lease_expires_at is None
    assert status_path.exists()
    db.close()


def test_transient_credential_probe_failure_does_not_consume_job_retries(monkeypatch, tmp_path):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-probe-outage", session_local)
    status_path = tmp_path / "auth-blocked"
    monkeypatch.setenv("WORKER_AUTH_STATUS_FILE", str(status_path))

    class UnavailableApiAdapter:
        def verify_credentials(self):
            raise KubernetesReconcileError(
                "Kubernetes temporarily failed the credential check request (HTTP 503).",
                retryable=True,
            )

    monkeypatch.setattr(
        "services.worker.main.build_kubernetes_adapter",
        lambda *_: UnavailableApiAdapter(),
    )
    process_jobs()

    db = session_local()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert deployment.status == "accepted"
    assert job.state == "queued"
    assert job.attempts == 0
    assert job.lease_expires_at is None
    assert not status_path.exists()
    db.close()


def test_worker_does_not_reapply_a_completed_job(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-completed-idempotency", session_local)

    class ReadyAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0

        def apply(self, deployment):
            self.apply_calls += 1

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    adapter = ReadyAdapter()
    process_jobs(adapter=adapter)
    process_jobs(adapter=adapter)

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert adapter.apply_calls == 1
    assert final_deployment.status == "ready"
    assert final_job.state == "completed"
    db.close()


def test_api_status_matches_persisted_worker_completion(monkeypatch):
    monkeypatch.setattr("services.worker.main.SessionLocal", TestingSessionLocal)
    suffix = uuid.uuid4().hex
    email = f"worker-status-{suffix}@example.com"
    password = "status-test-password"
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project_response = client.post(
        "/api/v1/projects",
        json={"name": f"Worker status {suffix}", "slug": f"worker-status-{suffix}"},
        headers=headers,
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]
    deployment_response = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={
            "name": "worker-status",
            "image": "nginxinc/nginx-unprivileged:1.27-alpine",
            "port": 8080,
            "replicas": 1,
            "idempotency_key": f"worker-status-{suffix}",
        },
        headers=headers,
    )
    assert deployment_response.status_code == 202
    deployment_id = deployment_response.json()["deployment_id"]

    class ReadyAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pass

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    process_jobs(adapter=ReadyAdapter())

    operation_response = client.get(
        f"/api/v1/operations/{deployment_id}",
        headers=headers,
    )
    assert operation_response.status_code == 200
    assert operation_response.json() == {
        "deployment_id": deployment_id,
        "kubernetes_resource_name": deployment_resource_name(deployment_id),
        "revision_id": deployment_response.json()["revision_id"],
        "job_state": "completed",
        "status": "ready",
        "last_error": None,
    }
    db = TestingSessionLocal()
    job = db.query(DeploymentJob).filter(DeploymentJob.deployment_id == deployment_id).one()
    assert job.state == "completed"
    db.close()


def test_worker_recovers_an_expired_processing_lease(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-lease-recovery", session_local)

    class ReadyAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0

        def apply(self, deployment):
            self.apply_calls += 1

        def observe(self, deployment):
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    adapter = ReadyAdapter()
    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    job.state = "applying"
    job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db.commit()
    db.close()

    process_jobs(adapter=adapter)
    assert adapter.apply_calls == 0

    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    db.close()
    process_jobs(adapter=adapter)

    db = session_local()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert adapter.apply_calls == 1
    assert final_deployment.status == "ready"
    assert final_job.state == "completed"
    assert final_job.lease_expires_at is None
    db.close()


def test_worker_restart_after_apply_reuses_the_existing_resource(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-crash-after-apply", session_local)

    class WorkerCrashAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.resource_exists = False
            self.apply_attempts = 0
            self.created_resources = 0

        def apply(self, deployment):
            self.apply_attempts += 1
            if not self.resource_exists:
                self.resource_exists = True
                self.created_resources += 1
                raise RuntimeError("simulated worker termination after resource creation")

        def observe(self, deployment):
            assert self.resource_exists
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    adapter = WorkerCrashAdapter()
    with pytest.raises(RuntimeError, match="simulated worker termination"):
        process_jobs(adapter=adapter)

    db = session_local()
    interrupted_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert interrupted_job.state == "applying"
    assert interrupted_job.started_at is not None
    interrupted_job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    db.close()

    process_jobs(adapter=adapter)

    db = session_local()
    recovered_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    recovered_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    assert adapter.apply_attempts == 2
    assert adapter.created_resources == 1
    assert recovered_job.state == "completed"
    assert recovered_deployment.status == "ready"
    db.close()


def test_worker_does_not_overwrite_a_terminal_operation(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-terminal-state-guard", session_local)
    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    job.state = "completed"
    job.last_error = None
    deployment.status = "ready"
    deployment.last_error = None
    db.commit()
    event_count = (
        db.query(DeploymentEvent).filter(DeploymentEvent.deployment_id == deployment_id).count()
    )
    db.close()

    class UnexpectedReconcileAdapter:
        rollout_timeout_seconds = 600

        def apply(self, deployment):
            pytest.fail("A terminal operation must not be applied again.")

        def observe(self, deployment):
            pytest.fail("A terminal operation must not be observed again.")

    process_jobs(adapter=UnexpectedReconcileAdapter())

    db = session_local()
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    assert job.state == "completed"
    assert deployment.status == "ready"
    assert (
        db.query(DeploymentEvent).filter(DeploymentEvent.deployment_id == deployment_id).count()
        == event_count
    )
    db.close()


def test_stale_worker_cannot_process_or_finalize_after_lease_is_reclaimed(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-stale-lease-fencing", session_local)
    first_claim_time = datetime.now(UTC)

    db = session_local()
    first_claim = _claim_jobs(db, first_claim_time)
    assert [claimed_id for claimed_id, _ in first_claim] == [job_id]
    stale_lease = first_claim[0][1]
    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    job.lease_expires_at = first_claim_time - timedelta(seconds=1)
    db.commit()

    second_claim_time = first_claim_time + timedelta(minutes=6)
    current_claim = _claim_jobs(db, second_claim_time)
    assert [claimed_id for claimed_id, _ in current_claim] == [job_id]
    current_lease = current_claim[0][1]
    assert current_lease > stale_lease

    class ReadyAdapter:
        rollout_timeout_seconds = 600

        def __init__(self):
            self.apply_calls = 0
            self.observe_calls = 0

        def apply(self, deployment):
            self.apply_calls += 1

        def observe(self, deployment):
            self.observe_calls += 1
            return DeploymentObservation(True, "Observed 1/1 ready replicas.")

    adapter = ReadyAdapter()
    _process_job(db, job_id, stale_lease, adapter, second_claim_time)

    job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    assert adapter.apply_calls == 0
    assert adapter.observe_calls == 0
    assert job.lease_expires_at.replace(tzinfo=UTC) == current_lease
    assert job.state == "queued"
    assert deployment.status == "accepted"

    _process_job(db, job_id, current_lease, adapter, second_claim_time)
    _process_job(db, job_id, current_lease, adapter, second_claim_time)

    final_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    final_deployment = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    ready_events = (
        db.query(DeploymentEvent)
        .filter(
            DeploymentEvent.deployment_id == deployment_id,
            DeploymentEvent.event_type == "ready",
        )
        .count()
    )
    assert adapter.apply_calls == 1
    assert adapter.observe_calls == 1
    assert final_job.state == "completed"
    assert final_job.lease_expires_at is None
    assert final_deployment.status == "ready"
    assert ready_events == 1
    db.close()


def test_job_claims_respect_leases_and_recover_after_expiration(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    _, job_id = _create_worker_job("worker-claim-lease", session_local)
    now = datetime.now(UTC)

    db = session_local()
    first_claim = _claim_jobs(db, now)
    assert [claimed_id for claimed_id, _ in first_claim] == [job_id]
    db.close()

    db = session_local()
    assert _claim_jobs(db, now + timedelta(seconds=1)) == []
    db.close()

    db = session_local()
    recovered_claim = _claim_jobs(db, now + timedelta(minutes=6))
    assert [claimed_id for claimed_id, _ in recovered_claim] == [job_id]
    assert recovered_claim[0][1] > first_claim[0][1]
    db.close()


def test_worker_marks_failed_when_cluster_context_is_unavailable(monkeypatch):
    session_local = _new_worker_sessionmaker(monkeypatch)
    deployment_id, job_id = _create_worker_job("worker-no-cluster", session_local)
    monkeypatch.setattr(
        "services.worker.main.build_kubernetes_adapter",
        lambda *_: (_ for _ in ()).throw(
            KubernetesConfigurationError(
                "Set KUBERNETES_CONFIG_FILE and KUBERNETES_CONTEXT for local Kubernetes access."
            )
        ),
    )
    process_jobs()

    db = session_local()
    refreshed = db.query(Deployment).filter(Deployment.id == deployment_id).one()
    refreshed_job = db.query(DeploymentJob).filter(DeploymentJob.id == job_id).one()
    assert refreshed.status == "failed"
    assert "KUBERNETES_CONFIG_FILE" in refreshed.last_error
    assert refreshed_job.state == "failed"
    db.close()


def test_project_membership_requires_owner_and_preserves_a_last_owner():
    suffix = uuid.uuid4().hex
    owner = client.post(
        "/api/v1/auth/register",
        json={"email": f"owner-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    member = client.post(
        "/api/v1/auth/register",
        json={"email": f"member-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    member_headers = {"Authorization": f"Bearer {member['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Membership project", "slug": f"membership-{suffix}"},
        headers=owner_headers,
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    members_path = f"/api/v1/projects/{project_id}/members"

    hidden_members = client.get(members_path, headers=member_headers)
    missing_members = client.get(
        "/api/v1/projects/nonexistent-project/members", headers=member_headers
    )
    assert hidden_members.status_code == missing_members.status_code == 404
    assert hidden_members.json() == missing_members.json()
    added = client.post(
        members_path,
        json={"email": f" MEMBER-{suffix}@EXAMPLE.COM ", "role": "member"},
        headers=owner_headers,
    )
    assert added.status_code == 201, added.text
    assert added.json() == {
        "user_id": member["user"]["id"],
        "email": f"member-{suffix}@example.com",
        "role": "member",
    }
    assert (
        client.post(
            members_path,
            json={"email": f"member-{suffix}@example.com"},
            headers=owner_headers,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"{members_path}/{member['user']['id']}",
            json={"role": "owner"},
            headers=member_headers,
        ).status_code
        == 403
    )

    promoted = client.patch(
        f"{members_path}/{member['user']['id']}",
        json={"role": "owner"},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "owner"
    assert (
        client.delete(
            f"{members_path}/{owner['user']['id']}",
            headers=owner_headers,
        ).status_code
        == 204
    )

    project_after_transfer = client.get(
        f"/api/v1/projects/{project_id}",
        headers=member_headers,
    )
    assert project_after_transfer.status_code == 200
    assert project_after_transfer.json()["owner_id"] == member["user"]["id"]
    assert (
        client.patch(
            f"{members_path}/{member['user']['id']}",
            json={"role": "member"},
            headers=member_headers,
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"{members_path}/{member['user']['id']}",
            headers=member_headers,
        ).status_code
        == 409
    )


def test_project_member_changes_are_hidden_from_non_members():
    suffix = uuid.uuid4().hex
    owner = client.post(
        "/api/v1/auth/register",
        json={"email": f"private-owner-{suffix}@example.com", "password": "secure-test-password"},
    ).json()
    stranger = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"private-stranger-{suffix}@example.com",
            "password": "secure-test-password",
        },
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    stranger_headers = {"Authorization": f"Bearer {stranger['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Private membership project", "slug": f"private-members-{suffix}"},
        headers=owner_headers,
    ).json()
    path = f"/api/v1/projects/{project['id']}/members"

    hidden_members = client.get(path, headers=stranger_headers)
    missing_project_members = client.get(
        "/api/v1/projects/nonexistent-project/members", headers=stranger_headers
    )
    assert hidden_members.status_code == missing_project_members.status_code == 404
    assert hidden_members.json() == missing_project_members.json()
    assert (
        client.post(
            path,
            json={"email": f"private-stranger-{suffix}@example.com"},
            headers=stranger_headers,
        ).status_code
        == 403
    )


def test_project_membership_route_authorization_matrix():
    suffix = uuid.uuid4().hex
    accounts = {}
    for role in ("owner", "member", "stranger"):
        accounts[role] = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"matrix-{role}-{suffix}@example.com",
                "password": "secure-test-password",
            },
        ).json()

    headers = {
        role: {"Authorization": f"Bearer {account['access_token']}"}
        for role, account in accounts.items()
    }
    project = client.post(
        "/api/v1/projects",
        json={"name": "Membership matrix", "slug": f"membership-matrix-{suffix}"},
        headers=headers["owner"],
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    updated_project = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Updated membership matrix"},
        headers=headers["owner"],
    )
    assert updated_project.status_code == 200
    repeated_update = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Updated membership matrix"},
        headers=headers["owner"],
    )
    assert repeated_update.status_code == 200
    members_path = f"/api/v1/projects/{project.json()['id']}/members"
    member_id = accounts["member"]["user"]["id"]
    stranger_id = accounts["stranger"]["user"]["id"]

    added_member = client.post(
        members_path,
        json={"email": accounts["member"]["user"]["email"]},
        headers=headers["owner"],
    )
    assert added_member.status_code == 201, added_member.text

    assert client.get(members_path).status_code == 401
    assert client.get(members_path, headers=headers["owner"]).status_code == 200
    assert client.get(members_path, headers=headers["member"]).status_code == 200
    hidden_members = client.get(members_path, headers=headers["stranger"])
    missing_members = client.get(
        "/api/v1/projects/nonexistent-project/members",
        headers=headers["stranger"],
    )
    assert hidden_members.status_code == missing_members.status_code == 404
    assert hidden_members.json() == missing_members.json()

    stranger_path = f"{members_path}/{stranger_id}"
    add_payload = {"email": accounts["stranger"]["user"]["email"]}
    assert client.post(members_path, json=add_payload).status_code == 401
    assert client.post(members_path, json=add_payload, headers=headers["member"]).status_code == 403
    assert (
        client.post(members_path, json=add_payload, headers=headers["stranger"]).status_code == 403
    )
    role_payload = {"role": "owner"}
    assert client.patch(stranger_path, json=role_payload).status_code == 401
    assert (
        client.patch(stranger_path, json=role_payload, headers=headers["member"]).status_code == 403
    )
    assert (
        client.patch(stranger_path, json=role_payload, headers=headers["stranger"]).status_code
        == 403
    )
    assert client.delete(stranger_path).status_code == 401
    assert client.delete(stranger_path, headers=headers["member"]).status_code == 403
    assert client.delete(stranger_path, headers=headers["stranger"]).status_code == 403

    added_stranger = client.post(members_path, json=add_payload, headers=headers["owner"])
    assert added_stranger.status_code == 201, added_stranger.text

    promoted = client.patch(stranger_path, json=role_payload, headers=headers["owner"])
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "owner"
    assert (
        client.patch(
            stranger_path,
            json={"role": "administrator"},
            headers=headers["owner"],
        ).status_code
        == 422
    )

    assert client.delete(stranger_path, headers=headers["member"]).status_code == 403
    assert client.delete(stranger_path, headers=headers["owner"]).status_code == 204

    missing_member_path = f"{members_path}/missing-user"
    assert (
        client.patch(
            missing_member_path,
            json={"role": "member"},
            headers=headers["owner"],
        ).status_code
        == 404
    )
    assert client.delete(missing_member_path, headers=headers["owner"]).status_code == 404

    remaining_members = client.get(members_path, headers=headers["owner"])
    assert remaining_members.status_code == 200
    assert {item["user_id"] for item in remaining_members.json()} == {
        accounts["owner"]["user"]["id"],
        member_id,
    }
    with TestingSessionLocal() as db:
        audit_actions = [
            event.action
            for event in db.query(AuditEvent)
            .filter(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.id)
            .all()
        ]
    assert audit_actions.count("project.created") == 1
    assert audit_actions.count("project.updated") == 1
    assert audit_actions.count("project.member_added") == 2
    assert audit_actions.count("project.member_role_updated") == 1
    assert audit_actions.count("project.member_removed") == 1


def _create_diagnostics_deployment() -> tuple[dict[str, str], str, str]:
    suffix = uuid.uuid4().hex
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": f"diagnostics-{suffix}@example.com", "password": "secret1234"},
    )
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects",
        json={"name": "Diagnostics project", "slug": f"diag-{suffix}"},
        headers=headers,
    )
    deployment = client.post(
        f"/api/v1/projects/{project.json()['id']}/deployments",
        json={"name": "web", "image": "nginx:1.27", "port": 80, "replicas": 1},
        headers=headers,
    )
    return headers, project.json()["id"], deployment.json()["deployment_id"]


def test_kubernetes_logs_are_member_scoped_bounded_and_redacted(monkeypatch):
    headers, project_id, deployment_id = _create_diagnostics_deployment()
    core_api = Mock()
    pod = kubernetes_client.V1Pod(
        metadata=kubernetes_client.V1ObjectMeta(
            name="web-abc",
            namespace="minicloud",
            uid="pod-uid",
            labels={
                PROJECT_ID_LABEL: project_id,
                DEPLOYMENT_ID_LABEL: deployment_id,
            },
        ),
        spec=kubernetes_client.V1PodSpec(containers=[kubernetes_client.V1Container(name="app")]),
    )
    core_api.list_namespaced_pod.return_value = kubernetes_client.V1PodList(
        metadata=kubernetes_client.V1ListMeta(),
        items=[pod],
    )
    core_api.read_namespaced_pod_log.return_value = (
        "2026-11-13T00:00:00Z password=hunter2\n"
        "2026-11-13T00:00:01Z Authorization: Bearer abc.def.ghi\n"
    )
    adapter = KubernetesDiagnosticsAdapter(core_api, "minicloud")
    monkeypatch.setattr(
        "app.routers.deployments.get_kubernetes_diagnostics_adapter",
        lambda: adapter,
    )

    response = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/logs"
        "?since_seconds=60&tail_lines=12",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["pod_name"] == "web-abc"
    assert "hunter2" not in body["items"][0]["log"]
    assert "abc.def.ghi" not in body["items"][0]["log"]
    assert body["items"][0]["truncated"] is False
    core_api.read_namespaced_pod_log.assert_called_once_with(
        "web-abc",
        "minicloud",
        container="app",
        since_seconds=60,
        tail_lines=12,
        timestamps=True,
        limit_bytes=MAX_LOG_BYTES_PER_POD,
    )


def test_kubernetes_diagnostics_do_not_initialize_for_non_members(monkeypatch):
    headers, project_id, deployment_id = _create_diagnostics_deployment()
    stranger = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"diagnostics-stranger-{uuid.uuid4().hex}@example.com",
            "password": "secret1234",
        },
    )
    monkeypatch.setattr(
        "app.routers.deployments.get_kubernetes_diagnostics_adapter",
        lambda: pytest.fail("Unauthorized users must not initialize a Kubernetes client."),
    )
    stranger_headers = {"Authorization": f"Bearer {stranger.json()['access_token']}"}

    logs = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/logs",
        headers=stranger_headers,
    )
    events = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/kubernetes-events",
        headers=stranger_headers,
    )
    assert logs.status_code == 404
    assert events.status_code == 404


def test_kubernetes_log_source_unavailable_is_not_reported_as_empty_success(monkeypatch):
    headers, project_id, deployment_id = _create_diagnostics_deployment()

    def unavailable_adapter():
        raise KubernetesDiagnosticsUnavailable("No Kubernetes client.")

    monkeypatch.setattr(
        "app.routers.deployments.get_kubernetes_diagnostics_adapter",
        unavailable_adapter,
    )

    response = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/logs",
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "No Kubernetes client."


def test_kubernetes_diagnostics_bound_logs_and_filter_events_to_deployment():
    project_id = str(uuid.uuid4())
    deployment_id = str(uuid.uuid4())
    pod_uid = str(uuid.uuid4())
    pod = kubernetes_client.V1Pod(
        metadata=kubernetes_client.V1ObjectMeta(
            name="web-abc",
            namespace="minicloud",
            uid=pod_uid,
            labels={
                PROJECT_ID_LABEL: project_id,
                DEPLOYMENT_ID_LABEL: deployment_id,
            },
        ),
        spec=kubernetes_client.V1PodSpec(containers=[kubernetes_client.V1Container(name="app")]),
    )
    other_pod = kubernetes_client.V1Pod(
        metadata=kubernetes_client.V1ObjectMeta(
            name="other",
            namespace="minicloud",
            uid=str(uuid.uuid4()),
            labels={
                PROJECT_ID_LABEL: str(uuid.uuid4()),
                DEPLOYMENT_ID_LABEL: str(uuid.uuid4()),
            },
        ),
    )
    timestamp = datetime.now(UTC)
    core_api = Mock()
    core_api.list_namespaced_pod.return_value = kubernetes_client.V1PodList(
        metadata=kubernetes_client.V1ListMeta(),
        items=[pod, other_pod],
    )
    core_api.read_namespaced_pod_log.return_value = "x" * (MAX_LOG_BYTES_PER_POD + 1)
    core_api.list_namespaced_event.return_value = kubernetes_client.CoreV1EventList(
        metadata=kubernetes_client.V1ListMeta(),
        items=[
            kubernetes_client.CoreV1Event(
                metadata=kubernetes_client.V1ObjectMeta(creation_timestamp=timestamp),
                involved_object=kubernetes_client.V1ObjectReference(
                    kind="Pod",
                    uid=pod_uid,
                    namespace="minicloud",
                ),
                type="Warning",
                reason="BackOff",
                message="password=hidden",
            ),
            kubernetes_client.CoreV1Event(
                metadata=kubernetes_client.V1ObjectMeta(creation_timestamp=timestamp),
                involved_object=kubernetes_client.V1ObjectReference(
                    kind="Pod",
                    uid=other_pod.metadata.uid,
                    namespace="minicloud",
                ),
                type="Warning",
                reason="Other",
                message="must not be returned",
            ),
        ],
    )
    core_api.list_namespaced_event.side_effect = [
        core_api.list_namespaced_event.return_value,
        kubernetes_client.CoreV1EventList(
            metadata=kubernetes_client.V1ListMeta(),
            items=[
                kubernetes_client.CoreV1Event(
                    metadata=kubernetes_client.V1ObjectMeta(creation_timestamp=timestamp),
                    involved_object=kubernetes_client.V1ObjectReference(
                        kind="Deployment",
                        name=deployment_resource_name(deployment_id),
                        namespace="minicloud",
                    ),
                    type="Normal",
                    reason="ScalingReplicaSet",
                    message="Scaled to one replica.",
                )
            ],
        ),
    ]
    adapter = KubernetesDiagnosticsAdapter(core_api, "minicloud")

    logs, logs_truncated, _ = adapter.read_pod_logs(
        project_id,
        deployment_id,
        "app",
        None,
        100,
    )
    events, events_truncated, _ = adapter.list_kubernetes_events(
        project_id,
        deployment_id,
    )
    assert len(logs) == 1
    assert logs_truncated is True
    assert logs[0].truncated is True
    assert len(logs[0].log.encode("utf-8")) <= MAX_LOG_BYTES_PER_POD
    assert {(event.resource_kind, event.resource_name) for event in events} == {
        ("Pod", "web-abc"),
        ("Deployment", deployment_resource_name(deployment_id)),
    }
    pod_event = next(event for event in events if event.resource_kind == "Pod")
    assert "hidden" not in pod_event.message
    assert events_truncated is False
    assert core_api.list_namespaced_event.call_count == 2
    assert core_api.list_namespaced_event.call_args_list[0].kwargs == {
        "field_selector": "involvedObject.kind=Pod",
        "limit": 100,
    }
    assert core_api.list_namespaced_event.call_args_list[1].kwargs == {
        "field_selector": (
            f"involvedObject.kind=Deployment,involvedObject.name={deployment_resource_name(deployment_id)}"
        ),
        "limit": 100,
    }


def test_kubernetes_diagnostics_surface_api_unavailability(monkeypatch):
    headers, project_id, deployment_id = _create_diagnostics_deployment()
    adapter = Mock()
    adapter.read_pod_logs.side_effect = ApiException(status=403)
    monkeypatch.setattr(
        "app.routers.deployments.get_kubernetes_diagnostics_adapter",
        lambda: adapter,
    )

    response = client.get(
        f"/api/v1/projects/{project_id}/deployments/{deployment_id}/logs",
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Kubernetes log source is unavailable."


def test_diagnostics_client_requires_its_own_explicit_identity():
    configured = Settings(
        app_env="test",
        service_role="api",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret_key="test-only-minicloud-signing-key-32chars",
        kubernetes_diagnostics_in_cluster=False,
        kubernetes_diagnostics_config_file=None,
        kubernetes_diagnostics_context=None,
    )
    with pytest.raises(
        KubernetesDiagnosticsUnavailable,
        match="dedicated kubeconfig and explicit context",
    ):
        build_kubernetes_diagnostics_adapter(configured)
