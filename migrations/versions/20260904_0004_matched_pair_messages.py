"""Create private matched-pair messages.

Revision ID: 20260904_0004
Revises: 20260902_0003
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0004"
down_revision: str | None = "20260902_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matched_pair_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["match_proposals.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_matched_pair_messages_proposal_sent",
        "matched_pair_messages",
        ["proposal_id", "sent_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_matched_pair_messages_proposal_sent",
        table_name="matched_pair_messages",
    )
    op.drop_table("matched_pair_messages")
