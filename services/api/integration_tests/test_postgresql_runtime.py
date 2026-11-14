from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from app.core.db import engine
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parents[3]))


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _admin_connection() -> psycopg.Connection:
    return psycopg.connect(_psycopg_url(os.environ["MINICLOUD_TEST_ADMIN_DATABASE_URL"]))


def _assert_isolated_test_database() -> None:
    assert os.environ.get("MINICLOUD_POSTGRES_INTEGRATION") == "1"
    assert os.environ.get("MINICLOUD_TEST_DATABASE_NAME") == "minicloud"
    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", run_id)

    runtime = urlsplit(os.environ["DATABASE_URL"])
    worker = urlsplit(os.environ["MINICLOUD_TEST_WORKER_DATABASE_URL"])
    admin = urlsplit(os.environ["MINICLOUD_TEST_ADMIN_DATABASE_URL"])
    assert runtime.hostname == worker.hostname == admin.hostname == "pg"
    assert runtime.path == worker.path == admin.path == "/minicloud"
    assert runtime.username == "minicloud_api_runtime"
    assert worker.username == "minicloud_worker_runtime"
    assert admin.username == "postgres"
    assert not runtime.password and not worker.password and not admin.password

    with _admin_connection() as connection:
        database, version_num, marker, application_name = connection.execute(
            """
            SELECT current_database(),
                   current_setting('server_version_num'),
                   current_setting('minicloud.phase8_test_run', true),
                   current_setting('application_name')
            """
        ).fetchone()
    assert database == "minicloud"
    assert version_num == "160015"
    assert marker == run_id
    assert application_name.startswith("minicloud_phase8_it")


@pytest.fixture(scope="module", autouse=True)
def verify_isolated_postgresql_target() -> None:
    _assert_isolated_test_database()


