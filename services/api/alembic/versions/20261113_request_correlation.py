"""Persist request IDs across deployment jobs and lifecycle events.

Revision ID: 20261113_request_correlation
Revises: 20261113_audit_events
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261113_request_correlation"
down_revision = "20261113_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_jobs",
        sa.Column("request_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "deployment_events",
        sa.Column("request_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_events", "request_id")
    op.drop_column("deployment_jobs", "request_id")
