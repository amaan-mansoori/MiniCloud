"""Static policy tests for the proposed PostgreSQL grant script.

These tests inspect SQL text only; they do not parse or execute it against
PostgreSQL and are not PostgreSQL integration tests.
"""

from __future__ import annotations

import re
from pathlib import Path

GRANT_SCRIPT = (
    Path(__file__).parents[3] / "scripts" / "grant_minicloud_runtime.sql"
).read_text(encoding="utf-8")


def test_runtime_table_grants_match_the_reviewed_operation_matrix():
    grants = re.findall(
        r"^GRANT\s+(.+?)\s+ON\s+TABLE\s+([\w.]+)\s+TO\s+minicloud_api_runtime;",
        GRANT_SCRIPT,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    actual = {
        table.lower(): {privilege.strip().upper() for privilege in privileges.split(",")}
        for privileges, table in grants
    }

    assert actual == {
        "public.users": {"SELECT", "INSERT"},
        "public.projects": {"SELECT", "INSERT", "UPDATE"},
        "public.project_memberships": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "public.deployments": {"SELECT", "INSERT", "UPDATE"},
        "public.deployment_jobs": {"SELECT", "INSERT", "UPDATE"},
        "public.deployment_events": {"SELECT", "INSERT"},
        "public.deployment_revisions": {"SELECT", "INSERT"},
        "public.resource_policies": {"SELECT", "INSERT", "UPDATE"},
        "public.resource_policy_mutations": {"SELECT", "INSERT"},
        "public.audit_events": {"INSERT"},
        "public.token_revocations": {"SELECT", "INSERT"},
    }
    assert "GRANT DELETE ON ALL TABLES" not in GRANT_SCRIPT.upper()
    assert "GRANT TRUNCATE" not in GRANT_SCRIPT.upper()
    assert "GRANT USAGE ON ALL SEQUENCES" not in GRANT_SCRIPT.upper()
    assert "ALTER DEFAULT PRIVILEGES" not in GRANT_SCRIPT.upper()

    worker_grants = re.findall(
        r"^GRANT\s+(.+?)\s+ON\s+TABLE\s+([\w.]+)\s+TO\s+minicloud_worker_runtime;",
        GRANT_SCRIPT,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    worker_actual = {
        table.lower(): {privilege.strip().upper() for privilege in privileges.split(",")}
        for privileges, table in worker_grants
    }
    assert worker_actual == {
        "public.deployments": {"SELECT", "UPDATE"},
        "public.deployment_jobs": {"SELECT", "UPDATE"},
        "public.deployment_events": {"INSERT"},
    }
    assert "legacy shared minicloud_runtime role exists" in GRANT_SCRIPT.lower()
    assert "minicloud_worker_runtime" in GRANT_SCRIPT


def test_grant_script_requires_the_current_migration_head():
    assert "version_num = '20261114_resource_policies'" in GRANT_SCRIPT
    assert "Expected Alembic revision 20261114_resource_policies" in GRANT_SCRIPT


def test_grant_script_fails_closed_for_existing_role_and_effective_access():
    sql = GRANT_SCRIPT.lower()

    assert "pg_catalog.pg_auth_members" in sql
    assert "roleid = runtime_role_oid or member = runtime_role_oid" in sql
    assert "roleid = worker_role_oid or member = worker_role_oid" in sql
    assert "roleid = maintenance_role_oid or member = maintenance_role_oid" in sql
    assert "pg_catalog.pg_shdepend" in sql
    assert "unexpected objects" in sql
    assert "pg_catalog.pg_db_role_setting" in sql
    assert "setting.value like 'search_path=%'" in sql
    assert "setrole in (" in sql
    assert "runtime_role_oid, worker_role_oid, maintenance_role_oid" in sql
    assert "setdatabase in (0, database_oid)" in sql
    assert "pg_catalog.has_schema_privilege" in sql
    assert "pg_catalog.has_table_privilege" in sql
    assert "pg_catalog.has_sequence_privilege" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert "rolcanlogin or rolsuper or rolcreatedb or rolcreaterole" in sql
    assert "rolreplication or rolbypassrls or rolinherit" in sql
    assert "pg_catalog.has_database_privilege" in sql
    assert "maintenance role has direct database create or temp" in sql
    assert "acl.grantee = maintenance_role_oid" in sql
    assert "pg_catalog.has_sequence_privilege" in sql
    assert "maintenance role has unexpected non-system sequence privileges" in sql
    assert "pg_catalog.has_column_privilege" in sql
    assert "maintenance role must have select only on token_revocations.expires_at" in sql
    assert "required_relation_kind not in ('r', 'p')" in sql
    assert "relation.relname = 'deployment_events'" in sql
    assert "priv.name = 'insert'" in sql
    assert "defaclobjtype in ('r', 's', 'f', 't', 'n')" in sql
    assert "future_acl.defaclnamespace = 0" not in sql
    assert "default privileges expose future objects" in sql


def test_maintenance_acl_preflight_does_not_revoke_or_rewrite_existing_acl():
    sql = GRANT_SCRIPT.upper()

    revoke_targets = re.findall(
        r"^REVOKE\b[^;]*?\bFROM\s+([^;]+);",
        sql,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert revoke_targets
    assert set(revoke_targets) == {
        "MINICLOUD_API_RUNTIME",
        "MINICLOUD_WORKER_RUNTIME",
    }
    assert "ALTER DATABASE" not in sql
    assert "ALTER ROLE MINICLOUD_REVOCATION_MAINTENANCE" not in sql
    assert "PG_CATALOG.HAS_DATABASE_PRIVILEGE" in sql
    assert "PG_CATALOG.HAS_SCHEMA_PRIVILEGE" in sql
    assert "FROM MINICLOUD_API_RUNTIME" in sql
    assert "FROM MINICLOUD_WORKER_RUNTIME" in sql
    assert "FROM MINICLOUD_REVOCATION_MAINTENANCE" not in sql


def test_grant_script_preserves_public_temp_boundary_and_rolls_back_on_detection():
    sql = GRANT_SCRIPT.lower()

    assert "revoke create, temporary on database minicloud from minicloud_api_runtime" in sql
    assert "revoke create, temporary on database minicloud from minicloud_worker_runtime" in sql
    assert "'minicloud_api_runtime', 'minicloud', 'temp'" in sql
    assert "'minicloud_worker_runtime', 'minicloud', 'temp'" in sql
    assert "runtime role can create or temp in minicloud" in sql
    assert "effective temp is checked below" in sql
    assert "acl.is_grantable" in sql
    assert "\\set on_error_stop on" in sql
    assert "begin;" in sql
    assert "commit;" in sql