def test_migrations_and_effective_role_privilege_matrix() -> None:
    with _admin_connection() as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("20261114_resource_policies",)

        request_id_columns = {
            (table_name, column_name, is_nullable)
            for table_name, column_name, is_nullable in connection.execute(
                """
                SELECT table_name, column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (table_name, column_name) IN (
                      ('deployment_jobs', 'request_id'),
                      ('deployment_events', 'request_id'),
                      ('deployment_jobs', 'revision_id')
                  )
                """
            )
        }
        assert request_id_columns == {
            ("deployment_jobs", "request_id", "YES"),
            ("deployment_events", "request_id", "YES"),
            ("deployment_jobs", "revision_id", "YES"),
        }

        policy_columns = {
            (table_name, column_name, is_nullable)
            for table_name, column_name, is_nullable in connection.execute(
                """
                SELECT table_name, column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('resource_policies', 'resource_policy_mutations')
                """
            )
        }
        assert {
            ("resource_policies", "max_replicas", "NO"),
            ("resource_policies", "cpu_request_millicores", "NO"),
            ("resource_policies", "cpu_limit_millicores", "NO"),
            ("resource_policies", "memory_request_bytes", "NO"),
            ("resource_policies", "memory_limit_bytes", "NO"),
            ("resource_policies", "version", "NO"),
            ("resource_policies", "applied_version", "YES"),
            ("resource_policy_mutations", "idempotency_key", "NO"),
            ("resource_policy_mutations", "request_hash", "NO"),
        }.issubset(policy_columns)
        assert connection.execute(
            "SELECT count(*) FROM public.resource_policies"
        ).fetchone() == (0,)

        indexes = {
            name: unique
            for name, unique in connection.execute(
                """
                SELECT indexname, indexdef ILIKE '%UNIQUE%'
                FROM pg_catalog.pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                    'ix_deployment_jobs_idempotency_key',
                    'uq_deployment_jobs_project_id_idempotency_key',
                    'uq_project_memberships_project_user',
                    'ix_token_revocations_expires_at',
                    'token_revocations_pkey',
                    'ix_audit_events_project_created_id',
                    'ix_projects_created_id',
                    'ix_project_memberships_user_project',
                    'ix_deployments_project_created_id',
                    'ix_deployment_events_deployment_created_id',
                    'uq_deployment_revisions_deployment_number',
                    'ix_deployment_revisions_deployment_created_id',
                    'uq_resource_policies_project_id',
                    'uq_resource_policy_mutations_project_key'
                  )
                """
            )
        }
        assert indexes == {
            "uq_deployment_jobs_project_id_idempotency_key": True,
            "uq_project_memberships_project_user": True,
            "ix_token_revocations_expires_at": False,
            "token_revocations_pkey": True,
            "ix_audit_events_project_created_id": False,
            "ix_projects_created_id": False,
            "ix_project_memberships_user_project": False,
            "ix_deployments_project_created_id": False,
            "ix_deployment_events_deployment_created_id": False,
            "uq_deployment_revisions_deployment_number": True,
            "ix_deployment_revisions_deployment_created_id": False,
            "uq_resource_policies_project_id": True,
            "uq_resource_policy_mutations_project_key": True,
        }
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_trigger
            WHERE tgname = 'minicloud_audit_events_append_only'
              AND tgenabled = 'O'
              AND NOT tgisinternal
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_trigger
            WHERE tgname = 'minicloud_deployment_revisions_append_only'
              AND tgenabled = 'O'
              AND NOT tgisinternal
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'deployment_jobs'
              AND column_name = 'project_id'
            """
        ).fetchone() == ("NO",)
        assert connection.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'deployment_jobs'
              AND column_name = 'started_at'
            """
        ).fetchone() == ("YES",)
        operation_columns = {
            name: nullable
            for name, nullable in connection.execute(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'deployment_jobs'
                  AND column_name IN ('operation_type', 'target_replicas')
                """
            )
        }
        assert operation_columns == {
            "operation_type": "NO",
            "target_replicas": "YES",
        }
        project_scope_index = connection.execute(
            """
            SELECT indexdef
            FROM pg_catalog.pg_indexes
            WHERE schemaname = 'public'
              AND indexname = 'uq_deployment_jobs_project_id_idempotency_key'
            """
        ).fetchone()[0]
        assert "UNIQUE" in project_scope_index.upper()
        assert "(project_id, idempotency_key)" in project_scope_index

        roles = {
            name: (can_login, superuser, create_db, create_role, inherit, replication, bypass_rls)
            for (
                name,
                can_login,
                superuser,
                create_db,
                create_role,
                inherit,
                replication,
                bypass_rls,
            ) in connection.execute(
                """
                SELECT rolname, rolcanlogin, rolsuper, rolcreatedb,
                       rolcreaterole, rolinherit, rolreplication, rolbypassrls
                FROM pg_catalog.pg_roles
                WHERE rolname IN (
                    'minicloud_api_runtime',
                    'minicloud_worker_runtime',
                    'minicloud_revocation_maintenance'
                )
                """
            )
        }
        assert roles == {
            "minicloud_api_runtime": (True, False, False, False, False, False, False),
            "minicloud_worker_runtime": (True, False, False, False, False, False, False),
            "minicloud_revocation_maintenance": (
                False,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
        }

        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS granted
              ON granted.oid = membership.member
            JOIN pg_catalog.pg_roles AS granted_to
              ON granted_to.oid = membership.roleid
            WHERE granted.rolname IN (
                'minicloud_api_runtime', 'minicloud_worker_runtime',
                'minicloud_revocation_maintenance'
            )
               OR granted_to.rolname IN (
                'minicloud_api_runtime', 'minicloud_worker_runtime',
                'minicloud_revocation_maintenance'
            )
            """
        ).fetchone()[0] == 0

        runtime_expected = {
            "users": {"SELECT", "INSERT"},
            "projects": {"SELECT", "INSERT", "UPDATE"},
            "project_memberships": {"SELECT", "INSERT", "UPDATE", "DELETE"},
            "deployments": {"SELECT", "INSERT", "UPDATE"},
            "deployment_jobs": {"SELECT", "INSERT", "UPDATE"},
            "deployment_events": {"SELECT", "INSERT"},
            "deployment_revisions": {"SELECT", "INSERT"},
            "resource_policies": {"SELECT", "INSERT", "UPDATE"},
            "resource_policy_mutations": {"SELECT", "INSERT"},
            "audit_events": {"INSERT"},
            "token_revocations": {"SELECT", "INSERT"},
            "alembic_version": set(),
        }
        worker_expected = {
            "users": set(),
            "projects": set(),
            "project_memberships": set(),
            "deployments": {"SELECT", "UPDATE"},
            "deployment_jobs": {"SELECT", "UPDATE"},
            "deployment_events": {"INSERT"},
            "deployment_revisions": set(),
            "resource_policies": set(),
            "resource_policy_mutations": set(),
            "audit_events": set(),
            "token_revocations": set(),
            "alembic_version": set(),
        }
        maintenance_expected = {
            **{table: set() for table in runtime_expected},
            "token_revocations": {"DELETE"},
        }
        table_privileges = [
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ]
        for role, expected in (
            ("minicloud_api_runtime", runtime_expected),
            ("minicloud_worker_runtime", worker_expected),
            ("minicloud_revocation_maintenance", maintenance_expected),
        ):
            for table, allowed in expected.items():
                actual = {
                    privilege
                    for privilege, granted in connection.execute(
                        """
                        SELECT privilege,
                               pg_catalog.has_table_privilege(%s, %s, privilege)
                        FROM pg_catalog.unnest(%s::text[]) AS privilege
                        """,
                        (role, f"public.{table}", table_privileges),
                    )
                    if granted
                }
                assert actual == allowed, (role, table, actual)

        assert connection.execute(
            """
            SELECT pg_catalog.has_database_privilege(
                       'minicloud_api_runtime', current_database(), 'CONNECT'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_api_runtime', current_database(), 'CREATE'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_api_runtime', current_database(), 'TEMP'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_worker_runtime', current_database(), 'CONNECT'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_worker_runtime', current_database(), 'CREATE'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_worker_runtime', current_database(), 'TEMP'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_revocation_maintenance',
                       current_database(),
                       'CREATE'
                   ),
                   pg_catalog.has_database_privilege(
                       'minicloud_revocation_maintenance',
                       current_database(),
                       'TEMP'
                   )
            """
        ).fetchone() == (True, False, False, True, False, False, False, False)

        schemas = connection.execute(
            """
            SELECT namespace.nspname,
                   pg_catalog.has_schema_privilege(
                       'minicloud_api_runtime', namespace.oid, 'USAGE'
                   ),
                   pg_catalog.has_schema_privilege(
                       'minicloud_api_runtime', namespace.oid, 'CREATE'
                   ),
                   pg_catalog.has_schema_privilege(
                       'minicloud_worker_runtime', namespace.oid, 'USAGE'
                   ),
                   pg_catalog.has_schema_privilege(
                       'minicloud_worker_runtime', namespace.oid, 'CREATE'
                   ),
                   pg_catalog.has_schema_privilege(
                       'minicloud_revocation_maintenance', namespace.oid, 'USAGE'
                   ),
                   pg_catalog.has_schema_privilege(
                       'minicloud_revocation_maintenance', namespace.oid, 'CREATE'
                   )
            FROM pg_catalog.pg_namespace AS namespace
            WHERE namespace.nspname !~ '^pg_'
              AND namespace.nspname <> 'information_schema'
            """
        ).fetchall()
        assert schemas
        for (
            schema,
            runtime_usage,
            runtime_create,
            worker_usage,
            worker_create,
            maintenance_usage,
            maintenance_create,
        ) in schemas:
            if schema == "public":
                assert (runtime_usage, runtime_create) == (True, False)
                assert (worker_usage, worker_create) == (True, False)
                assert (maintenance_usage, maintenance_create) == (True, False)
            else:
                assert not any(
                    (
                        runtime_usage,
                        runtime_create,
                        worker_usage,
                        worker_create,
                        maintenance_usage,
                        maintenance_create,
                    )
                )

        assert connection.execute(
            """
            SELECT EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_database AS database
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                           COALESCE(
                               database.datacl,
                               pg_catalog.acldefault('d', database.datdba)
                           )
                       ) AS acl
                       WHERE database.datname = current_database()
                         AND acl.grantee = 0
                         AND acl.privilege_type = 'TEMPORARY'
                   ),
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_namespace AS namespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                           COALESCE(
                               namespace.nspacl,
                               pg_catalog.acldefault('n', namespace.nspowner)
                           )
                       ) AS acl
                       WHERE namespace.nspname = 'public'
                         AND acl.grantee = 0
                         AND acl.privilege_type = 'CREATE'
                   )
            """
        ).fetchone() == (False, False)

        owned_objects = connection.execute(
            """
            SELECT namespace.nspname, relation.relname, owner.rolname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_roles AS owner
              ON owner.oid = relation.relowner
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p', 'i', 'I', 'S')
            """
        ).fetchall()
        assert owned_objects
        assert all(owner == "postgres" for _, _, owner in owned_objects)

        cleanup = connection.execute(
            """
            SELECT owner.rolname,
                   procedure.prosecdef,
                   procedure.proconfig,
                   pg_catalog.pg_get_functiondef(procedure.oid),
                   pg_catalog.has_function_privilege(
                       'minicloud_api_runtime', procedure.oid, 'EXECUTE'
                   ),
                   pg_catalog.has_function_privilege(
                       'minicloud_revocation_maintenance',
                       procedure.oid,
                       'EXECUTE'
                   ),
                   pg_catalog.has_function_privilege(
                       'minicloud_worker_runtime', procedure.oid, 'EXECUTE'
                   ),
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.aclexplode(
                           COALESCE(
                               procedure.proacl,
                               pg_catalog.acldefault('f', procedure.proowner)
                           )
                       ) AS acl
                       WHERE acl.grantee = 0
                         AND acl.privilege_type = 'EXECUTE'
                   ),
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.aclexplode(
                           COALESCE(
                               procedure.proacl,
                               pg_catalog.acldefault('f', procedure.proowner)
                           )
                       ) AS acl
                       WHERE acl.grantee = (
                           SELECT oid FROM pg_catalog.pg_roles
                           WHERE rolname = 'minicloud_api_runtime'
                       )
                         AND acl.privilege_type = 'EXECUTE'
                         AND acl.is_grantable
                   )
            FROM pg_catalog.pg_proc AS procedure
            JOIN pg_catalog.pg_roles AS owner
              ON owner.oid = procedure.proowner
            WHERE procedure.oid =
                'public.minicloud_cleanup_expired_token_revocations()'
                ::pg_catalog.regprocedure
            """
        ).fetchone()
        assert cleanup is not None
        (
            owner,
            security_definer,
            settings,
            body,
            runtime_execute,
            maintenance_execute,
            worker_execute,
            public_execute,
            runtime_grantable,
        ) = cleanup
        assert owner == "minicloud_revocation_maintenance"
        assert security_definer
        assert "search_path=pg_catalog" in settings
        assert runtime_execute and maintenance_execute
        assert not worker_execute
        assert not public_execute and not runtime_grantable
        normalized_body = " ".join(body.lower().split())
        assert normalized_body.count("delete from") == 1
        assert "delete from public.token_revocations" in normalized_body
        assert "where expires_at <= pg_catalog.now()" in normalized_body
        assert not any(
            statement in normalized_body
            for statement in ("insert into", "update ", "truncate ", "drop ")
        )

        maintenance_column_privileges = {
            name
            for name, granted in connection.execute(
                """
                SELECT attribute.attname,
                       pg_catalog.has_column_privilege(
                           'minicloud_revocation_maintenance',
                           attribute.attrelid,
                           attribute.attname,
                           'SELECT'
                       )
                FROM pg_catalog.pg_attribute AS attribute
                WHERE attribute.attrelid =
                    'public.token_revocations'::pg_catalog.regclass
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                """
            )
            if granted
        }
        assert maintenance_column_privileges == {"expires_at"}

        function_acl = connection.execute(
            """
            SELECT grantee.rolname, acl.privilege_type, acl.is_grantable
            FROM pg_catalog.pg_proc AS procedure
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f', procedure.proowner)
                )
            ) AS acl
            LEFT JOIN pg_catalog.pg_roles AS grantee
              ON grantee.oid = acl.grantee
            WHERE procedure.oid =
                'public.minicloud_cleanup_expired_token_revocations()'
                ::pg_catalog.regprocedure
            """
        ).fetchall()
        assert ("minicloud_revocation_maintenance", "EXECUTE", False) in function_acl
        assert ("minicloud_api_runtime", "EXECUTE", False) in function_acl
        assert not any(
            grantee == "minicloud_worker_runtime"
            for grantee, _, _ in function_acl
        )
        assert not any(grantee is None for grantee, _, _ in function_acl)

        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind = 'S'
              AND (
                  pg_catalog.has_sequence_privilege(
                      'minicloud_api_runtime', relation.oid, 'USAGE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_api_runtime', relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_api_runtime', relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_worker_runtime', relation.oid, 'USAGE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_worker_runtime', relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_worker_runtime', relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_revocation_maintenance',
                      relation.oid,
                      'USAGE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_revocation_maintenance',
                      relation.oid,
                      'SELECT'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      'minicloud_revocation_maintenance',
                      relation.oid,
                      'UPDATE'
                  )
              )
            """
        ).fetchone()[0] == 0


def test_api_and_worker_use_separate_runtime_database_privileges() -> None:
    from unittest.mock import patch

    from services.worker.kubernetes_adapter import DeploymentObservation
    from services.worker.main import _claim_jobs, _process_job

    class ReadyAdapter:
        def apply(self, deployment: object) -> None:
            return None

        def observe(self, deployment: object) -> DeploymentObservation:
            return DeploymentObservation(True, "Integration workload is ready.")

    with TestClient(app, raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{os.environ['MINICLOUD_TEST_RUN_ID']}@example.test",
                "password": "integration-only-password",
            },
        )
        assert registered.status_code == 201
        token = registered.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        project_response = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Integration Project", "slug": os.environ["MINICLOUD_TEST_RUN_ID"]},
        )
        assert project_response.status_code == 201
        project = project_response.json()

        member_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{os.environ['MINICLOUD_TEST_RUN_ID']}-member@example.test",
                "password": "integration-only-password",
            },
        )
        assert member_response.status_code == 201
        member_id = member_response.json()["user"]["id"]
        members_path = f"/api/v1/projects/{project['id']}/members"
        added_member = client.post(
            members_path,
            headers=headers,
            json={"email": member_response.json()["user"]["email"]},
        )
        assert added_member.status_code == 201
        assert added_member.json()["user_id"] == member_id
        promoted_member = client.patch(
            f"{members_path}/{member_id}",
            headers=headers,
            json={"role": "owner"},
        )
        assert promoted_member.status_code == 200
        demoted_member = client.patch(
            f"{members_path}/{member_id}",
            headers=headers,
            json={"role": "member"},
        )
        assert demoted_member.status_code == 200
        assert client.delete(
            f"{members_path}/{member_id}",
            headers=headers,
        ).status_code == 204

        update_response = client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=headers,
            json={"name": "Updated Integration Project"},
        )
        assert update_response.status_code == 200

        deployment_response = client.post(
            f"/api/v1/projects/{project['id']}/deployments",
            headers=headers,
            json={
                "name": "integration-app",
                "image": "nginxinc/nginx-unprivileged:1.27-alpine",
                "port": 8080,
                "replicas": 1,
                "idempotency_key": os.environ["MINICLOUD_TEST_RUN_ID"],
            },
        )
        assert deployment_response.status_code == 202
        deployment_id = deployment_response.json()["deployment_id"]
        initial_revision_id = deployment_response.json()["revision_id"]

        worker_engine = create_engine(
            os.environ["MINICLOUD_TEST_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        worker_session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=worker_engine,
        )
        try:
            with patch("services.worker.main.SessionLocal", worker_session_factory), worker_session_factory() as db:
                claimed_jobs = _claim_jobs(db, datetime.now(UTC))
                assert claimed_jobs
                for job_id, lease_expires_at in claimed_jobs:
                    _process_job(
                        db,
                        job_id,
                        lease_expires_at,
                        ReadyAdapter(),
                        datetime.now(UTC),
                    )

            scale_path = (
                f"/api/v1/projects/{project['id']}/deployments/{deployment_id}/scale"
            )
            scale_payload = {
                "replicas": 2,
                "expected_replicas": 1,
                "idempotency_key": f"{os.environ['MINICLOUD_TEST_RUN_ID']}-scale",
            }
            scale_response = client.patch(
                scale_path,
                headers=headers,
                json=scale_payload,
            )
            repeated_scale = client.patch(
                scale_path,
                headers=headers,
                json=scale_payload,
            )
            assert scale_response.status_code == 202, scale_response.text
            assert repeated_scale.status_code == 202, repeated_scale.text
            assert repeated_scale.json()["deployment_id"] == deployment_id
            scale_revision_id = scale_response.json()["revision_id"]
            scale_request_id = scale_response.headers["X-Request-ID"]

            with patch("services.worker.main.SessionLocal", worker_session_factory), worker_session_factory() as db:
                scale_jobs = _claim_jobs(db, datetime.now(UTC))
                assert len(scale_jobs) == 1
                _process_job(
                    db,
                    scale_jobs[0][0],
                    scale_jobs[0][1],
                    ReadyAdapter(),
                    datetime.now(UTC),
                )
        finally:
            worker_engine.dispose()

        rollback_response = client.post(
            f"/api/v1/projects/{project['id']}/deployments/{deployment_id}/rollback",
            headers=headers,
            json={
                "revision_id": initial_revision_id,
                "expected_revision_id": scale_revision_id,
                "idempotency_key": f"{os.environ['MINICLOUD_TEST_RUN_ID']}-rollback",
            },
        )
        assert rollback_response.status_code == 202, rollback_response.text
        rollback_revision_id = rollback_response.json()["revision_id"]
        rollback_request_id = rollback_response.headers["X-Request-ID"]

        rollback_engine = create_engine(
            os.environ["MINICLOUD_TEST_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        rollback_session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=rollback_engine,
        )
        try:
            with patch("services.worker.main.SessionLocal", rollback_session_factory), rollback_session_factory() as db:
                rollback_jobs = _claim_jobs(db, datetime.now(UTC))
                assert len(rollback_jobs) == 1
                _process_job(
                    db,
                    rollback_jobs[0][0],
                    rollback_jobs[0][1],
                    ReadyAdapter(),
                    datetime.now(UTC),
                )
        finally:
            rollback_engine.dispose()

        deployment_status = client.get(
            f"/api/v1/projects/{project['id']}/deployments/{deployment_id}",
            headers=headers,
        )
        assert deployment_status.status_code == 200
        assert deployment_status.json()["status"] == "ready"
        assert deployment_status.json()["replicas"] == 1
        events = client.get(
            f"/api/v1/projects/{project['id']}/deployments/{deployment_id}/events",
            headers=headers,
        )
        assert events.status_code == 200
        event_types = {event["event_type"] for event in events.json()["items"]}
        assert {
            "accepted",
            "scale_accepted",
            "rollback_accepted",
            "applying",
            "ready",
        } <= event_types

    with _admin_connection() as connection:
        scale_job = connection.execute(
            """
            SELECT operation_type, target_replicas, state, request_id, revision_id
            FROM public.deployment_jobs
            WHERE deployment_id = %s
              AND operation_type = 'scale'
            """,
            (deployment_id,),
        ).fetchone()
        revisions = connection.execute(
            """
            SELECT id, revision_number, image, replicas, rollback_of_revision_id
            FROM public.deployment_revisions
            WHERE deployment_id = %s
            ORDER BY revision_number
            """,
            (deployment_id,),
        ).fetchall()
        rollback_job = connection.execute(
            """
            SELECT operation_type, target_replicas, state, request_id, revision_id
            FROM public.deployment_jobs
            WHERE deployment_id = %s
              AND operation_type = 'rollback'
            """,
            (deployment_id,),
        ).fetchone()
        rollback_events = connection.execute(
            """
            SELECT event_type, request_id
            FROM public.deployment_events
            WHERE deployment_id = %s
              AND request_id = %s
            ORDER BY created_at, id
            """,
            (deployment_id, rollback_request_id),
        ).fetchall()
        scale_events = connection.execute(
            """
            SELECT event_type, request_id
            FROM public.deployment_events
            WHERE deployment_id = %s
              AND request_id = %s
            ORDER BY created_at, id
            """,
            (deployment_id, scale_request_id),
        ).fetchall()
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM public.users),
                (SELECT count(*) FROM public.projects),
                (SELECT count(*) FROM public.project_memberships),
                (SELECT count(*) FROM public.deployments),
                (SELECT count(*) FROM public.deployment_jobs),
                (SELECT count(*) FROM public.deployment_events),
                (SELECT count(*) FROM public.audit_events
                 WHERE project_id = %s)
            """,
            (project["id"],),
        ).fetchone()
    assert scale_job == ("scale", 2, "completed", scale_request_id, scale_revision_id)
    assert revisions == [
        (initial_revision_id, 1, "nginxinc/nginx-unprivileged:1.27-alpine", 1, None),
        (scale_revision_id, 2, "nginxinc/nginx-unprivileged:1.27-alpine", 2, None),
        (
            rollback_revision_id,
            3,
            "nginxinc/nginx-unprivileged:1.27-alpine",
            1,
            initial_revision_id,
        ),
    ]
    assert rollback_job == (
        "rollback",
        1,
        "completed",
        rollback_request_id,
        rollback_revision_id,
    )
    assert {event_type for event_type, _ in scale_events} >= {
        "scale_accepted",
        "applying",
        "ready",
    }
    assert all(request_id == scale_request_id for _, request_id in scale_events)
    assert {event_type for event_type, _ in rollback_events} >= {
        "rollback_accepted",
        "applying",
        "ready",
    }
    assert all(request_id == rollback_request_id for _, request_id in rollback_events)
    assert all(count > 0 for count in counts[:6])
    assert counts[6] == 9

    with _admin_connection() as connection:
        audit_actions = dict(
            connection.execute(
                """
                SELECT action, count(*)
                FROM public.audit_events
                WHERE project_id = %s
                GROUP BY action
                """,
                (project["id"],),
            ).fetchall()
        )
        audit_event_id = connection.execute(
            """
            SELECT id FROM public.audit_events
            WHERE project_id = %s
            ORDER BY created_at, id
            LIMIT 1
            """,
            (project["id"],),
        ).fetchone()[0]
        revision_id = connection.execute(
            """
            SELECT id FROM public.deployment_revisions
            WHERE deployment_id = %s
            ORDER BY revision_number
            LIMIT 1
            """,
            (deployment_id,),
        ).fetchone()[0]
        assert audit_actions == {
            "project.created": 1,
            "project.member_added": 1,
            "project.member_role_updated": 2,
            "project.member_removed": 1,
            "project.updated": 1,
            "deployment.created": 1,
            "deployment.scale_requested": 1,
            "deployment.rollback_requested": 1,
        }
        for statement in (
            "UPDATE public.audit_events SET action = 'tampered' WHERE id = %s",
            "DELETE FROM public.audit_events WHERE id = %s",
            "TRUNCATE public.audit_events",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="audit_events is append-only"):
                if "%s" in statement:
                    connection.execute(statement, (audit_event_id,))
                else:
                    connection.execute(statement)
            connection.rollback()
        for statement in (
            "UPDATE public.deployment_revisions SET replicas = 9 WHERE id = %s",
            "DELETE FROM public.deployment_revisions WHERE id = %s",
            "TRUNCATE public.deployment_revisions CASCADE",
        ):
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="deployment_revisions is append-only",
            ):
                if "%s" in statement:
                    connection.execute(statement, (revision_id,))
                else:
                    connection.execute(statement)
            connection.rollback()


