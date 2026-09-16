"""Owner feedback: denomination, consent acknowledgements, and intro session.

Revision ID: 20260916_0008
Revises: 20260909_0007
Create Date: 2026-09-16
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0008"
down_revision: str | None = "20260909_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DENOMINATION_CODES = (
    "baptist",
    "catholic",
    "anglican_episcopal",
    "methodist_wesleyan",
    "presbyterian_reformed",
    "pentecostal_charismatic",
    "orthodox",
    "lutheran",
    "seventh_day_adventist",
    "non_denominational",
    "other",
    "prefer_not_to_say",
)
_STATUSES = ("available", "scheduled", "completed", "cancelled")
_REASONS = ("member_requested", "counselor_unavailable", "operations_reschedule")
_CONSENT_ID = uuid.UUID("8b3a1412-f17b-4e28-8f09-202609160008")
_ACKS = [
    {
        "key": "eligibility_faith",
        "label": "I confirm I am at least 18 and identify as Christian.",
    },
    {
        "key": "voluntary_participation",
        "label": "I understand participation is voluntary and I may withdraw.",
    },
    {
        "key": "counselor_limits",
        "label": "I understand counselors are match facilitators, not emergency, legal, or medical providers.",
    },
    {
        "key": "privacy_data",
        "label": "I understand the described screening, privacy, and data-minimization practices.",
    },
    {
        "key": "matching_risk",
        "label": "I understand matching is not guaranteed and participation has interpersonal risks.",
    },
    {
        "key": "safety_reporting",
        "label": "I understand safety reporting, holds, and escalation may limit participation.",
    },
    {
        "key": "billing_cancellation",
        "label": "I understand the described billing, cancellation, and introductory-session terms.",
    },
]
_BODY = """# DRAFT — Pilot Participation Consent

**Pending legal review. This draft has not been represented as legally reviewed or approved.**

## Eligibility and faith
You confirm that you are at least 18 years old, identify as Christian, and want faith to guide this relationship journey.

## Voluntary participation
Participation is voluntary. You may decline an introduction, pause, or withdraw without being required to explain private circumstances.

## Counselor role and limits
Counselors support readiness, introductions, and structured check-ins. They do not provide emergency response, legal advice, medical care, psychotherapy, or a guarantee of safety or relationship outcomes.

## Screening and data minimization
The pilot may use identity or eligibility screening. Matchwell stores only normalized status and constrained operational reason codes, not screening reports. Provide only information requested by the service.

## Matching and no guarantee
Matching uses limited profile and preference data plus counselor review. Matchwell does not guarantee a match, compatibility, a relationship, or any outcome.

## Communications and check-ins
You may receive service communications, introduction notices, and guided check-in reminders. Use only Matchwell-supported channels for pilot activity when instructed.

## Safety, reporting, and holds
You may block or report another member. Matchwell may apply a safety or administrative hold, restrict contact, investigate within the pilot's limits, disable accounts, or escalate imminent concerns to appropriate emergency or public-safety resources.

## Privacy and data handling
Matchwell minimizes collection, limits access by Center and role, keeps audit records, and uses service providers needed to operate the pilot. No internet service can promise absolute security.

## Billing and cancellation
Displayed charges and cancellation terms apply to paid services. The one-time introductory counselor session is complimentary; cancellation permits rescheduling but does not create another benefit.

## Withdrawal and account disabling
You may request withdrawal or account disabling. Legal, safety, fraud-prevention, billing, and audit records may be retained where reasonably necessary.

## Risk acknowledgement
Meeting and communicating with others involves emotional, privacy, interpersonal, and physical-safety risks. Exercise judgment, protect personal information, and contact emergency services for immediate danger.

