"""Persist project resource policies and quota mutation replay records.

Revision ID: 20261114_resource_policies
Revises: 20261114_deployment_revisions
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261114_resource_policies"
down_revision = "20261114_deployment_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_policies",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("max_replicas", sa.Integer(), nullable=False),
        sa.Column("cpu_request_millicores", sa.BigInteger(), nullable=False),
        sa.Column("cpu_limit_millicores", sa.BigInteger(), nullable=False),
        sa.Column("memory_request_bytes", sa.BigInteger(), nullable=False),
        sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column(
            "updated_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", name="uq_resource_policies_project_id"),
        sa.CheckConstraint(
            "max_replicas BETWEEN 1 AND 4",
            name="ck_resource_policies_max_replicas",
        ),
        sa.CheckConstraint(
            "cpu_request_millicores > 0 AND cpu_request_millicores <= cpu_limit_millicores",
            name="ck_resource_policies_cpu_bounds",
        ),
        sa.CheckConstraint(
            "memory_request_bytes > 0 AND memory_request_bytes <= memory_limit_bytes",
            name="ck_resource_policies_memory_bounds",
        ),
        sa.CheckConstraint(
            "version > 0 AND (applied_version IS NULL OR applied_version <= version)",
            name="ck_resource_policies_version",
        ),
    )
    op.create_table(
        "resource_policy_mutations",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_resource_policy_mutations_project_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("resource_policy_mutations")
    op.drop_table("resource_policies")