def test_worker_claims_are_exclusive_under_concurrent_postgresql_transactions() -> None:
    from services.worker.main import _claim_jobs

    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    with TestClient(app, raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{run_id}-claim@example.test",
                "password": "integration-only-password",
            },
        )
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        project_response = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Concurrent Claim Project", "slug": f"{run_id}-claim"},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]
        deployment_response = client.post(
            f"/api/v1/projects/{project_id}/deployments",
            headers=headers,
            json={
                "name": "claim-race",
                "image": "nginxinc/nginx-unprivileged:1.27-alpine",
                "port": 8080,
                "replicas": 1,
                "idempotency_key": f"{run_id}-claim",
            },
        )
        assert deployment_response.status_code == 202
        deployment_id = deployment_response.json()["deployment_id"]

    worker_engine = create_engine(
        os.environ["MINICLOUD_TEST_WORKER_DATABASE_URL"],
        pool_pre_ping=True,
    )
    worker_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=worker_engine,
    )
    barrier = threading.Barrier(3)
    claim_time = datetime.now(UTC)

    def claim_once() -> list[tuple[str, datetime]]:
        with worker_session_factory() as db:
            barrier.wait(timeout=10)
            return _claim_jobs(db, claim_time)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim_once) for _ in range(2)]
            barrier.wait(timeout=10)
            claim_results = [future.result(timeout=20) for future in futures]
    finally:
        worker_engine.dispose()

    claimed = [claim for result in claim_results for claim in result]
    assert len(claimed) == 1
    assert claimed[0][0]
    with _admin_connection() as connection:
        database_job = connection.execute(
            """
            SELECT lease_expires_at
            FROM public.deployment_jobs
            WHERE deployment_id = %s
            """,
            (deployment_id,),
        ).fetchone()
    assert database_job is not None
    assert database_job[0] is not None
    assert database_job[0].replace(tzinfo=UTC) == claimed[0][1]


