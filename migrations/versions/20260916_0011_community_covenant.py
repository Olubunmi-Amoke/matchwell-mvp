"""Add the global versioned faith and community covenant.

Revision ID: 20260916_0011
Revises: 20260916_0010
Create Date: 2026-09-16
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0011"
down_revision: str | None = "20260916_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000501")
POLICY_KEY = "matchwell-faith-community-covenant"
AFFIRMATIONS = [
    {
        "key": "christian_identity",
        "label": "I identify as Christian.",
    },
    {
        "key": "christian_marriage_intent",
        "label": "I intend to pursue a committed Christian marriage.",
    },
    {
        "key": "dignity_and_respect",
        "label": "I will treat every person with dignity and respect, including when we disagree.",
    },
    {
        "key": "truthful_participation",
        "label": "I will participate truthfully.",
    },
    {
        "key": "non_harassment_and_non_discrimination",
        "label": "I will not harass or discriminate against anyone in my platform conduct.",
    },
    {
        "key": "safety_and_operations",
        "label": "I will follow safety, reporting, boundaries, and counselor or Member Operations decisions.",
    },
]
BODY = """## Shared purpose

Matchwell is a Christian community for people intending to pursue a committed Christian marriage.

## Community conduct

Participate truthfully. Treat every person with dignity and respect, including when you disagree. Harassment, discrimination, retaliation, or mistreatment is not permitted in platform conduct.

## Safety and accountability

Follow platform safety guidance, reporting processes, personal boundaries, and decisions made by counselors or Member Operations within their roles.

## Scope of affirmation

