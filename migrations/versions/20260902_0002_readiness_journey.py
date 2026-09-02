"""Create the pilot readiness journey.

Revision ID: 20260902_0002
Revises: 20260902_0001
Create Date: 2026-09-02
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0002"
down_revision: str | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CENTER_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")
COMMUNITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000201")
CONSENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000301")
ASSESSMENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000401")


def upgrade() -> None:
    op.create_table(
        "centers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "communities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("center_id", "slug"),
    )
    op.create_index("ix_communities_center_id", "communities", ["center_id"])
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=500), nullable=False),
        sa.Column("oidc_subject", sa.String(length=500), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("oidc_issuer", "oidc_subject"),
    )
    op.create_index("ix_users_center_id", "users", ["center_id"])
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("invited_by_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invitations_email_active",
        "invitations",
        ["email", "accepted_at", "expires_at"],
    )
    op.create_table(
        "consent_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key", "version"),
    )
    op.create_table(
        "consent_acceptances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("consent_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["consent_version_id"], ["consent_versions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "consent_version_id"),
    )
    op.create_index(
        "ix_consent_acceptances_user_id",
        "consent_acceptances",
        ["user_id"],
    )
    op.create_table(
        "member_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("faith_affirmed", sa.Boolean(), nullable=False),
        sa.Column("relationship_intent", sa.String(length=300), nullable=False),
        sa.Column("denomination", sa.String(length=100), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "assessment_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version"),
    )
    op.create_table(
        "assessment_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["assessment_definitions.id"],
        ),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_assignments_member_id",
        "assessment_assignments",
        ["member_id"],
    )
    op.create_table(
        "counselor_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("counselor_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["counselor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_counselor_assignments_active",
        "counselor_assignments",
        ["member_id", "ended_at"],
    )
    op.create_index(
        "uq_counselor_assignments_one_active",
        "counselor_assignments",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "counselor_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["counselor_assignments.id"],
        ),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_counselor_decisions_assignment_id",
        "counselor_decisions",
        ["assignment_id"],
    )
    op.create_table(
        "screening_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_reference", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id"),
    )
    op.create_table(
        "screening_event_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id"),
    )
    op.create_table(
        "holds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("center_context_id", sa.Uuid(), nullable=True),
        sa.Column("hold_type", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("applied_by_id", sa.Uuid(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_by_id", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["applied_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["center_context_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["released_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_holds_member_active",
        "holds",
        ["member_id", "released_at"],
    )
    op.create_table(
        "readiness_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column(
            "unmet_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "evidence_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "configuration_version",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_readiness_decisions_member_evaluated",
        "readiness_decisions",
        ["member_id", "evaluated_at"],
    )

    effective_at = datetime(2026, 9, 2, tzinfo=UTC)
    op.bulk_insert(
        sa.table(
            "centers",
            sa.column("id", sa.Uuid()),
            sa.column("slug", sa.String()),
            sa.column("name", sa.String()),
        ),
        [{"id": CENTER_ID, "slug": "matchwell-pilot", "name": "Matchwell Center"}],
    )
    op.bulk_insert(
        sa.table(
            "communities",
            sa.column("id", sa.Uuid()),
            sa.column("center_id", sa.Uuid()),
            sa.column("slug", sa.String()),
            sa.column("name", sa.String()),
        ),
        [
            {
                "id": COMMUNITY_ID,
                "center_id": CENTER_ID,
                "slug": "intentional-relationships",
                "name": "Intentional Relationships Community",
            }
        ],
    )
    op.bulk_insert(
        sa.table(
            "consent_versions",
            sa.column("id", sa.Uuid()),
            sa.column("policy_key", sa.String()),
            sa.column("version", sa.String()),
            sa.column("title", sa.String()),
            sa.column("body_markdown", sa.Text()),
            sa.column("effective_at", sa.DateTime(timezone=True)),
            sa.column("is_active", sa.Boolean()),
        ),
        [
            {
                "id": CONSENT_ID,
                "policy_key": "pilot-participation",
                "version": "1.0",
                "title": "Matchwell Pilot Participation Consent",
                "body_markdown": (
                    "By participating, you confirm that you are at least 18 years "
                    "old, identify as Christian, will provide truthful information, "
                    "and agree to follow Matchwell's safety and community standards. "
                    "Matchwell may pause eligibility while safety or administrative "
                    "concerns are reviewed."
                ),
                "effective_at": effective_at,
                "is_active": True,
            }
        ],
    )
    questions = json.dumps(
        [
            {
                "id": "intentionality",
                "prompt": "I am clear about why I want a committed relationship.",
            },
            {
                "id": "communication",
                "prompt": "I can communicate needs and boundaries respectfully.",
            },
            {
                "id": "conflict",
                "prompt": "I can approach disagreement without contempt or avoidance.",
            },
            {
                "id": "faith",
                "prompt": "My Christian faith meaningfully guides my relationships.",
            },
            {
                "id": "support",
                "prompt": "I have trusted support for healthy accountability.",
            },
        ]
    ).replace("'", "''")
    op.execute(
        f"""
        INSERT INTO assessment_definitions
            (id, key, version, title, description, questions, is_active)
        VALUES
            (
                '{ASSESSMENT_ID}',
                'relationship-readiness',
                '1.0',
                'Relationship Readiness Reflection',
                'Rate each statement from 1 (not yet true) to 5 (consistently true). '
                'Answers support counselor intake and are not used for automated '
                'relationship decisions.',
                '{questions}'::jsonb,
                true
            );
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_readiness_decisions_member_evaluated",
        table_name="readiness_decisions",
    )
    op.drop_table("readiness_decisions")
    op.drop_index("ix_holds_member_active", table_name="holds")
    op.drop_table("holds")
    op.drop_table("screening_event_receipts")
    op.drop_table("screening_cases")
    op.drop_index(
        "ix_counselor_decisions_assignment_id",
        table_name="counselor_decisions",
    )
    op.drop_table("counselor_decisions")
    op.drop_index(
        "ix_counselor_assignments_active",
        table_name="counselor_assignments",
    )
    op.drop_index(
        "uq_counselor_assignments_one_active",
        table_name="counselor_assignments",
    )
    op.drop_table("counselor_assignments")
    op.drop_index(
        "ix_assessment_assignments_member_id",
        table_name="assessment_assignments",
    )
    op.drop_table("assessment_assignments")
    op.drop_table("assessment_definitions")
    op.drop_table("member_profiles")
    op.drop_index(
        "ix_consent_acceptances_user_id",
        table_name="consent_acceptances",
    )
    op.drop_table("consent_acceptances")
    op.drop_table("consent_versions")
    op.drop_index("ix_invitations_email_active", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_users_center_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_communities_center_id", table_name="communities")
    op.drop_table("communities")
    op.drop_table("centers")
