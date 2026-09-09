import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from matchwell.domain.access import AccountStatus, Role
from matchwell.domain.readiness import TOTAL_ORDINARY_REQUIREMENTS, ReadinessResult

# A stable, non-human identity used to attribute provider-callback-driven
# audit events, matching billing's ``BILLING_SYSTEM_ACTOR_ID`` pattern and
# the "background jobs and provider callbacks use dedicated identities"
# authorization principle.
SCREENING_SYSTEM_ACTOR_ID = uuid.UUID(int=0)


class CounselorDecisionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class ScreeningStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    FAILED = "failed"


class ScreeningReasonCode(StrEnum):
    """Safe, constrained screening reason/status codes.

    Never free text: provider reports and free-form screening detail must
    never enter Matchwell. Only these operationally meaningful codes may be
    recorded, displayed, or included in audit metadata.
    """

    IDENTITY_VERIFICATION_FAILED = "identity_verification_failed"
    PROVIDER_INELIGIBLE_RESULT = "provider_ineligible_result"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    DOCUMENT_UNREADABLE = "document_unreadable"
    DUPLICATE_SUBMISSION = "duplicate_submission"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    EXPIRED = "expired"
    OTHER_OPERATIONAL = "other_operational"


@dataclass(frozen=True, slots=True)
class ScreeningProviderEvent:
    """A normalized, provider-neutral screening callback event.

    Mirrors ``BillingWebhookEvent``'s idempotent-processing shape. This pilot
    has no live external screening provider; any future adapter verifies the
    real provider's signature and keeps its payload behind its own boundary,
    constructing only this safe, minimal event.
    """

    provider: str
    provider_event_id: str
    provider_reference: str | None
    status: ScreeningStatus | None
    reason_code: ScreeningReasonCode | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ScreeningFailureView:
    """A safe, admin-facing view of a screening receipt that never applied.

    Mirrors ``WebhookFailureView`` in the billing domain. Never carries a raw
    provider payload, screening report, or free-text detail -- only
    identifiers and a constrained reason code needed to triage and retry.
    """

    id: uuid.UUID
    provider: str
    provider_event_id: str
    event_type: str
    member_id: uuid.UUID | None
    unresolved_reason: str | None
    received_at: datetime


@dataclass(frozen=True, slots=True)
class AccountRow:
    """Admin-facing account row for the account access/security workspace.

    Never includes OIDC subject, tokens, or any authentication secret.
    """

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    status: AccountStatus
    disabled_reason_code: str | None
    disabled_at: datetime | None
    is_self: bool
    counselor_needs_reassignment: bool


@dataclass(frozen=True, slots=True)
class ProfileInput:
    display_name: str
    birth_date: date
    faith_affirmed: bool
    relationship_intent: str
    denomination: str
    city: str
    state: str


@dataclass(frozen=True, slots=True)
class ConsentView:
    id: uuid.UUID
    title: str
    version: str
    body_markdown: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class AssessmentQuestion:
    id: str
    prompt: str


@dataclass(frozen=True, slots=True)
class AssessmentView:
    assignment_id: uuid.UUID
    title: str
    description: str
    questions: tuple[AssessmentQuestion, ...]
    completed: bool


@dataclass(frozen=True, slots=True)
class MemberProgress:
    member_id: uuid.UUID
    display_name: str
    readiness: ReadinessResult
    counselor_status: CounselorDecisionStatus
    screening_status: ScreeningStatus
    community_name: str


@dataclass(frozen=True, slots=True)
class OperationsMember:
    id: uuid.UUID
    email: str
    display_name: str
    center_id: uuid.UUID
    counselor_id: uuid.UUID | None
    counselor_status: CounselorDecisionStatus
    screening_status: ScreeningStatus
    hold_active: bool
    readiness: ReadinessResult
    account_status: AccountStatus = AccountStatus.ACTIVE
    counselor_needs_reassignment: bool = False

    @property
    def eligible(self) -> bool:
        return self.readiness.eligible

    @property
    def readiness_completed_count(self) -> int:
        return TOTAL_ORDINARY_REQUIREMENTS - len(self.readiness.unmet_requirements)


@dataclass(frozen=True, slots=True)
class InvitationInput:
    email: str
    role: Role
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InvitationView:
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    accepted_at: datetime | None


AssessmentAnswers = dict[str, int]
SafeMetadata = dict[str, Any]