This affirmation concerns participation commitments. It is not a theological or mental-health diagnosis, and it does not authorize harassment, discrimination, or mistreatment of any person.
"""


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.create_table(
        "community_covenant_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("policy_key", sa.String(100), nullable=False),
        sa.Column("display_version", sa.String(50), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("required_affirmations", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "policy_key",
            "revision",
            name="uq_community_covenant_policy_revision",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_community_covenant_positive_revision",
        ),
    )
    op.create_index(
        "uq_community_covenant_one_active",
        "community_covenant_definitions",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION protect_community_covenant_definitions()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'community covenant definitions are append-only';
                END IF;
                IF OLD.id IS DISTINCT FROM NEW.id
                    OR OLD.policy_key IS DISTINCT FROM NEW.policy_key
                    OR OLD.display_version IS DISTINCT FROM NEW.display_version
                    OR OLD.revision IS DISTINCT FROM NEW.revision
                    OR OLD.effective_at IS DISTINCT FROM NEW.effective_at
                    OR OLD.title IS DISTINCT FROM NEW.title
                    OR OLD.body_markdown IS DISTINCT FROM NEW.body_markdown
                    OR OLD.required_affirmations IS DISTINCT FROM NEW.required_affirmations
                    OR NOT OLD.is_active
                    OR NEW.is_active
                THEN
                    RAISE EXCEPTION 'community covenant definitions are append-only';
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
        op.execute(
            """
            CREATE TRIGGER community_covenant_definitions_append_only
            BEFORE UPDATE OR DELETE ON community_covenant_definitions
            FOR EACH ROW EXECUTE FUNCTION protect_community_covenant_definitions();
            """
        )
    op.create_table(
        "community_covenant_acceptances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "covenant_definition_id",
            sa.Uuid(),
            sa.ForeignKey("community_covenant_definitions.id"),
            nullable=False,
        ),
        sa.Column("accepted_affirmation_keys", sa.JSON(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id",
            "covenant_definition_id",
            name="uq_community_covenant_acceptance_user_version",
        ),
    )
    op.create_index(
        "ix_community_covenant_acceptances_user",
        "community_covenant_acceptances",
        ["user_id"],
    )
    covenant_id = (
        COVENANT_ID.hex
        if op.get_context().dialect.name == "sqlite"
        else str(COVENANT_ID)
    )
    op.execute(
        "INSERT INTO community_covenant_definitions "
        "(id, policy_key, display_version, revision, effective_at, title, "
        "body_markdown, required_affirmations, is_active) VALUES ("
        f"{_quoted(covenant_id)}, {_quoted(POLICY_KEY)}, {_quoted('Pilot 1.0')}, 1, "
        f"{_quoted(datetime(2026, 9, 16, tzinfo=UTC).isoformat())}, "
        f"{_quoted('Matchwell Faith & Community Covenant')}, {_quoted(BODY)}, "
        f"{_quoted(json.dumps(AFFIRMATIONS))}, true)"
    )
    _revoke_existing_eligibility()


def _revoke_existing_eligibility() -> None:
    dialect = op.get_context().dialect.name
    latest_eligible = (
        "FROM readiness_decisions rd "
        "JOIN (SELECT member_id, MAX(evaluated_at) AS max_at "
        "FROM readiness_decisions GROUP BY member_id) latest "
        "ON latest.member_id = rd.member_id "
        "AND latest.max_at = rd.evaluated_at "
        "JOIN users u ON u.id = rd.member_id "
        "WHERE rd.eligible = true"
    )
    if dialect == "sqlite":

        def new_id_for(_alias: str) -> str:
            return "lower(hex(randomblob(16)))"

        covenant_id = COVENANT_ID.hex
        evidence_versions = (
            "json_set(COALESCE(rd.evidence_versions, '{}'), "
            "'$.community_covenant_definition_id', "
            f"'{covenant_id}', '$.community_covenant_revision', '1', "
            "'$.community_covenant_acceptance_id', 'none')"
        )
        metadata = (
            "json_object('eligible', 0, 'unmet_requirements', "
            "json_array('community_covenant'), 'configuration_version', "
            "'pilot-v2-community-covenant')"
        )
        payload = (
            "json_object('member_id', rd.member_id, "
            "'community_id', rd.community_id, 'eligible', 0, "
            "'configuration_version', 'pilot-v2-community-covenant')"
        )
        matching_metadata = "json_object('reason', 'readiness_lost')"
        matching_payload = (
            "json_object('proposal_id', p.id, 'reason', 'readiness_lost')"
        )
    else:

        def new_id_for(alias: str) -> str:
            return f"md5({alias}.id::text || random()::text)::uuid"

        covenant_id = str(COVENANT_ID)
        evidence_versions = (
            "COALESCE(rd.evidence_versions, '{}'::jsonb) || "
            "jsonb_build_object("
            "'community_covenant_definition_id', "
            f"'{covenant_id}', 'community_covenant_revision', '1', "
            "'community_covenant_acceptance_id', 'none')"
        )
        metadata = (
            "jsonb_build_object('eligible', false, 'unmet_requirements', "
            "jsonb_build_array('community_covenant'), "
            "'configuration_version', 'pilot-v2-community-covenant')"
        )
        payload = (
            "jsonb_build_object('member_id', rd.member_id::text, "
            "'community_id', rd.community_id::text, 'eligible', false, "
            "'configuration_version', 'pilot-v2-community-covenant')"
        )
        matching_metadata = "jsonb_build_object('reason', 'readiness_lost')"
        matching_payload = (
            "jsonb_build_object('proposal_id', p.id::text, 'reason', 'readiness_lost')"
        )

    affected_members = "SELECT rd.member_id " + latest_eligible
    affected_proposals = (
        "SELECT p.id FROM match_proposals p WHERE "
        "p.status IN ('pending_review', 'introduced', 'active') AND "
        f"(p.member_a_id IN ({affected_members}) "
        f"OR p.member_b_id IN ({affected_members}))"
    )
    op.execute(
        "INSERT INTO audit_events "
        "(id, actor_id, action, subject_id, center_id, correlation_id, "
        "safe_metadata, occurred_at) "
        f"SELECT {new_id_for('p')}, "
        "'00000000-0000-0000-0000-000000000000', "
        "'matching.closed', "
        + ("p.id" if dialect == "sqlite" else "p.id::text")
        + f", p.center_id, {new_id_for('p')}, "
        f"{matching_metadata}, CURRENT_TIMESTAMP "
        "FROM match_proposals p WHERE "
        "p.status IN ('pending_review', 'introduced', 'active') AND "
        f"(p.member_a_id IN ({affected_members}) "
        f"OR p.member_b_id IN ({affected_members}))"
    )
    op.execute(
        "INSERT INTO outbox_messages "
        "(id, event_type, payload, occurred_at, attempts) "
        f"SELECT {new_id_for('p')}, 'matching.closed', {matching_payload}, "
        "CURRENT_TIMESTAMP, 0 FROM match_proposals p WHERE "
        "p.status IN ('pending_review', 'introduced', 'active') AND "
        f"(p.member_a_id IN ({affected_members}) "
        f"OR p.member_b_id IN ({affected_members}))"
    )
    op.execute(
        "DELETE FROM match_proposal_participant_claims "
        f"WHERE proposal_id IN ({affected_proposals})"
    )
    op.execute(
        "UPDATE match_proposals SET status = 'closed', "
        "closed_at = CURRENT_TIMESTAMP, closed_reason = 'readiness_lost' "
        f"WHERE id IN ({affected_proposals})"
    )
    op.execute(
        "UPDATE self_paced_suggestion_interests "
        "SET status = 'withdrawn', updated_at = CURRENT_TIMESTAMP "
        "WHERE status IN ('interested', 'dismissed') AND "
        f"(member_id IN ({affected_members}) "
        f"OR candidate_member_id IN ({affected_members}))"
    )

    op.execute(
        "INSERT INTO audit_events "
        "(id, actor_id, action, subject_id, center_id, correlation_id, "
        "safe_metadata, occurred_at) "
        f"SELECT {new_id_for('rd')}, "
        "'00000000-0000-0000-0000-000000000000', "
        "'readiness.evaluated', "
        + ("rd.member_id" if dialect == "sqlite" else "rd.member_id::text")
        + f", u.center_id, {new_id_for('rd')}, {metadata}, CURRENT_TIMESTAMP "
        + latest_eligible
    )
    op.execute(
        "INSERT INTO outbox_messages "
        "(id, event_type, payload, occurred_at, attempts) "
        f"SELECT {new_id_for('rd')}, "
        f"'readiness.eligibility_changed', {payload}, "
        f"CURRENT_TIMESTAMP, 0 {latest_eligible}"
    )
    op.execute(
        "INSERT INTO readiness_decisions "
        "(id, member_id, community_id, eligible, unmet_requirements, "
        "evidence_versions, configuration_version, evaluated_at) "
        f"SELECT {new_id_for('rd')}, rd.member_id, rd.community_id, false, "
        + (
            "json_array('community_covenant')"
            if dialect == "sqlite"
            else "jsonb_build_array('community_covenant')"
        )
        + f", {evidence_versions}, 'pilot-v2-community-covenant', "
        f"CURRENT_TIMESTAMP {latest_eligible}"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM outbox_messages "
        "WHERE event_type = 'readiness.eligibility_changed' "
        "AND "
        + (
            "json_extract(payload, '$.configuration_version') "
            "= 'pilot-v2-community-covenant'"
            if op.get_context().dialect.name == "sqlite"
            else "payload ->> 'configuration_version' = 'pilot-v2-community-covenant'"
        )
    )
    op.execute(
        "DELETE FROM readiness_decisions "
        "WHERE configuration_version = 'pilot-v2-community-covenant'"
    )
    op.drop_index(
        "ix_community_covenant_acceptances_user",
        table_name="community_covenant_acceptances",
    )
    op.drop_table("community_covenant_acceptances")
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS community_covenant_definitions_append_only "
            "ON community_covenant_definitions"
        )
        op.execute("DROP FUNCTION IF EXISTS protect_community_covenant_definitions()")
    op.drop_index(
        "uq_community_covenant_one_active",
        table_name="community_covenant_definitions",
    )
    op.drop_table("community_covenant_definitions")