def test_concurrent_owner_removals_preserve_one_project_owner() -> None:
    from app.main import auth_rate_limiter

    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    auth_rate_limiter.clear()
    with TestClient(app, raise_server_exceptions=False) as client:
        owner = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{run_id}-owner-race@example.test",
                "password": "integration-only-password",
            },
        )
        other_owner = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{run_id}-other-owner-race@example.test",
                "password": "integration-only-password",
            },
        )
        auth_rate_limiter.clear()
        assert owner.status_code == other_owner.status_code == 201
        owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
        other_owner_headers = {
            "Authorization": f"Bearer {other_owner.json()['access_token']}"
        }

        project_response = client.post(
            "/api/v1/projects",
            headers=owner_headers,
            json={"name": "Owner race project", "slug": f"{run_id}-owner-race"},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]
        members_path = f"/api/v1/projects/{project_id}/members"
        added = client.post(
            members_path,
            headers=owner_headers,
            json={"email": other_owner.json()["user"]["email"]},
        )
        assert added.status_code == 201
        promoted = client.patch(
            f"{members_path}/{other_owner.json()['user']['id']}",
            headers=owner_headers,
            json={"role": "owner"},
        )
        assert promoted.status_code == 200

        admin = _admin_connection()
        try:
            admin.execute(
                "SELECT id FROM public.projects WHERE id = %s FOR UPDATE",
                (project_id,),
            ).fetchone()
            barrier = threading.Barrier(3)
            started = [threading.Event(), threading.Event()]

            def remove_owner(
                index: int,
                headers: dict[str, str],
                member_user_id: str,
            ) -> int:
                started[index].set()
                barrier.wait(timeout=10)
                with TestClient(app, raise_server_exceptions=False) as request_client:
                    response = request_client.delete(
                        f"{members_path}/{member_user_id}",
                        headers=headers,
                    )
                    return response.status_code

            with ThreadPoolExecutor(max_workers=2) as executor:
                removals = [
                    executor.submit(
                        remove_owner,
                        0,
                        owner_headers,
                        other_owner.json()["user"]["id"],
                    ),
                    executor.submit(
                        remove_owner,
                        1,
                        other_owner_headers,
                        owner.json()["user"]["id"],
                    ),
                ]
                barrier.wait(timeout=10)
                assert all(event.wait(timeout=10) for event in started)

                waiting_requests = 0
                for _ in range(100):
                    waiting_row = admin.execute(
                        """
                        SELECT count(*)
                        FROM pg_catalog.pg_stat_activity
                        WHERE datname = pg_catalog.current_database()
                          AND usename = 'minicloud_api_runtime'
                          AND wait_event_type = 'Lock'
                        """
                    ).fetchone()
                    assert waiting_row is not None
                    waiting_requests = waiting_row[0]
                    if waiting_requests >= 1:
                        break
                    threading.Event().wait(0.1)
                requests_pending = all(not future.done() for future in removals)
                admin.commit()

                assert waiting_requests >= 1 and requests_pending, (
                    "Owner removal requests must remain pending while the "
                    "project row is locked."
                )
                assert sorted(future.result(timeout=20) for future in removals) == [
                    204,
                    403,
                ]
        finally:
            admin.rollback()
            admin.close()

    with _admin_connection() as connection:
        owners = connection.execute(
            """
            SELECT project.owner_id,
                   membership.user_id,
                   count(*) OVER ()
            FROM public.projects AS project
            JOIN public.project_memberships AS membership
              ON membership.project_id = project.id
             AND membership.role = 'owner'
            WHERE project.id = %s
            """,
            (project_id,),
        ).fetchall()
    assert len(owners) == 1
    assert owners[0][0] == owners[0][1]
    assert owners[0][2] == 1


