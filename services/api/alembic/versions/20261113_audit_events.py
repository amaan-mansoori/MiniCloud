"""Persist append-only audit events for project mutations.

Revision ID: 20261113_audit_events
Revises: 20261113_operation_kind
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261113_audit_events"
down_revision = "20261113_operation_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "actor_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_events_project_created_id",
        "audit_events",
        ["project_id", "created_at", "id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION public.minicloud_reject_audit_event_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog
            AS $function$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only';
            END;
            $function$
            """
        )
        op.execute(
            """
            REVOKE ALL ON FUNCTION public.minicloud_reject_audit_event_mutation()
            FROM PUBLIC
            """
        )
        op.execute(
            """
            CREATE TRIGGER minicloud_audit_events_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON public.audit_events
            FOR EACH STATEMENT
            EXECUTE FUNCTION public.minicloud_reject_audit_event_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER minicloud_audit_events_append_only ON public.audit_events"
        )
        op.execute(
            "DROP FUNCTION public.minicloud_reject_audit_event_mutation()"
        )
    op.drop_index("ix_audit_events_project_created_id", table_name="audit_events")
    op.drop_table("audit_events")
