"""Create guided matched-pair journeys.

Revision ID: 20260904_0005
Revises: 20260904_0004
Create Date: 2026-09-04
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0005"
down_revision: str | None = "20260904_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE_ID = uuid.UUID("c270f45f-261b-4606-a6b8-a584e3826de5")
_TASKS = (
    (
        uuid.UUID("a4c9316a-166c-426b-8dfa-495df6e9a7a1"),
        1,
        "Agree on communication expectations",
        "Discuss preferred communication rhythms and how you will handle pauses.",
        "shared",
        7,
    ),
    (
        uuid.UUID("55a362cb-6ed4-46f0-8a4d-34806eab7e87"),
        2,
        "Discuss faith and values",
        "Talk through the faith practices and values that shape daily life.",
        "shared",
        14,
    ),
    (
        uuid.UUID("122f083b-eb94-4587-a5cf-7a89e5d38bdd"),
        3,
        "Reflect on healthy boundaries",
        "Privately review the boundaries that help you participate with clarity.",
        "individual",
        21,
    ),
    (
        uuid.UUID("d6f976fd-c9d3-4690-98f4-b1cdaffdc6e5"),
        4,
        "Practice a conflict-repair conversation",
        "Use a low-stakes disagreement to practice listening and repair.",
        "shared",
        30,
    ),
    (
        uuid.UUID("fd7d6839-7d62-41fc-96a4-785106adcefb"),
        5,
        "Discuss family and community support",
        "Discuss the healthy people and communities supporting your relationship.",
        "shared",
        45,
    ),
    (
        uuid.UUID("59832d04-e58c-452e-a13a-cdb81a9a21a0"),
        6,
        "Reflect on relationship goals",
        "Privately consider alignment, questions, and next steps.",
        "individual",
        60,
    ),
)


def upgrade() -> None:
    op.create_table(
        "journey_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version"),
    )
    op.create_table(
        "journey_template_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("due_day", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["journey_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "sequence"),
    )
    op.create_table(
        "pair_journeys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["match_proposals.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["journey_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id"),
    )
    op.create_table(
        "journey_task_completions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("journey_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["journey_id"], ["pair_journeys.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["journey_template_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journey_id", "task_id", "member_id"),
    )
    op.create_index(
        "ix_journey_task_completions_member",
        "journey_task_completions",
        ["journey_id", "member_id"],
    )
    op.create_table(
        "journey_check_ins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("journey_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("milestone", sa.String(length=20), nullable=False),
        sa.Column("relationship_status", sa.String(length=30), nullable=False),
        sa.Column("support_requested", sa.Boolean(), nullable=False),
        sa.Column("concern_flag", sa.Boolean(), nullable=False),
        sa.Column("private_reflection", sa.String(length=2000), nullable=False),
        sa.Column("share_with_counselor", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["journey_id"], ["pair_journeys.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journey_id", "member_id", "milestone"),
    )
    op.create_index(
        "ix_journey_check_ins_member",
        "journey_check_ins",
        ["journey_id", "member_id"],
    )

    templates = sa.table(
        "journey_templates",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("version", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("is_active", sa.Boolean()),
    )
    tasks = sa.table(
        "journey_template_tasks",
        sa.column("id", sa.Uuid()),
        sa.column("template_id", sa.Uuid()),
        sa.column("sequence", sa.Integer()),
        sa.column("title", sa.String()),
        sa.column("description", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("due_day", sa.Integer()),
    )
    op.bulk_insert(
        templates,
        [
            {
                "id": _TEMPLATE_ID,
                "key": "pilot-foundations",
                "version": "1.0",
                "name": "Pilot Foundations",
                "description": (
                    "A counselor-guided foundation for intentional relationships."
                ),
                "is_active": True,
            }
        ],
    )
    op.bulk_insert(
        tasks,
        [
            {
                "id": task_id,
                "template_id": _TEMPLATE_ID,
                "sequence": sequence,
                "title": title,
                "description": description,
                "scope": scope,
                "due_day": due_day,
            }
            for task_id, sequence, title, description, scope, due_day in _TASKS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_journey_check_ins_member", table_name="journey_check_ins")
    op.drop_table("journey_check_ins")
    op.drop_index(
        "ix_journey_task_completions_member",
        table_name="journey_task_completions",
    )
    op.drop_table("journey_task_completions")
    op.drop_table("pair_journeys")
    op.drop_table("journey_template_tasks")
    op.drop_table("journey_templates")
