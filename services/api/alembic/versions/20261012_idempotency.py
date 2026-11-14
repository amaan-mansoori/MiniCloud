"""Add a unique idempotency key to deployment jobs.

Revision ID: 20261012_idempotency
Revises: 6c9831d5fb37
Create Date: 2026-10-12 15:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20261012_idempotency"
down_revision = "6c9831d5fb37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_deployment_jobs_idempotency_key",
        "deployment_jobs",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_jobs_idempotency_key", table_name="deployment_jobs")
