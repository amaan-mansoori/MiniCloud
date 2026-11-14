"""Add indexes for cursor-paginated project, deployment, and event reads.

Revision ID: 20261113_pagination_indexes
Revises: 20261113_request_correlation
Create Date: 2026-11-13
"""

from __future__ import annotations

from alembic import op

revision = "20261113_pagination_indexes"
down_revision = "20261113_request_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_projects_created_id",
        "projects",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_project_memberships_user_project",
        "project_memberships",
        ["user_id", "project_id"],
    )
    op.create_index(
        "ix_deployments_project_created_id",
        "deployments",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_deployment_events_deployment_created_id",
        "deployment_events",
        ["deployment_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deployment_events_deployment_created_id",
        table_name="deployment_events",
    )
    op.drop_index(
        "ix_deployments_project_created_id",
        table_name="deployments",
    )
    op.drop_index(
        "ix_project_memberships_user_project",
        table_name="project_memberships",
    )
    op.drop_index("ix_projects_created_id", table_name="projects")
