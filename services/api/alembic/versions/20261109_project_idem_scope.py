"""Scope deployment idempotency keys to their project.

Revision ID: 20261109_project_idem_scope
Revises: 20261012_revocation_cleanup
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261109_project_idem_scope"
down_revision = "20261012_revocation_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_jobs",
        sa.Column("project_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE deployment_jobs
            SET project_id = deployments.project_id
            FROM deployments
            WHERE deployments.id = deployment_jobs.deployment_id
            """
        )
    )
    op.alter_column(
        "deployment_jobs",
        "project_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_deployment_jobs_project_id_projects",
        "deployment_jobs",
        "projects",
        ["project_id"],
        ["id"],
    )
    op.drop_index(
        "ix_deployment_jobs_idempotency_key",
        table_name="deployment_jobs",
    )
    op.create_index(
        "uq_deployment_jobs_project_id_idempotency_key",
        "deployment_jobs",
        ["project_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM deployment_jobs
                    WHERE idempotency_key IS NOT NULL
                    GROUP BY idempotency_key
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot restore globally unique deployment idempotency keys while cross-project duplicates exist';
                END IF;
            END
            $$
            """
        )
    )
    op.drop_index(
        "uq_deployment_jobs_project_id_idempotency_key",
        table_name="deployment_jobs",
    )
    op.create_index(
        "ix_deployment_jobs_idempotency_key",
        "deployment_jobs",
        ["idempotency_key"],
        unique=True,
    )
    op.drop_constraint(
        "fk_deployment_jobs_project_id_projects",
        "deployment_jobs",
        type_="foreignkey",
    )
    op.drop_column("deployment_jobs", "project_id")
