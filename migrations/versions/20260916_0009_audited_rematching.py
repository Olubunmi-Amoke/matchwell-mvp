"""Audited, counselor-approved rematching.

Revision ID: 20260916_0009
Revises: 20260916_0008
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0009"
down_revision: str | None = "20260916_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN = "status IN ('pending_review', 'introduced', 'active')"
_LIVE_AUTH = "status IN ('pending', 'approved')"


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(
            "match_proposals",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            batch.drop_constraint("uq_match_proposals_member_a_id", type_="unique")
            batch.create_check_constraint(
                "ck_match_proposals_distinct_members",
                "member_a_id <> member_b_id",
            )
    else:
        op.drop_constraint(
            "match_proposals_member_a_id_member_b_id_key",
            "match_proposals",
            type_="unique",
        )
        op.create_check_constraint(
            "ck_match_proposals_distinct_members",
            "match_proposals",
            "member_a_id <> member_b_id",
        )
    op.create_index(
        "ix_match_proposals_center_pair",
        "match_proposals",
        ["center_id", "member_a_id", "member_b_id"],
    )
    op.create_index(
        "uq_match_proposals_open_pair",
        "match_proposals",
        ["center_id", "member_a_id", "member_b_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN),
        sqlite_where=sa.text(_OPEN),
    )
    op.create_table(
        "match_proposal_participant_claims",
        sa.Column(
            "proposal_id",
            sa.Uuid(),
            sa.ForeignKey("match_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("proposal_id", "member_id"),
        sa.UniqueConstraint(
            "member_id", name="uq_match_proposal_participant_claims_member_id"
        ),
    )
    # A duplicate member across either side fails this insert and aborts the
    # migration rather than silently choosing one of the conflicting proposals.
    op.execute(
        sa.text(
            "INSERT INTO match_proposal_participant_claims (proposal_id, member_id) "
            "SELECT id, member_a_id FROM match_proposals WHERE " + _OPEN + " "
            "UNION ALL "
            "SELECT id, member_b_id FROM match_proposals WHERE " + _OPEN
        )
    )

    op.create_table(
        "rematch_authorizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("center_id", sa.Uuid(), sa.ForeignKey("centers.id"), nullable=False),
        sa.Column(
            "community_id", sa.Uuid(), sa.ForeignKey("communities.id"), nullable=False
        ),
        sa.Column("member_a_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("member_b_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "requested_by_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "counselor_a_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "counselor_assignment_a_id",
            sa.Uuid(),
            sa.ForeignKey("counselor_assignments.id"),
            nullable=False,
        ),
        sa.Column(
            "counselor_b_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "counselor_assignment_b_id",
            sa.Uuid(),
            sa.ForeignKey("counselor_assignments.id"),
            nullable=False,
        ),
        sa.Column("counselor_a_approved_by_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("counselor_a_approved_at", sa.DateTime(timezone=True)),
        sa.Column("counselor_b_approved_by_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("counselor_b_approved_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "consumed_proposal_id",
            sa.Uuid(),
            sa.ForeignKey("match_proposals.id"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("revocation_reason_code", sa.String(50)),
        sa.CheckConstraint(
            "member_a_id <> member_b_id",
            name="ck_rematch_authorizations_distinct_members",
        ),
        sa.CheckConstraint(
            "counselor_a_id <> counselor_b_id",
            name="ck_rematch_authorizations_distinct_counselors",
        ),
        sa.CheckConstraint(
            "counselor_a_approved_by_id IS NULL "
            "OR counselor_b_approved_by_id IS NULL "
            "OR counselor_a_approved_by_id <> counselor_b_approved_by_id",
            name="ck_rematch_authorizations_distinct_approvers",
        ),
        sa.CheckConstraint(
            "reason_code IN ('member_decline_reconsidered', "
            "'counselor_decline_reconsidered', 'entitlement_restored', "
            "'circumstances_changed', 'operations_correction')",
            name="ck_rematch_authorizations_reason",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'consumed', 'revoked')",
            name="ck_rematch_authorizations_status",
        ),
        sa.CheckConstraint(
            "revocation_reason_code IS NULL "
            "OR revocation_reason_code = 'assignment_changed'",
            name="ck_rematch_authorizations_revocation_reason",
        ),
    )
    op.create_index(
        "ix_rematch_authorizations_center_pair_status",
        "rematch_authorizations",
        ["center_id", "member_a_id", "member_b_id", "status"],
    )
    op.create_index(
        "uq_rematch_authorizations_live_pair",
        "rematch_authorizations",
        ["center_id", "member_a_id", "member_b_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_AUTH),
        sqlite_where=sa.text(_LIVE_AUTH),
    )


def downgrade() -> None:
    context = op.get_context()
    repeated_pair_query = (
        "SELECT 1 FROM match_proposals "
        "GROUP BY center_id, "
        "CASE WHEN member_a_id < member_b_id THEN member_a_id ELSE member_b_id END, "
        "CASE WHEN member_a_id < member_b_id THEN member_b_id ELSE member_a_id END "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )
    if context.as_sql:
        if context.dialect.name == "postgresql":
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    f"IF EXISTS ({repeated_pair_query}) THEN "
                    "RAISE EXCEPTION 'Cannot downgrade audited rematching after "
                    "repeat pair history exists; immutable proposal history will "
                    "not be deleted or merged.'; "
                    "END IF; END $$"
                )
            )
    else:
        repeated_pair = op.get_bind().execute(sa.text(repeated_pair_query)).first()
        if repeated_pair is not None:
            raise RuntimeError(
                "Cannot downgrade audited rematching after repeat pair history exists; "
                "immutable proposal history will not be deleted or merged."
            )
    op.drop_index(
        "uq_rematch_authorizations_live_pair", table_name="rematch_authorizations"
    )
    op.drop_index(
        "ix_rematch_authorizations_center_pair_status",
        table_name="rematch_authorizations",
    )
    op.drop_table("rematch_authorizations")
    op.drop_table("match_proposal_participant_claims")
    op.drop_index("uq_match_proposals_open_pair", table_name="match_proposals")
    op.drop_index("ix_match_proposals_center_pair", table_name="match_proposals")
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("match_proposals") as batch:
            batch.drop_constraint("ck_match_proposals_distinct_members", type_="check")
            batch.create_unique_constraint(
                "uq_match_proposals_member_a_id", ["member_a_id", "member_b_id"]
            )
    else:
        op.drop_constraint(
            "ck_match_proposals_distinct_members",
            "match_proposals",
            type_="check",
        )
        op.create_unique_constraint(
            "match_proposals_member_a_id_member_b_id_key",
            "match_proposals",
            ["member_a_id", "member_b_id"],
        )
