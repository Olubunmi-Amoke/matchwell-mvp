"""Create community matching, introductions, and safety tables.

Revision ID: 20260902_0003
Revises: 20260902_0002
Create Date: 2026-09-02

Backfill and safety strategy: ``member_match_preferences`` is a brand-new,
opt-in table rather than new nullable columns bolted onto
``member_profiles``. Every existing hosted member -- including everyone who
completed the first-milestone readiness journey before this migration -- has
no row in this table until they explicitly complete the new gender and
partner age-range preferences. Candidate generation and matching-pool
membership both require a completed row, so no existing member becomes
silently matching-eligible when this migration runs. No data backfill of
guessed preferences is performed or desired.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0003"
down_revision: str | None = "20260902_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_match_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("gender", sa.String(length=10), nullable=False),
        sa.Column("min_partner_age", sa.Integer(), nullable=False),
        sa.Column("max_partner_age", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "match_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("member_a_id", sa.Uuid(), nullable=False),
        sa.Column("member_b_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("counselor_a_id", sa.Uuid(), nullable=True),
        sa.Column("counselor_a_decision", sa.String(length=20), nullable=False),
        sa.Column("counselor_a_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counselor_a_reason_code", sa.String(length=100), nullable=True),
        sa.Column("counselor_b_id", sa.Uuid(), nullable=True),
        sa.Column("counselor_b_decision", sa.String(length=20), nullable=False),
        sa.Column("counselor_b_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counselor_b_reason_code", sa.String(length=100), nullable=True),
        sa.Column("introduced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("member_a_response", sa.String(length=20), nullable=True),
        sa.Column("member_a_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("member_b_response", sa.String(length=20), nullable=True),
        sa.Column("member_b_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"]),
        sa.ForeignKeyConstraint(["counselor_a_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["counselor_b_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["member_a_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["member_b_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_a_id", "member_b_id"),
    )
    op.create_index(
        "ix_match_proposals_community_status",
        "match_proposals",
        ["community_id", "status"],
    )
    op.create_index(
        "ix_match_proposals_counselor_a",
        "match_proposals",
        ["counselor_a_id", "status"],
    )
    op.create_index(
        "ix_match_proposals_counselor_b",
        "match_proposals",
        ["counselor_b_id", "status"],
    )
    op.create_table(
        "member_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("blocker_id", sa.Uuid(), nullable=False),
        sa.Column("blocked_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("context", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["blocked_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["blocker_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_id", "blocked_id"),
    )
    op.create_table(
        "member_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("reported_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("context", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["reported_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_member_reports_reported_id",
        "member_reports",
        ["reported_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_member_reports_reported_id", table_name="member_reports")
    op.drop_table("member_reports")
    op.drop_table("member_blocks")
    op.drop_index("ix_match_proposals_counselor_b", table_name="match_proposals")
    op.drop_index("ix_match_proposals_counselor_a", table_name="match_proposals")
    op.drop_index("ix_match_proposals_community_status", table_name="match_proposals")
    op.drop_table("match_proposals")
    op.drop_table("member_match_preferences")
