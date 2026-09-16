import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from matchwell.infrastructure.persistence.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

# Kept as plain string tuples (rather than importing domain enums) so the ORM
# layer stays decoupled from the domain layer; values are mirrored exactly
# from ``matchwell.domain.access`` and ``matchwell.domain.pilot`` and from
# migration ``20260909_0007``.
_ACCOUNT_STATUSES = ("active", "disabled")
_ACCOUNT_DISABLE_REASON_CODES = (
    "safety_concern",
    "policy_violation",
    "member_requested",
    "duplicate_account",
    "inactive_account",
    "other_operational",
)
_ACCOUNT_REACTIVATE_REASON_CODES = (
    "safety_concern_resolved",
    "member_requested",
    "admin_allowlist_restored",
    "entered_in_error",
    "other_operational",
)
_SCREENING_REASON_CODES = (
    "identity_verification_failed",
    "provider_ineligible_result",
    "provider_error",
    "provider_timeout",
    "document_unreadable",
    "duplicate_submission",
    "manual_review_required",
    "expired",
    "other_operational",
)
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
_INTRODUCTORY_SESSION_STATUSES = ("available", "scheduled", "completed", "cancelled")
_INTRODUCTORY_SESSION_REASON_CODES = (
    "member_requested",
    "counselor_unavailable",
    "operations_reschedule",
)
_REMATCH_REASON_CODES = (
    "member_decline_reconsidered",
    "counselor_decline_reconsidered",
    "entitlement_restored",
    "circumstances_changed",
    "operations_correction",
)
_REMATCH_AUTHORIZATION_STATUSES = ("pending", "approved", "consumed", "revoked")
_MATCHING_MODES = ("counselor_based", "self_paced")
_COMMUNITY_ASSIGNMENT_REASON_CODES = (
    "pilot_placement",
    "member_request",
    "operations_correction",
)
_SUGGESTION_INTEREST_STATUSES = (
    "interested",
    "dismissed",
    "matched",
    "withdrawn",
)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    center_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=dict,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class OutboxMessageRecord(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CenterRecord(Base):
    __tablename__ = "centers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class CommunityRecord(Base):
    __tablename__ = "communities"
    __table_args__ = (
        UniqueConstraint("center_id", "slug"),
        CheckConstraint(
            f"matching_mode IN ({', '.join(map(repr, _MATCHING_MODES))})",
            name="ck_communities_matching_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    matching_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default="counselor_based"
    )


class MemberCommunityAssignmentRecord(Base):
    __tablename__ = "member_community_assignments"
    __table_args__ = (
        CheckConstraint(
            "reason_code IN "
            f"({', '.join(map(repr, _COMMUNITY_ASSIGNMENT_REASON_CODES))})",
            name="ck_member_community_assignments_reason",
        ),
        Index("ix_member_community_assignments_member", "member_id", "ended_at"),
        Index(
            "uq_member_community_assignments_current",
            "member_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communities.id"), nullable=False
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject"),
        UniqueConstraint("email"),
        CheckConstraint(
            f"status IN ({', '.join(map(repr, _ACCOUNT_STATUSES))})",
            name="ck_users_status_allowlist",
        ),
        CheckConstraint(
            "disabled_reason_code IS NULL OR disabled_reason_code IN "
            f"({', '.join(map(repr, _ACCOUNT_DISABLE_REASON_CODES))})",
            name="ck_users_disabled_reason_allowlist",
        ),
        CheckConstraint(
            "reactivated_reason_code IS NULL OR reactivated_reason_code IN "
            f"({', '.join(map(repr, _ACCOUNT_REACTIVATE_REASON_CODES))})",
            name="ck_users_reactivated_reason_allowlist",
        ),
        Index("ix_users_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
        index=True,
    )
    oidc_issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(String(500), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    # Explicit enabled/disabled account state, checked on every sign-in
    # before an actor is ever returned. Never defaults to disabled so an
    # ordinary new account can always sign in.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    disabled_reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reactivated_reason_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    reactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reactivated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class InvitationRecord(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index("ix_invitations_email_active", "email", "accepted_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    invited_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ConsentVersionRecord(Base):
    __tablename__ = "consent_versions"
    __table_args__ = (UniqueConstraint("policy_key", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    required_acknowledgements: Mapped[list[dict[str, str]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConsentAcceptanceRecord(Base):
    __tablename__ = "consent_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "consent_version_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    consent_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consent_versions.id"),
        nullable=False,
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    accepted_acknowledgement_keys: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list
    )


class CommunityCovenantDefinitionRecord(Base):
    __tablename__ = "community_covenant_definitions"
    __table_args__ = (
        UniqueConstraint(
            "policy_key",
            "revision",
            name="uq_community_covenant_policy_revision",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_community_covenant_positive_revision",
        ),
        Index(
            "uq_community_covenant_one_active",
            "policy_key",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(100), nullable=False)
    display_version: Mapped[str] = mapped_column(String(50), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    required_affirmations: Mapped[list[dict[str, str]]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CommunityCovenantAcceptanceRecord(Base):
    __tablename__ = "community_covenant_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "covenant_definition_id",
            name="uq_community_covenant_acceptance_user_version",
        ),
        Index("ix_community_covenant_acceptances_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    covenant_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("community_covenant_definitions.id"), nullable=False
    )
    accepted_affirmation_keys: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemberProfileRecord(Base):
    __tablename__ = "member_profiles"
    __table_args__ = (
        CheckConstraint(
            f"denomination_code IN ({', '.join(map(repr, _DENOMINATION_CODES))})",
            name="ck_member_profiles_denomination_code_allowlist",
        ),
        CheckConstraint(
            "(denomination_code = 'other' AND denomination_other IS NOT NULL "
            "AND trim(denomination_other) <> '') OR "
            "(denomination_code <> 'other' AND denomination_other IS NULL)",
            name="ck_member_profiles_denomination_other",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    faith_affirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    relationship_intent: Mapped[str] = mapped_column(String(300), nullable=False)
    denomination_code: Mapped[str] = mapped_column(
        String(40), nullable=False, default="prefer_not_to_say"
    )
    denomination_other: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class IntroductorySessionBenefitRecord(Base):
    __tablename__ = "introductory_session_benefits"
    __table_args__ = (
        UniqueConstraint("member_id"),
        CheckConstraint(
            f"status IN ({', '.join(map(repr, _INTRODUCTORY_SESSION_STATUSES))})",
            name="ck_introductory_session_benefits_status",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            f"({', '.join(map(repr, _INTRODUCTORY_SESSION_REASON_CODES))})",
            name="ck_introductory_session_benefits_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    counselor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssessmentDefinitionRecord(Base):
    __tablename__ = "assessment_definitions"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    questions: Mapped[list[dict[str, str]]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AssessmentAssignmentRecord(Base):
    __tablename__ = "assessment_assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_definitions.id"),
        nullable=False,
    )
    answers: Mapped[dict[str, int] | None] = mapped_column(
        JSON_DOCUMENT,
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PersonalityInventoryDefinitionRecord(Base):
    __tablename__ = "personality_inventory_definitions"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PersonalityInventoryAssignmentRecord(Base):
    __tablename__ = "personality_inventory_assignments"
    __table_args__ = (
        Index(
            "ix_personality_inventory_assignments_member",
            "member_id",
            "assigned_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("personality_inventory_definitions.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalityInventoryResponseRecord(Base):
    """Sensitive raw inventory responses; never join into broad operational views."""

    __tablename__ = "personality_inventory_responses"

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("personality_inventory_assignments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    answers: Mapped[dict[str, int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PersonalityInventoryScoreRecord(Base):
    """Sensitive derived scores, exposed only through neutral pair explanations."""

    __tablename__ = "personality_inventory_scores"

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("personality_inventory_assignments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scores: Mapped[dict[str, float]] = mapped_column(JSON_DOCUMENT, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CounselorAssignmentRecord(Base):
    __tablename__ = "counselor_assignments"
    __table_args__ = (
        Index("ix_counselor_assignments_active", "member_id", "ended_at"),
        Index(
            "uq_counselor_assignments_one_active",
            "member_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    counselor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CounselorDecisionRecord(Base):
    __tablename__ = "counselor_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("counselor_assignments.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ScreeningCaseRecord(Base):
    __tablename__ = "screening_cases"
    __table_args__ = (
        UniqueConstraint("member_id"),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            f"({', '.join(map(repr, _SCREENING_REASON_CODES))})",
            name="ck_screening_cases_reason_allowlist",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ScreeningEventReceiptRecord(Base):
    """Idempotency and diagnostics ledger for screening provider callbacks.

    Mirrors ``BillingWebhookReceiptRecord``'s shape so provider failures are
    triaged the same safe way. Never stores a screening report, provider
    payload, or free-text detail -- only identifiers and a constrained
    reason code.
    """

    __tablename__ = "screening_event_receipts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id"),
        CheckConstraint(
            "unresolved_reason IS NULL OR unresolved_reason IN "
            f"({', '.join(map(repr, _SCREENING_REASON_CODES))})",
            name="ck_screening_event_receipts_reason_allowlist",
        ),
        Index("ix_screening_event_receipts_center_id", "center_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    center_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("centers.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="status_update"
    )
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Populated only while ``applied`` is False: a safe, constrained code
    # explaining why (never a raw payload, screening report, or secret).
    unresolved_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class HoldRecord(Base):
    __tablename__ = "holds"
    __table_args__ = (Index("ix_holds_member_active", "member_id", "released_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    center_context_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("centers.id"),
        nullable=True,
    )
    hold_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    applied_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ReadinessDecisionRecord(Base):
    __tablename__ = "readiness_decisions"
    __table_args__ = (
        Index("ix_readiness_decisions_member_evaluated", "member_id", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communities.id"),
        nullable=False,
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unmet_requirements: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    evidence_versions: Mapped[dict[str, str]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    configuration_version: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MemberMatchPreferencesRecord(Base):
    """Explicit member-completed matching preferences.

    This is a new, opt-in table rather than an extension of ``member_profiles``.
    Existing hosted members have no row here until they complete this step, so
    they never silently become matching-eligible after this migration.
    """

    __tablename__ = "member_match_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    min_partner_age: Mapped[int] = mapped_column(Integer, nullable=False)
    max_partner_age: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MatchProposalRecord(Base):
    __tablename__ = "match_proposals"
    __table_args__ = (
        CheckConstraint(
            "member_a_id <> member_b_id",
            name="ck_match_proposals_distinct_members",
        ),
        Index(
            "ix_match_proposals_center_pair",
            "center_id",
            "member_a_id",
            "member_b_id",
        ),
        Index(
            "uq_match_proposals_open_pair",
            "center_id",
            "member_a_id",
            "member_b_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_review', 'introduced', 'active')"
            ),
            sqlite_where=text("status IN ('pending_review', 'introduced', 'active')"),
        ),
        Index("ix_match_proposals_community_status", "community_id", "status"),
        Index("ix_match_proposals_counselor_a", "counselor_a_id", "status"),
        Index("ix_match_proposals_counselor_b", "counselor_b_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communities.id"),
        nullable=False,
    )
    member_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    member_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
    )
    counselor_a_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    counselor_a_decision: Mapped[str] = mapped_column(String(20), nullable=False)
    counselor_a_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    counselor_a_reason_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    counselor_b_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    counselor_b_decision: Mapped[str] = mapped_column(String(20), nullable=False)
    counselor_b_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    counselor_b_reason_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    introduced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    member_a_response: Mapped[str | None] = mapped_column(String(20), nullable=True)
    member_a_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    member_b_response: Mapped[str | None] = mapped_column(String(20), nullable=True)
    member_b_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MatchProposalParticipantClaimRecord(Base):
    __tablename__ = "match_proposal_participant_claims"
    __table_args__ = (
        UniqueConstraint(
            "member_id", name="uq_match_proposal_participant_claims_member_id"
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_proposals.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), primary_key=True
    )


class SelfPacedSuggestionInterestRecord(Base):
    __tablename__ = "self_paced_suggestion_interests"
    __table_args__ = (
        CheckConstraint(
            "member_id <> candidate_member_id",
            name="ck_self_paced_interests_distinct_members",
        ),
        CheckConstraint(
            f"status IN ({', '.join(map(repr, _SUGGESTION_INTEREST_STATUSES))})",
            name="ck_self_paced_interests_status",
        ),
        UniqueConstraint("member_id", "candidate_member_id"),
        Index(
            "ix_self_paced_interests_candidate_status",
            "candidate_member_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"), nullable=False
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communities.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    candidate_member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("match_proposals.id"), nullable=True
    )


class RematchAuthorizationRecord(Base):
    __tablename__ = "rematch_authorizations"
    __table_args__ = (
        CheckConstraint(
            "member_a_id <> member_b_id",
            name="ck_rematch_authorizations_distinct_members",
        ),
        CheckConstraint(
            "counselor_a_id <> counselor_b_id",
            name="ck_rematch_authorizations_distinct_counselors",
        ),
        CheckConstraint(
            "counselor_a_approved_by_id IS NULL "
            "OR counselor_b_approved_by_id IS NULL "
            "OR counselor_a_approved_by_id <> counselor_b_approved_by_id",
            name="ck_rematch_authorizations_distinct_approvers",
        ),
        CheckConstraint(
            f"reason_code IN ({', '.join(map(repr, _REMATCH_REASON_CODES))})",
            name="ck_rematch_authorizations_reason",
        ),
        CheckConstraint(
            f"status IN ({', '.join(map(repr, _REMATCH_AUTHORIZATION_STATUSES))})",
            name="ck_rematch_authorizations_status",
        ),
        CheckConstraint(
            "revocation_reason_code IS NULL "
            "OR revocation_reason_code = 'assignment_changed'",
            name="ck_rematch_authorizations_revocation_reason",
        ),
        Index(
            "ix_rematch_authorizations_center_pair_status",
            "center_id",
            "member_a_id",
            "member_b_id",
            "status",
        ),
        Index(
            "uq_rematch_authorizations_live_pair",
            "center_id",
            "member_a_id",
            "member_b_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
            sqlite_where=text("status IN ('pending', 'approved')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"), nullable=False
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communities.id"), nullable=False
    )
    member_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    member_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    counselor_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    counselor_assignment_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("counselor_assignments.id"), nullable=False
    )
    counselor_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    counselor_assignment_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("counselor_assignments.id"), nullable=False
    )
    counselor_a_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )
    counselor_a_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    counselor_b_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )
    counselor_b_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("match_proposals.id")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revocation_reason_code: Mapped[str | None] = mapped_column(String(50))


class MatchedPairMessageRecord(Base):
    __tablename__ = "matched_pair_messages"
    __table_args__ = (
        Index(
            "ix_matched_pair_messages_proposal_sent",
            "proposal_id",
            "sent_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_proposals.id"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JourneyTemplateRecord(Base):
    __tablename__ = "journey_templates"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JourneyTemplateTaskRecord(Base):
    __tablename__ = "journey_template_tasks"
    __table_args__ = (UniqueConstraint("template_id", "sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journey_templates.id"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, nullable=False)


class PairJourneyRecord(Base):
    __tablename__ = "pair_journeys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_proposals.id"),
        nullable=False,
        unique=True,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journey_templates.id"),
        nullable=False,
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JourneyTaskCompletionRecord(Base):
    __tablename__ = "journey_task_completions"
    __table_args__ = (
        UniqueConstraint("journey_id", "task_id", "member_id"),
        Index(
            "ix_journey_task_completions_member",
            "journey_id",
            "member_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    journey_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pair_journeys.id"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journey_template_tasks.id"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PilotPlanRecord(Base):
    """Seeded catalog of billable pilot plans; never stores payment details."""

    __tablename__ = "pilot_plans"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BillingCustomerRecord(Base):
    """Provider customer mapping only; no card or bank details are stored."""

    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint("member_id"),
        UniqueConstraint("provider", "provider_customer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_customer_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SubscriptionRecord(Base):
    """Current entitlement projection; one authoritative row per member."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("member_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pilot_plans.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_subscription_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    grace_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_provider_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class EntitlementHistoryRecord(Base):
    """Append-only entitlement transition history; never updated or deleted."""

    __tablename__ = "entitlement_history"
    __table_args__ = (
        Index("ix_entitlement_history_member_occurred", "member_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=dict,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BillingWebhookReceiptRecord(Base):
    """Idempotency ledger of processed provider webhook event IDs."""

    __tablename__ = "billing_webhook_receipts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id"),
        Index("ix_billing_webhook_receipts_center_id", "center_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Populated only while ``applied`` is False: a safe, non-sensitive code
    # explaining why (never the raw payload or any provider secret).
    unresolved_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Nullable: some events (e.g. an unresolvable member/customer mapping)
    # never resolve to a Center. Those rows are never returned to any Center
    # admin -- see docs/runbooks/provider-failure-recovery.md for the
    # documented operational consequence and engineering escalation path.
    center_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("centers.id"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CounselorEarningRecord(Base):
    """Append-only counselor earnings ledger; balances are always derived."""

    __tablename__ = "counselor_earnings"
    __table_args__ = (
        Index(
            "uq_counselor_earnings_intake_member",
            "intake_member_id",
            unique=True,
            postgresql_where=text("entry_type = 'intake_credit'"),
            sqlite_where=text("entry_type = 'intake_credit'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
        index=True,
    )
    counselor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    intake_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JourneyCheckInRecord(Base):
    __tablename__ = "journey_check_ins"
    __table_args__ = (
        UniqueConstraint("journey_id", "member_id", "milestone"),
        Index("ix_journey_check_ins_member", "journey_id", "member_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    journey_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pair_journeys.id"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    milestone: Mapped[str] = mapped_column(String(20), nullable=False)
    relationship_status: Mapped[str] = mapped_column(String(30), nullable=False)
    support_requested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    concern_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    private_reflection: Mapped[str] = mapped_column(String(2000), nullable=False)
    share_with_counselor: Mapped[bool] = mapped_column(Boolean, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class MemberBlockRecord(Base):
    __tablename__ = "member_blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    blocker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    context: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemberReportRecord(Base):
    __tablename__ = "member_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("centers.id"),
        nullable=False,
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    reported_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    context: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class BackupDrillRunRecord(Base):
    """Operator-recorded evidence of a completed backup/restore drill.

    Populated only by a human operator running
    ``scripts/backup/verify-restore.ps1`` (or the shell equivalent) against a
    real backup, never by request-handling code. The admin operations
    dashboard reads the most recent row to compute backup-drill staleness.
    """

    __tablename__ = "backup_drill_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    performed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    target_description: Mapped[str] = mapped_column(String(200), nullable=False)
    verification_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notes: Mapped[str] = mapped_column(String(500), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