## Contact and escalation
Contact Member Operations for account, scheduling, billing, or withdrawal requests; Counselor Operations for counselor-service coordination; and Trust & Safety for reports or urgent safety escalation. Use local emergency services for immediate danger.
"""


def upgrade() -> None:
    op.add_column(
        "consent_versions",
        sa.Column("required_acknowledgements", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE consent_versions SET required_acknowledgements = '[]'")
    with op.batch_alter_table("consent_versions") as batch:
        batch.alter_column("required_acknowledgements", nullable=False)
    op.add_column(
        "consent_acceptances",
        sa.Column("accepted_acknowledgement_keys", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE consent_acceptances SET accepted_acknowledgement_keys = '[]'")
    with op.batch_alter_table("consent_acceptances") as batch:
        batch.alter_column("accepted_acknowledgement_keys", nullable=False)

    with op.batch_alter_table("member_profiles") as batch:
        batch.alter_column("denomination", new_column_name="denomination_legacy")
        batch.add_column(
            sa.Column(
                "denomination_code",
                sa.String(length=40),
                nullable=False,
                server_default="prefer_not_to_say",
            )
        )
        batch.add_column(sa.Column("denomination_other", sa.String(length=100)))
    normalized = "lower(replace(replace(replace(trim(denomination_legacy), '-', ' '), '/', ' '), '  ', ' '))"
    cases = {
        "baptist": ("baptist",),
        "catholic": ("catholic", "roman catholic"),
        "anglican_episcopal": ("anglican", "episcopal", "anglican episcopal"),
        "methodist_wesleyan": ("methodist", "wesleyan", "methodist wesleyan"),
        "presbyterian_reformed": ("presbyterian", "reformed", "presbyterian reformed"),
        "pentecostal_charismatic": (
            "pentecostal",
            "charismatic",
            "pentecostal charismatic",
        ),
        "orthodox": ("orthodox",),
        "lutheran": ("lutheran",),
        "seventh_day_adventist": ("seventh day adventist", "sda"),
        "non_denominational": ("non denominational", "nondenominational"),
    }
    expression = "CASE"
    for code, values in cases.items():
        quoted = ", ".join(repr(value) for value in values)
        expression += f" WHEN {normalized} IN ({quoted}) THEN '{code}'"
    expression += (
        " WHEN trim(denomination_legacy) = '' THEN 'prefer_not_to_say' ELSE 'other' END"
    )
    op.execute(f"UPDATE member_profiles SET denomination_code = {expression}")
    op.execute(
        "UPDATE member_profiles SET denomination_other = trim(denomination_legacy) "
        "WHERE denomination_code = 'other'"
    )
    with op.batch_alter_table("member_profiles") as batch:
        batch.drop_column("denomination_legacy")
        batch.create_check_constraint(
            "ck_member_profiles_denomination_code_allowlist",
            f"denomination_code IN ({', '.join(map(repr, _DENOMINATION_CODES))})",
        )
        batch.create_check_constraint(
            "ck_member_profiles_denomination_other",
            "(denomination_code = 'other' AND denomination_other IS NOT NULL "
            "AND trim(denomination_other) <> '') OR "
            "(denomination_code <> 'other' AND denomination_other IS NULL)",
        )
        batch.alter_column("denomination_code", server_default=None)

    op.create_table(
        "introductory_session_benefits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("center_id", sa.Uuid(), sa.ForeignKey("centers.id"), nullable=False),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("counselor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("reason_code", sa.String(40)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("member_id"),
        sa.CheckConstraint(
            f"status IN ({', '.join(map(repr, _STATUSES))})",
            name="ck_introductory_session_benefits_status",
        ),
        sa.CheckConstraint(
            f"reason_code IS NULL OR reason_code IN ({', '.join(map(repr, _REASONS))})",
            name="ck_introductory_session_benefits_reason",
        ),
    )

    op.execute("UPDATE consent_versions SET is_active = false")

    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    op.execute(
        "INSERT INTO consent_versions "
        "(id, policy_key, version, title, body_markdown, "
        "required_acknowledgements, effective_at, is_active) VALUES ("
        f"{quoted(str(_CONSENT_ID))}, {quoted('pilot-participation')}, "
        f"{quoted('2026-09-draft-2')}, "
        f"{quoted('DRAFT Pilot Participation Consent — Pending Legal Review')}, "
        f"{quoted(_BODY)}, {quoted(json.dumps(_ACKS))}, "
        f"{quoted(datetime(2026, 9, 16, tzinfo=UTC).isoformat())}, true)"
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM consent_acceptances WHERE consent_version_id = '{_CONSENT_ID}'"
    )
    op.execute(f"DELETE FROM consent_versions WHERE id = '{_CONSENT_ID}'")
    op.execute(
        "UPDATE consent_versions SET is_active = true "
        "WHERE id = (SELECT id FROM consent_versions ORDER BY effective_at DESC LIMIT 1)"
    )
    op.drop_table("introductory_session_benefits")
    with op.batch_alter_table("member_profiles") as batch:
        batch.drop_constraint("ck_member_profiles_denomination_other", type_="check")
        batch.drop_constraint(
            "ck_member_profiles_denomination_code_allowlist", type_="check"
        )
        batch.add_column(sa.Column("denomination", sa.String(100), nullable=True))
    op.execute(
        "UPDATE member_profiles SET denomination = "
        "CASE WHEN denomination_code = 'other' THEN denomination_other "
        "WHEN denomination_code = 'prefer_not_to_say' THEN '' "
        "ELSE denomination_code END"
    )
    with op.batch_alter_table("member_profiles") as batch:
        batch.alter_column("denomination", nullable=False)
        batch.drop_column("denomination_other")
        batch.drop_column("denomination_code")
    op.drop_column("consent_acceptances", "accepted_acknowledgement_keys")
    op.drop_column("consent_versions", "required_acknowledgements")
