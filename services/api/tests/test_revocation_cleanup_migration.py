from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20261012_revocation_cleanup_function.py"
)
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "revocation_cleanup_migration", MIGRATION_PATH
)
assert MIGRATION_SPEC is not None
assert MIGRATION_SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(MIGRATION)

TOKEN_MIGRATION_PATH = MIGRATION_PATH.with_name("20261012_token_revocations.py")
TOKEN_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "token_revocations_migration", TOKEN_MIGRATION_PATH
)
assert TOKEN_MIGRATION_SPEC is not None
assert TOKEN_MIGRATION_SPEC.loader is not None
TOKEN_MIGRATION = importlib.util.module_from_spec(TOKEN_MIGRATION_SPEC)
TOKEN_MIGRATION_SPEC.loader.exec_module(TOKEN_MIGRATION)

IDEMPOTENCY_MIGRATION_PATH = MIGRATION_PATH.with_name("20261012_idempotency.py")
IDEMPOTENCY_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "idempotency_migration", IDEMPOTENCY_MIGRATION_PATH
)
assert IDEMPOTENCY_MIGRATION_SPEC is not None
assert IDEMPOTENCY_MIGRATION_SPEC.loader is not None
IDEMPOTENCY_MIGRATION = importlib.util.module_from_spec(IDEMPOTENCY_MIGRATION_SPEC)
IDEMPOTENCY_MIGRATION_SPEC.loader.exec_module(IDEMPOTENCY_MIGRATION)

PROJECT_SCOPE_MIGRATION_PATH = MIGRATION_PATH.with_name("20261109_project_idem_scope.py")
PROJECT_SCOPE_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "project_scope_idempotency_migration", PROJECT_SCOPE_MIGRATION_PATH
)
assert PROJECT_SCOPE_MIGRATION_SPEC is not None
assert PROJECT_SCOPE_MIGRATION_SPEC.loader is not None
PROJECT_SCOPE_MIGRATION = importlib.util.module_from_spec(PROJECT_SCOPE_MIGRATION_SPEC)
PROJECT_SCOPE_MIGRATION_SPEC.loader.exec_module(PROJECT_SCOPE_MIGRATION)

MEMBERSHIP_MIGRATION_PATH = MIGRATION_PATH.with_name("20261109_membership_unique.py")
MEMBERSHIP_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "project_membership_unique_migration", MEMBERSHIP_MIGRATION_PATH
)
assert MEMBERSHIP_MIGRATION_SPEC is not None
assert MEMBERSHIP_MIGRATION_SPEC.loader is not None
MEMBERSHIP_MIGRATION = importlib.util.module_from_spec(MEMBERSHIP_MIGRATION_SPEC)
MEMBERSHIP_MIGRATION_SPEC.loader.exec_module(MEMBERSHIP_MIGRATION)

ROLLOUT_STARTED_MIGRATION_PATH = MEMBERSHIP_MIGRATION_PATH.with_name(
    "20261112_rollout_started_at.py"
)
ROLLOUT_STARTED_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "rollout_started_at_migration", ROLLOUT_STARTED_MIGRATION_PATH
)
assert ROLLOUT_STARTED_MIGRATION_SPEC is not None
assert ROLLOUT_STARTED_MIGRATION_SPEC.loader is not None
ROLLOUT_STARTED_MIGRATION = importlib.util.module_from_spec(
    ROLLOUT_STARTED_MIGRATION_SPEC
)
ROLLOUT_STARTED_MIGRATION_SPEC.loader.exec_module(ROLLOUT_STARTED_MIGRATION)


def test_migration_revision_follows_token_table_and_fits_alembic_column():
    assert MIGRATION.down_revision == "20261012_token_revocations"
    assert len(MIGRATION.revision) <= 32


def test_phase_eight_migrations_form_a_single_additive_chain():
    assert IDEMPOTENCY_MIGRATION.down_revision == "6c9831d5fb37"
    assert TOKEN_MIGRATION.down_revision == IDEMPOTENCY_MIGRATION.revision
    assert MIGRATION.down_revision == TOKEN_MIGRATION.revision
    assert PROJECT_SCOPE_MIGRATION.down_revision == MIGRATION.revision
    assert len(PROJECT_SCOPE_MIGRATION.revision) <= 32
    assert MEMBERSHIP_MIGRATION.down_revision == PROJECT_SCOPE_MIGRATION.revision
    assert len(MEMBERSHIP_MIGRATION.revision) <= 32
    assert ROLLOUT_STARTED_MIGRATION.down_revision == MEMBERSHIP_MIGRATION.revision
    assert len(ROLLOUT_STARTED_MIGRATION.revision) <= 32


def test_rollout_started_migration_adds_nullable_timestamp_and_backfills_active_jobs(monkeypatch):
    operations = []
    monkeypatch.setattr(
        ROLLOUT_STARTED_MIGRATION,
        "op",
        SimpleNamespace(
            add_column=lambda *args: operations.append(("add_column", args)),
            execute=lambda statement: operations.append(("execute", str(statement))),
        ),
    )

    ROLLOUT_STARTED_MIGRATION.upgrade()

    assert operations[0][0] == "add_column"
    assert operations[0][1][0] == "deployment_jobs"
    column = operations[0][1][1]
    assert column.name == "started_at"
    assert column.nullable is True
    assert operations[1][0] == "execute"
    assert "COALESCE(deployments.updated_at, deployment_jobs.created_at)" in operations[1][1]
    assert "deployment_jobs.state IN ('applying', 'progressing', 'timed_out')" in operations[1][1]