def test_concurrent_scale_mutations_reject_a_stale_replica_count() -> None:
    from app.main import auth_rate_limiter

    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    auth_rate_limiter.clear()
    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{run_id}-scale-race@example.test",
                "password": "integration-only-password",
            },
        )
        auth_rate_limiter.clear()
        assert registration.status_code == 201
        headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
        project = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"name": "Scale race project", "slug": f"{run_id}-scale-race"},
        )
        assert project.status_code == 201
        deployment_response = client.post(
            f"/api/v1/projects/{project.json()['id']}/deployments",
            headers=headers,
            json={
                "name": "scale-race",
                "image": "nginx:1.27",
                "replicas": 2,
            },
        )
        assert deployment_response.status_code == 202
        deployment_id = deployment_response.json()["deployment_id"]
        scale_path = (
            f"/api/v1/projects/{project.json()['id']}/deployments/"
            f"{deployment_id}/scale"
        )

    admin = _admin_connection()
    try:
        admin.execute(
            "UPDATE public.deployments SET status = 'ready' WHERE id = %s",
            (deployment_id,),
        )
        admin.execute(
            """
            UPDATE public.deployment_jobs
            SET state = 'completed'
            WHERE deployment_id = %s AND operation_type = 'deploy'
            """,
            (deployment_id,),
        )
        admin.execute(
            "SELECT id FROM public.deployments WHERE id = %s FOR UPDATE",
            (deployment_id,),
        ).fetchone()
        barrier = threading.Barrier(3)
        started = [threading.Event(), threading.Event()]

        def scale(
            index: int,
            target_replicas: int,
        ) -> tuple[int, str | None]:
            started[index].set()
            barrier.wait(timeout=10)
            with TestClient(app, raise_server_exceptions=False) as request_client:
                response = request_client.patch(
                    scale_path,
                    headers=headers,
                    json={
                        "replicas": target_replicas,
                        "expected_replicas": 2,
                        "idempotency_key": f"{run_id}-scale-race-{target_replicas}",
                    },
                )
                detail = response.json().get("detail")
                return response.status_code, detail if isinstance(detail, str) else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            requests = [
                executor.submit(scale, 0, 3),
                executor.submit(scale, 1, 4),
            ]
            barrier.wait(timeout=10)
            assert all(event.wait(timeout=10) for event in started)

            waiting_requests = 0
            for _ in range(100):
                waiting_row = admin.execute(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_stat_activity
                    WHERE datname = pg_catalog.current_database()
                      AND usename = 'minicloud_api_runtime'
                      AND wait_event_type = 'Lock'
                    """
                ).fetchone()
                assert waiting_row is not None
                waiting_requests = waiting_row[0]
                if waiting_requests >= 1:
                    break
                threading.Event().wait(0.1)
            requests_pending = all(not future.done() for future in requests)
            admin.commit()

            assert waiting_requests >= 1 and requests_pending, (
                "Scale requests must contend on the deployment row before "
                "checking the expected replica count."
            )
            results = [future.result(timeout=20) for future in requests]
    finally:
        admin.rollback()
        admin.close()

    assert sorted(status_code for status_code, _ in results) == [202, 409], results
    rejected = next(detail for status_code, detail in results if status_code == 409)
    assert rejected == "Deployment replica count changed; refresh before retrying"
    with _admin_connection() as connection:
        row = connection.execute(
            """
            SELECT deployment.replicas,
                   count(job.id) FILTER (WHERE job.operation_type = 'scale')
            FROM public.deployments AS deployment
            LEFT JOIN public.deployment_jobs AS job
              ON job.deployment_id = deployment.id
            WHERE deployment.id = %s
            GROUP BY deployment.replicas
            """,
            (deployment_id,),
        ).fetchone()
    assert row is not None
    assert row[0] in {3, 4}
    assert row[1] == 1


def test_runtime_denials_and_cleanup_function_expired_row_scope() -> None:
    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    expired_hash = hashlib.sha256(f"{run_id}:expired".encode()).hexdigest()
    active_hash = hashlib.sha256(f"{run_id}:active".encode()).hexdigest()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO public.token_revocations
                    (token_hash, expires_at, created_at)
                VALUES
                    (:expired, :past, pg_catalog.now()),
                    (:active, :future, pg_catalog.now())
                """
            ),
            {
                "expired": expired_hash,
                "active": active_hash,
                "past": datetime.now(UTC) - timedelta(minutes=1),
                "future": datetime.now(UTC) + timedelta(hours=1),
            },
        )
        connection.execute(
            text("SELECT public.minicloud_cleanup_expired_token_revocations()")
        )
        remaining = set(
            connection.execute(
                text(
                    """
                    SELECT token_hash FROM public.token_revocations
                    WHERE token_hash IN (:expired, :active)
                    """
                ),
                {"expired": expired_hash, "active": active_hash},
            ).scalars()
        )
    assert remaining == {active_hash}

    denied_statements = (
        "DELETE FROM public.token_revocations",
        "SELECT version_num FROM public.alembic_version",
        "UPDATE public.audit_events SET action = 'tampered' WHERE false",
        "DELETE FROM public.audit_events WHERE false",
        "SELECT * FROM public.audit_events",
        f"CREATE TABLE public.phase8_forbidden_{run_id} (id integer)",
    )
    with psycopg.connect(_psycopg_url(os.environ["DATABASE_URL"])) as connection:
        for statement in denied_statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()
        assert connection.execute("SELECT 1").fetchone() == (1,)

    worker_denied_statements = (
        "SELECT * FROM public.users",
        "SELECT * FROM public.projects",
        "SELECT * FROM public.project_memberships",
        "SELECT * FROM public.token_revocations",
        "SELECT * FROM public.audit_events",
        (
            "INSERT INTO public.audit_events "
            "(id, actor_user_id, project_id, action, resource_type, resource_id, details) "
            "VALUES ('worker-denied', 'worker-denied', 'worker-denied', "
            "'denied', 'denied', 'worker-denied', '{}')"
        ),
        "INSERT INTO public.token_revocations (token_hash, expires_at) VALUES ('worker-denied', pg_catalog.now())",
        "SELECT public.minicloud_cleanup_expired_token_revocations()",
        "SELECT * FROM public.deployment_events",
        f"CREATE TABLE public.worker_forbidden_{run_id} (id integer)",
    )
    with psycopg.connect(
        _psycopg_url(os.environ["MINICLOUD_TEST_WORKER_DATABASE_URL"])
    ) as connection:
        for statement in worker_denied_statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()
        assert connection.execute("SELECT 1").fetchone() == (1,)

    unauthorized_role = f"minicloud_phase8_u_{run_id[:12]}"
    with _admin_connection() as connection:
        connection.execute(
            f"""
            CREATE ROLE {unauthorized_role}
                LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOINHERIT NOREPLICATION NOBYPASSRLS
            """
        )

    admin_url = urlsplit(os.environ["MINICLOUD_TEST_ADMIN_DATABASE_URL"])
    authority = admin_url.hostname
    if admin_url.port is not None:
        authority = f"{authority}:{admin_url.port}"
    unauthorized_url = urlunsplit(
        (
            admin_url.scheme,
            f"{unauthorized_role}@{authority}",
            admin_url.path,
            admin_url.query,
            admin_url.fragment,
        )
    )
    with psycopg.connect(unauthorized_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT public.minicloud_cleanup_expired_token_revocations()"
            )
        connection.rollback()
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_logout_revokes_token_and_fails_closed_when_cleanup_is_denied() -> None:
    run_id = os.environ["MINICLOUD_TEST_RUN_ID"]
    with TestClient(app, raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"logout-{run_id}@example.test",
                "password": "integration-only-password",
            },
        )
        assert registered.status_code == 201
        token = registered.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        logged_out = client.post("/api/v1/auth/logout", headers=headers)
        assert logged_out.status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with _admin_connection() as connection:
            assert connection.execute(
                "SELECT token_hash FROM public.token_revocations WHERE token_hash = %s",
                (token_hash,),
            ).fetchone() == (token_hash,)

        failed_logout = client.post("/api/v1/auth/register", json={
            "email": f"failed-logout-{run_id}@example.test",
            "password": "integration-only-password",
        })
        assert failed_logout.status_code == 201
        failed_token = failed_logout.json()["access_token"]
        failed_hash = hashlib.sha256(failed_token.encode()).hexdigest()

        try:
            with _admin_connection() as connection:
                connection.execute(
                    """
                    REVOKE EXECUTE ON FUNCTION
                    public.minicloud_cleanup_expired_token_revocations()
                    FROM minicloud_api_runtime
                    """
                )
            response = client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {failed_token}"},
            )
            assert response.status_code == 503
            assert "permission denied" not in response.text.lower()
        finally:
            with _admin_connection() as connection:
                connection.execute(
                    """
                    GRANT EXECUTE ON FUNCTION
                    public.minicloud_cleanup_expired_token_revocations()
                    TO minicloud_api_runtime
                    """
                )

        with _admin_connection() as connection:
            assert connection.execute(
                "SELECT count(*) FROM public.token_revocations WHERE token_hash = %s",
                (failed_hash,),
            ).fetchone() == (0,)


