"""Add optional personality inventory and community matching modes.

Revision ID: 20260916_0010
Revises: 20260916_0009
Create Date: 2026-09-16
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0010"
down_revision: str | None = "20260916_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PILOT_CENTER_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
SELF_PACED_COMMUNITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000202")
PERSONALITY_DEFINITION_ID = uuid.UUID("00000000-0000-0000-0000-000000000402")

ITEMS = [
    {
        "id": "o1",
        "prompt": "I have a vivid imagination.",
        "trait": "openness_intellect",
        "reverse_keyed": False,
    },
    {
        "id": "o2",
        "prompt": "I am interested in abstract ideas.",
        "trait": "openness_intellect",
        "reverse_keyed": False,
    },
    {
        "id": "o3",
        "prompt": "I have difficulty understanding abstract ideas.",
        "trait": "openness_intellect",
        "reverse_keyed": True,
    },
    {
        "id": "o4",
        "prompt": "I do not have a good imagination.",
        "trait": "openness_intellect",
        "reverse_keyed": True,
    },
    {
        "id": "c1",
        "prompt": "I get chores done right away.",
        "trait": "conscientiousness",
        "reverse_keyed": False,
    },
    {
        "id": "c2",
        "prompt": "I like order.",
        "trait": "conscientiousness",
        "reverse_keyed": False,
    },
    {
        "id": "c3",
        "prompt": "I often forget to put things back in their proper place.",
        "trait": "conscientiousness",
        "reverse_keyed": True,
    },
    {
        "id": "c4",
        "prompt": "I make a mess of things.",
        "trait": "conscientiousness",
        "reverse_keyed": True,
    },
    {
        "id": "e1",
        "prompt": "I am the life of the party.",
        "trait": "extraversion",
        "reverse_keyed": False,
    },
    {
        "id": "e2",
        "prompt": "I talk to a lot of different people at parties.",
        "trait": "extraversion",
        "reverse_keyed": False,
    },
    {
        "id": "e3",
        "prompt": "I do not talk a lot.",
        "trait": "extraversion",
        "reverse_keyed": True,
    },
    {
        "id": "e4",
        "prompt": "I keep in the background.",
        "trait": "extraversion",
        "reverse_keyed": True,
    },
    {
        "id": "a1",
        "prompt": "I sympathize with others' feelings.",
        "trait": "agreeableness",
        "reverse_keyed": False,
    },
    {
        "id": "a2",
        "prompt": "I feel others' emotions.",
        "trait": "agreeableness",
        "reverse_keyed": False,
    },
    {
        "id": "a3",
        "prompt": "I am not interested in other people's problems.",
        "trait": "agreeableness",
        "reverse_keyed": True,
    },
    {
        "id": "a4",
        "prompt": "I insult people.",
        "trait": "agreeableness",
        "reverse_keyed": True,
    },
    {
        "id": "s1",
        "prompt": "I am relaxed most of the time.",
        "trait": "emotional_stability",
        "reverse_keyed": False,
    },
    {
        "id": "s2",
        "prompt": "I seldom feel blue.",
        "trait": "emotional_stability",
        "reverse_keyed": False,
    },
    {
        "id": "s3",
        "prompt": "I get stressed out easily.",
        "trait": "emotional_stability",
        "reverse_keyed": True,
    },
    {
        "id": "s4",
        "prompt": "I worry about things.",
        "trait": "emotional_stability",
        "reverse_keyed": True,
    },
]


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    center_literal = (
        PILOT_CENTER_ID.hex if dialect == "sqlite" else str(PILOT_CENTER_ID)
    )
    self_paced_literal = (
        SELF_PACED_COMMUNITY_ID.hex
        if dialect == "sqlite"
        else str(SELF_PACED_COMMUNITY_ID)
    )
    personality_literal = (
        PERSONALITY_DEFINITION_ID.hex
        if dialect == "sqlite"
        else str(PERSONALITY_DEFINITION_ID)
    )
    op.add_column(
        "communities",
        sa.Column(
            "matching_mode",
            sa.String(30),
            nullable=False,
            server_default="counselor_based",
        ),
    )
    if dialect == "sqlite":
        with op.batch_alter_table("communities") as batch:
            batch.create_check_constraint(
                "ck_communities_matching_mode",
                "matching_mode IN ('counselor_based', 'self_paced')",
            )
    else:
        op.create_check_constraint(
            "ck_communities_matching_mode",
            "communities",
            "matching_mode IN ('counselor_based', 'self_paced')",
        )
    op.execute(
        sa.text(
            "UPDATE communities SET matching_mode = 'counselor_based' "
            "WHERE matching_mode IS NULL OR matching_mode <> 'counselor_based'"
        )
    )
    op.execute(
        "INSERT INTO communities (id, center_id, slug, name, matching_mode) "
        f"SELECT '{self_paced_literal}', '{center_literal}', "
        "'self-paced-pilot', 'Self-Paced Pilot', 'self_paced' "
        f"WHERE EXISTS (SELECT 1 FROM centers WHERE id = '{center_literal}') "
        "AND NOT EXISTS (SELECT 1 FROM communities "
        f"WHERE center_id = '{center_literal}' AND slug = 'self-paced-pilot')"
    )

    op.create_table(
        "member_community_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("center_id", sa.Uuid(), sa.ForeignKey("centers.id"), nullable=False),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "community_id", sa.Uuid(), sa.ForeignKey("communities.id"), nullable=False
        ),
        sa.Column(
            "assigned_by_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "reason_code IN ('pilot_placement', 'member_request', 'operations_correction')",
            name="ck_member_community_assignments_reason",
        ),
    )
    op.create_index(
        "ix_member_community_assignments_member",
        "member_community_assignments",
        ["member_id", "ended_at"],
    )
    op.create_index(
        "uq_member_community_assignments_current",
        "member_community_assignments",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    # Existing members are intentionally placed in the established counselor mode.
    op.execute(
        sa.text(
            "INSERT INTO member_community_assignments "
            "(id, center_id, member_id, community_id, assigned_by_id, reason_code) "
            "SELECT u.id, u.center_id, u.id, c.id, u.id, 'pilot_placement' "
            "FROM users u JOIN communities c ON c.center_id = u.center_id "
            "AND c.matching_mode = 'counselor_based' "
            "WHERE u.role = 'member' AND c.id = "
            "(SELECT c2.id FROM communities c2 WHERE c2.center_id = u.center_id "
            "AND c2.matching_mode = 'counselor_based' "
            "ORDER BY CASE WHEN c2.slug = 'intentional-relationships' "
            "THEN 0 ELSE 1 END, c2.slug LIMIT 1)"
        )
    )

    op.create_table(
        "personality_inventory_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("key", "version"),
    )
    op.create_table(
        "personality_inventory_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "definition_id",
            sa.Uuid(),
            sa.ForeignKey("personality_inventory_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_personality_inventory_assignments_member",
        "personality_inventory_assignments",
        ["member_id", "assigned_at"],
    )
    op.create_table(
        "personality_inventory_responses",
        sa.Column(
            "assignment_id",
            sa.Uuid(),
            sa.ForeignKey("personality_inventory_assignments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "personality_inventory_scores",
        sa.Column(
            "assignment_id",
            sa.Uuid(),
            sa.ForeignKey("personality_inventory_assignments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
    )
    item_json = json.dumps(ITEMS).replace("'", "''")
    op.execute(
        "INSERT INTO personality_inventory_definitions "
        "(id, key, version, title, description, items, provenance, is_active) "
        f"VALUES ('{personality_literal}', 'ipip-big-five-20', '1.0', "
        "'Optional Personality Inventory', "
        "'A non-diagnostic reflection aid. It does not determine readiness, "
        "eligibility, candidate scores, rank, or rejection.', "
        f"'{item_json}', "
        "'Items adapted from the International Personality Item Pool (IPIP), "
        "which is in the public domain: https://ipip.ori.org/.', true)"
    )

    op.create_table(
        "self_paced_suggestion_interests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("center_id", sa.Uuid(), sa.ForeignKey("centers.id"), nullable=False),
        sa.Column(
            "community_id", sa.Uuid(), sa.ForeignKey("communities.id"), nullable=False
        ),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "candidate_member_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("match_proposals.id")),
        sa.CheckConstraint(
            "member_id <> candidate_member_id",
            name="ck_self_paced_interests_distinct_members",
        ),
        sa.CheckConstraint(
            "status IN ('interested', 'dismissed', 'matched', 'withdrawn')",
            name="ck_self_paced_interests_status",
        ),
        sa.UniqueConstraint("member_id", "candidate_member_id"),
    )
    op.create_index(
        "ix_self_paced_interests_candidate_status",
        "self_paced_suggestion_interests",
        ["candidate_member_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_self_paced_interests_candidate_status",
        table_name="self_paced_suggestion_interests",
    )
    op.drop_table("self_paced_suggestion_interests")
    op.drop_table("personality_inventory_scores")
    op.drop_table("personality_inventory_responses")
    op.drop_index(
        "ix_personality_inventory_assignments_member",
        table_name="personality_inventory_assignments",
    )
    op.drop_table("personality_inventory_assignments")
    op.drop_table("personality_inventory_definitions")
    op.drop_index(
        "uq_member_community_assignments_current",
        table_name="member_community_assignments",
    )
    op.drop_index(
        "ix_member_community_assignments_member",
        table_name="member_community_assignments",
    )
    op.drop_table("member_community_assignments")
    community_literal = (
        SELF_PACED_COMMUNITY_ID.hex
        if op.get_context().dialect.name == "sqlite"
        else str(SELF_PACED_COMMUNITY_ID)
    )
    op.execute(f"DELETE FROM communities WHERE id = '{community_literal}'")
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("communities") as batch:
            batch.drop_constraint("ck_communities_matching_mode", type_="check")
            batch.drop_column("matching_mode")
    else:
        op.drop_constraint("ck_communities_matching_mode", "communities", type_="check")
        op.drop_column("communities", "matching_mode")