def test_rollout_started_migration_downgrade_only_drops_added_column(monkeypatch):
    operations = []
    monkeypatch.setattr(
        ROLLOUT_STARTED_MIGRATION,
        "op",
        SimpleNamespace(drop_column=lambda *args: operations.append(args)),
    )

    ROLLOUT_STARTED_MIGRATION.downgrade()

    assert operations == [("deployment_jobs", "started_at")]


def test_membership_migration_rejects_duplicate_rows_before_adding_constraint(monkeypatch):
    operations = []
    monkeypatch.setattr(
        MEMBERSHIP_MIGRATION,
        "op",
        SimpleNamespace(
            execute=lambda sql: operations.append(("execute", str(sql))),
            create_unique_constraint=lambda *args: operations.append(("create", args)),
        ),
    )

    MEMBERSHIP_MIGRATION.upgrade()

    assert operations[0][0] == "execute"
    assert "GROUP BY project_id, user_id" in operations[0][1]
    assert "Duplicate project memberships exist" in operations[0][1]
    assert operations[1] == (
        "create",
        (
            "uq_project_memberships_project_user",
            "project_memberships",
            ["project_id", "user_id"],
        ),
    )


def test_membership_migration_downgrade_only_removes_unique_constraint(monkeypatch):
    operations = []
    monkeypatch.setattr(
        MEMBERSHIP_MIGRATION,
        "op",
        SimpleNamespace(drop_constraint=lambda *args, **kwargs: operations.append((args, kwargs))),
    )

    MEMBERSHIP_MIGRATION.downgrade()

    assert operations == [
        (
            (
                "uq_project_memberships_project_user",
                "project_memberships",
            ),
            {"type_": "unique"},
        )
    ]


def test_project_scoped_idempotency_migration_backfills_before_unique_index(monkeypatch):
    operations = []

    def record(name):
        return lambda *args, **kwargs: operations.append((name, args, kwargs))

    monkeypatch.setattr(
        PROJECT_SCOPE_MIGRATION,
        "op",
        SimpleNamespace(
            add_column=record("add_column"),
            execute=record("execute"),
            alter_column=record("alter_column"),
            create_foreign_key=record("create_foreign_key"),
            drop_index=record("drop_index"),
            create_index=record("create_index"),
        ),
    )

    PROJECT_SCOPE_MIGRATION.upgrade()

    operation_names = [operation[0] for operation in operations]
    assert operation_names == [
        "add_column",
        "execute",
        "alter_column",
        "create_foreign_key",
        "drop_index",
        "create_index",
    ]
    assert "UPDATE deployment_jobs" in str(operations[1][1][0])
    assert "deployments.project_id" in str(operations[1][1][0])
    assert operations[-1][1][0] == "uq_deployment_jobs_project_id_idempotency_key"
    assert operations[-1][1][2] == ["project_id", "idempotency_key"]
    assert operations[-1][2]["unique"] is True


def test_cleanup_migration_defines_restricted_function_without_running_sql(monkeypatch):
    statements = []
    monkeypatch.setattr(
        MIGRATION,
        "op",
        SimpleNamespace(execute=statements.append),
    )

    MIGRATION.upgrade()
    sql = "\n".join(statements)

    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog" in sql
    assert "DELETE FROM public.token_revocations" in sql
    assert "WHERE expires_at <= pg_catalog.now()" in sql
    assert "ALTER FUNCTION public.minicloud_cleanup_expired_token_revocations() " in sql
    assert "OWNER TO minicloud_revocation_maintenance" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert "FROM PUBLIC" in sql
    assert "to_regclass('public.token_revocations')" in sql
    assert "minicloud_revocation_maintenance already exists" in sql
    assert "Maintenance role attributes or memberships are unsafe" in sql
    assert "GRANT SELECT (expires_at) ON TABLE public.token_revocations" in sql
    assert "Maintenance role must have SELECT only on token_revocations.expires_at" in sql
    assert "proc.proconfig @> ARRAY['search_path=pg_catalog']" in sql
    assert "acl.grantee <> proc.proowner" in sql
    assert "Maintenance role has unexpected sequence privileges" in sql


def test_cleanup_migration_downgrade_preserves_revocation_table_and_rows(monkeypatch):
    statements = []
    monkeypatch.setattr(
        MIGRATION,
        "op",
        SimpleNamespace(execute=statements.append),
    )

    MIGRATION.downgrade()
    sql = "\n".join(statements).upper()

    assert "DROP FUNCTION" in sql
    assert "DROP ROLE MINICLOUD_REVOCATION_MAINTENANCE" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM PUBLIC.TOKEN_REVOCATIONS" not in sql
    assert "REVOKE SELECT (EXPIRES_AT)" in sql


def test_token_revocation_migration_refuses_data_destroying_downgrade():
    with pytest.raises(RuntimeError, match="permanently delete logout revocation records"):
        TOKEN_MIGRATION.downgrade()
