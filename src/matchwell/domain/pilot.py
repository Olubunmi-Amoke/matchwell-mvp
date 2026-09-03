import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from matchwell.domain.access import Role
from matchwell.domain.readiness import ReadinessResult


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

    @property
    def eligible(self) -> bool:
        return self.readiness.eligible

    @property
    def readiness_completed_count(self) -> int:
        return 7 - len(self.readiness.unmet_requirements)


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
