"""Persist immutable deployment revisions and link accepted jobs.

Revision ID: 20261114_deployment_revisions
Revises: 20261113_pagination_indexes
Create Date: 2026-11-14
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261114_deployment_revisions"
down_revision = "20261113_pagination_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "deployment_id",
            sa.String(length=36),
            sa.ForeignKey("deployments.id"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("image", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("replicas", sa.Integer(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "rollback_of_revision_id",
            sa.String(length=36),
            sa.ForeignKey("deployment_revisions.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "deployment_id",
            "revision_number",
            name="uq_deployment_revisions_deployment_number",
        ),
    )
    op.create_index(
        "ix_deployment_revisions_deployment_created_id",
        "deployment_revisions",
        ["deployment_id", "created_at", "id"],
    )

    bind = op.get_bind()
    deployments = sa.table(
        "deployments",
        sa.column("id", sa.String(length=36)),
        sa.column("owner_id", sa.String(length=36)),
        sa.column("name", sa.String(length=120)),
        sa.column("image", sa.String(length=255)),
        sa.column("port", sa.Integer()),
        sa.column("replicas", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    revisions = sa.table(
        "deployment_revisions",
        sa.column("id", sa.String(length=36)),
        sa.column("deployment_id", sa.String(length=36)),
        sa.column("revision_number", sa.Integer()),
        sa.column("name", sa.String(length=120)),
        sa.column("image", sa.String(length=255)),
        sa.column("port", sa.Integer()),
        sa.column("replicas", sa.Integer()),
        sa.column("created_by_user_id", sa.String(length=36)),
        sa.column("rollback_of_revision_id", sa.String(length=36)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for deployment in bind.execute(sa.select(deployments)).mappings():
        op.bulk_insert(
            revisions,
            [
                {
                    "id": str(uuid.uuid4()),
                    "deployment_id": deployment["id"],
                    "revision_number": 1,
                    "name": deployment["name"],
                    "image": deployment["image"],
                    "port": deployment["port"],
                    "replicas": deployment["replicas"],
                    "created_by_user_id": deployment["owner_id"],
                    "rollback_of_revision_id": None,
                    "created_at": deployment["created_at"],
                }
            ],
        )

    op.add_column(
        "deployment_jobs",
        sa.Column("revision_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_deployment_jobs_revision_id",
        "deployment_jobs",
        "deployment_revisions",
        ["revision_id"],
        ["id"],
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION public.minicloud_reject_deployment_revision_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $function$
            BEGIN
                RAISE EXCEPTION 'deployment_revisions is append-only';
            END;
            $function$
            """
        )
        op.execute(
            """
            REVOKE ALL ON FUNCTION
                public.minicloud_reject_deployment_revision_mutation()
            FROM PUBLIC
            """
        )
        op.execute(
            """
            CREATE TRIGGER minicloud_deployment_revisions_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON public.deployment_revisions
            FOR EACH STATEMENT
            EXECUTE FUNCTION public.minicloud_reject_deployment_revision_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER minicloud_deployment_revisions_append_only "
            "ON public.deployment_revisions"
        )
        op.execute(
            "DROP FUNCTION public.minicloud_reject_deployment_revision_mutation()"
        )
    op.drop_constraint(
        "fk_deployment_jobs_revision_id",
        "deployment_jobs",
        type_="foreignkey",
    )
    op.drop_column("deployment_jobs", "revision_id")
    op.drop_index(
        "ix_deployment_revisions_deployment_created_id",
        table_name="deployment_revisions",
    )
    op.drop_table("deployment_revisions")
