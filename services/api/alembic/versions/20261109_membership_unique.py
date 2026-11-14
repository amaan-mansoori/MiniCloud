"""Prevent duplicate project membership rows.

Revision ID: 20261109_membership_unique
Revises: 20261109_project_idem_scope
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "20261109_membership_unique"
down_revision = "20261109_project_idem_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM project_memberships
                GROUP BY project_id, user_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Duplicate project memberships exist; resolve them before adding the unique constraint';
            END IF;
        END
        $$
        """
    )
    op.create_unique_constraint(
        "uq_project_memberships_project_user",
        "project_memberships",
        ["project_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_project_memberships_project_user",
        "project_memberships",
        type_="unique",
    )