def test_zz_downgrade_preserves_revocation_table_and_rows() -> None:
    with _admin_connection() as connection:
        before = connection.execute(
            "SELECT count(*) FROM public.token_revocations"
        ).fetchone()[0]
    assert before > 0

    migration_env = os.environ.copy()
    migration_env["SERVICE_ROLE"] = "migrate"
    revision_downgrade = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "20261113_pagination_indexes",
        ],
        cwd=Path(__file__).parents[1],
        env=migration_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert revision_downgrade.returncode == 0, "revision-only downgrade failed"

    legacy_deployment_id = str(uuid.uuid4())
    with _admin_connection() as connection:
        owner_id, project_id = connection.execute(
            """
            SELECT owner.id, project.id
            FROM public.users AS owner
            JOIN public.projects AS project ON project.owner_id = owner.id
            ORDER BY project.created_at, project.id
            LIMIT 1
            """
        ).fetchone()
        connection.execute(
            """
            INSERT INTO public.deployments (
                id, project_id, owner_id, name, image, port, replicas, status,
                last_error, created_at, updated_at
            )
            VALUES (%s, %s, %s, 'legacy-backfill', 'nginx:1.26', 8080, 2,
                    'ready', NULL, now(), now())
            """,
            (legacy_deployment_id, project_id, owner_id),
        )

    revision_upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parents[1],
        env=migration_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert revision_upgrade.returncode == 0, "revision migration upgrade failed"
    with _admin_connection() as connection:
        backfilled_revision = connection.execute(
            """
            SELECT revision_number, name, image, port, replicas, created_by_user_id
            FROM public.deployment_revisions
            WHERE deployment_id = %s
            """,
            (legacy_deployment_id,),
        ).fetchone()
    assert backfilled_revision == (
        1,
        "legacy-backfill",
        "nginx:1.26",
        8080,
        2,
        owner_id,
    )

    cleanup_downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "20261012_token_revocations"],
        cwd=Path(__file__).parents[1],
        env=migration_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cleanup_downgrade.returncode == 0, "cleanup-only downgrade failed"

    destructive_downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "6c9831d5fb37"],
        cwd=Path(__file__).parents[1],
        env=migration_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert destructive_downgrade.returncode != 0
    assert "permanently delete logout revocation records" in (
        destructive_downgrade.stdout + destructive_downgrade.stderr
    )

    with _admin_connection() as connection:
        revision = connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()
        after = connection.execute(
            "SELECT count(*) FROM public.token_revocations"
        ).fetchone()[0]
    assert revision == ("20261012_token_revocations",)
    assert after == before
    engine.dispose()
