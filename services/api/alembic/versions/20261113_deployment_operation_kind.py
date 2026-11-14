"""Persist deployment operation kind and requested replica count.

Revision ID: 20261113_operation_kind
Revises: 20261112_rollout_started
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261113_operation_kind"
down_revision = "20261112_rollout_started"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_jobs",
        sa.Column(
            "operation_type",
            sa.String(length=40),
            server_default="deploy",
            nullable=False,
        ),
    )
    op.add_column(
        "deployment_jobs",
        sa.Column("target_replicas", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE deployment_jobs
            SET target_replicas = (
                SELECT deployments.replicas
                FROM deployments
                WHERE deployments.id = deployment_jobs.deployment_id
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("deployment_jobs", "target_replicas")
    op.drop_column("deployment_jobs", "operation_type")
