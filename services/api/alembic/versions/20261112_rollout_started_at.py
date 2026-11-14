"""Persist the first Worker rollout observation time.

Revision ID: 20261112_rollout_started
Revises: 20261109_membership_unique
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261112_rollout_started"
down_revision = "20261109_membership_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE deployment_jobs
            SET started_at = COALESCE(deployments.updated_at, deployment_jobs.created_at)
            FROM deployments
            WHERE deployments.id = deployment_jobs.deployment_id
              AND (
                  deployment_jobs.state IN ('applying', 'progressing', 'timed_out')
                  OR deployment_jobs.attempts > 0
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("deployment_jobs", "started_at")
