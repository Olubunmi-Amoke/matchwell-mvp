import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from matchwell.domain.access import AccountStatus, Role
from matchwell.domain.readiness import ReadinessResult

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


class MatchingMode(StrEnum):
    COUNSELOR_BASED = "counselor_based"
    SELF_PACED = "self_paced"


class CommunityAssignmentReasonCode(StrEnum):
    PILOT_PLACEMENT = "pilot_placement"
    MEMBER_REQUEST = "member_request"
    OPERATIONS_CORRECTION = "operations_correction"


class DenominationCode(StrEnum):
    BAPTIST = "baptist"
    CATHOLIC = "catholic"
    ANGLICAN_EPISCOPAL = "anglican_episcopal"
    METHODIST_WESLEYAN = "methodist_wesleyan"
    PRESBYTERIAN_REFORMED = "presbyterian_reformed"
    PENTECOSTAL_CHARISMATIC = "pentecostal_charismatic"
    ORTHODOX = "orthodox"
    LUTHERAN = "lutheran"
    SEVENTH_DAY_ADVENTIST = "seventh_day_adventist"
    NON_DENOMINATIONAL = "non_denominational"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


DENOMINATION_LABELS: dict[DenominationCode, str] = {
    DenominationCode.BAPTIST: "Baptist",
    DenominationCode.CATHOLIC: "Catholic",
    DenominationCode.ANGLICAN_EPISCOPAL: "Anglican/Episcopal",
    DenominationCode.METHODIST_WESLEYAN: "Methodist/Wesleyan",
    DenominationCode.PRESBYTERIAN_REFORMED: "Presbyterian/Reformed",
    DenominationCode.PENTECOSTAL_CHARISMATIC: "Pentecostal/Charismatic",
    DenominationCode.ORTHODOX: "Orthodox",
    DenominationCode.LUTHERAN: "Lutheran",
    DenominationCode.SEVENTH_DAY_ADVENTIST: "Seventh-day Adventist",
    DenominationCode.NON_DENOMINATIONAL: "Non-denominational",
    DenominationCode.OTHER: "Other",
    DenominationCode.PREFER_NOT_TO_SAY: "Prefer not to say",
}


def normalize_denomination(value: str) -> tuple[DenominationCode, str | None]:
    normalized = " ".join(
        value.strip().casefold().replace("-", " ").replace("/", " ").split()
    )
    aliases = {
        "baptist": DenominationCode.BAPTIST,
        "catholic": DenominationCode.CATHOLIC,
        "roman catholic": DenominationCode.CATHOLIC,
        "anglican": DenominationCode.ANGLICAN_EPISCOPAL,
        "episcopal": DenominationCode.ANGLICAN_EPISCOPAL,
        "anglican episcopal": DenominationCode.ANGLICAN_EPISCOPAL,
        "methodist": DenominationCode.METHODIST_WESLEYAN,
        "wesleyan": DenominationCode.METHODIST_WESLEYAN,
        "methodist wesleyan": DenominationCode.METHODIST_WESLEYAN,
        "presbyterian": DenominationCode.PRESBYTERIAN_REFORMED,
        "reformed": DenominationCode.PRESBYTERIAN_REFORMED,
        "presbyterian reformed": DenominationCode.PRESBYTERIAN_REFORMED,
        "pentecostal": DenominationCode.PENTECOSTAL_CHARISMATIC,
        "charismatic": DenominationCode.PENTECOSTAL_CHARISMATIC,
        "pentecostal charismatic": DenominationCode.PENTECOSTAL_CHARISMATIC,
        "orthodox": DenominationCode.ORTHODOX,
        "lutheran": DenominationCode.LUTHERAN,
        "seventh day adventist": DenominationCode.SEVENTH_DAY_ADVENTIST,
        "sda": DenominationCode.SEVENTH_DAY_ADVENTIST,
        "non denominational": DenominationCode.NON_DENOMINATIONAL,
        "nondenominational": DenominationCode.NON_DENOMINATIONAL,
    }
    if not normalized:
        return DenominationCode.PREFER_NOT_TO_SAY, None
    code = aliases.get(normalized)
    return (code, None) if code is not None else (DenominationCode.OTHER, value.strip())


class IntroductorySessionStatus(StrEnum):
    AVAILABLE = "available"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class IntroductorySessionReasonCode(StrEnum):
    MEMBER_REQUESTED = "member_requested"
    COUNSELOR_UNAVAILABLE = "counselor_unavailable"
    OPERATIONS_RESCHEDULE = "operations_reschedule"


@dataclass(frozen=True, slots=True)
class ConsentAcknowledgement:
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class CovenantAffirmation:
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class CommunityCovenantView:
    id: uuid.UUID
    policy_key: str
    display_version: str
    revision: int
    effective_at: datetime
    title: str
    body_markdown: str
    required_affirmations: tuple[CovenantAffirmation, ...]
    accepted: bool


@dataclass(frozen=True, slots=True)
class IntroductorySessionView:
    id: uuid.UUID
    member_id: uuid.UUID
    status: IntroductorySessionStatus
    counselor_id: uuid.UUID | None
    counselor_name: str | None
    scheduled_at: datetime | None
    completed_at: datetime | None
    reason_code: IntroductorySessionReasonCode | None
    created_at: datetime
    updated_at: datetime


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
    city: str
    state: str
    denomination_code: DenominationCode = DenominationCode.PREFER_NOT_TO_SAY
    denomination_other: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentView:
    id: uuid.UUID
    title: str
    version: str
    body_markdown: str
    required_acknowledgements: tuple[ConsentAcknowledgement, ...]
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
    matching_mode: MatchingMode = MatchingMode.COUNSELOR_BASED


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
    community_id: uuid.UUID | None = None
    community_name: str = ""
    matching_mode: MatchingMode = MatchingMode.COUNSELOR_BASED

    @property
    def eligible(self) -> bool:
        return self.readiness.eligible

    @property
    def readiness_completed_count(self) -> int:
        return self.readiness.completed_ordinary_requirement_count


@dataclass(frozen=True, slots=True)
class CommunityView:
    id: uuid.UUID
    name: str
    matching_mode: MatchingMode


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
