"""Persist revoked access-token fingerprints until their expiry."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261012_token_revocations"
down_revision = "20261012_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_revocations",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "ix_token_revocations_expires_at",
        "token_revocations",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "The token_revocations migration is irreversible because dropping its "
        "table would permanently delete logout revocation records."
    )
