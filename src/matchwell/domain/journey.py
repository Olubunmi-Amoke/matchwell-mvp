import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

PILOT_CURRICULUM_KEY = "pilot-foundations"


class TaskScope(StrEnum):
    SHARED = "shared"
    INDIVIDUAL = "individual"


class CheckInMilestone(StrEnum):
    DAY_30 = "day_30"
    DAY_60 = "day_60"
    DAY_90 = "day_90"

    @property
    def days(self) -> int:
        return {
            CheckInMilestone.DAY_30: 30,
            CheckInMilestone.DAY_60: 60,
            CheckInMilestone.DAY_90: 90,
        }[self]


class RelationshipStatus(StrEnum):
    ENCOURAGED = "encouraged"
    STEADY = "steady"
    UNCERTAIN = "uncertain"
    CONCERNED = "concerned"


class ReminderState(StrEnum):
    SCHEDULED = "scheduled"
    UPCOMING = "upcoming"
    DUE = "due"
    OVERDUE = "overdue"
    COMPLETED = "completed"


def reminder_state(
    due_at: datetime,
    submitted_at: datetime | None,
    now: datetime,
) -> ReminderState:
    if submitted_at is not None:
        return ReminderState.COMPLETED
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if now.date() > due_at.date():
        return ReminderState.OVERDUE
    if now.date() == due_at.date():
        return ReminderState.DUE
    if now >= due_at - timedelta(days=7):
        return ReminderState.UPCOMING
    return ReminderState.SCHEDULED


@dataclass(frozen=True, slots=True)
class JourneyTaskView:
    id: uuid.UUID
    sequence: int
    title: str
    description: str
    scope: TaskScope
    due_day: int
    completed_at: datetime | None
    partner_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberCheckInView:
    milestone: CheckInMilestone
    due_at: datetime
    reminder_state: ReminderState
    relationship_status: RelationshipStatus | None
    support_requested: bool
    concern_flag: bool
    private_reflection: str
    share_with_counselor: bool
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberJourneyView:
    id: uuid.UUID
    proposal_id: uuid.UUID
    template_name: str
    template_version: str
    started_at: datetime
    tasks: tuple[JourneyTaskView, ...]
    check_ins: tuple[MemberCheckInView, ...]

    @property
    def completed_task_count(self) -> int:
        return sum(item.completed_at is not None for item in self.tasks)


@dataclass(frozen=True, slots=True)
class CounselorCheckInView:
    milestone: CheckInMilestone
    due_at: datetime
    reminder_state: ReminderState
    submitted_at: datetime | None
    support_requested: bool | None
    concern_flag: bool | None
    shared_reflection: str | None


@dataclass(frozen=True, slots=True)
class CounselorJourneyMemberView:
    member_id: uuid.UUID
    display_name: str
    completed_task_count: int
    total_task_count: int
    is_my_member: bool
    check_ins: tuple[CounselorCheckInView, ...]


@dataclass(frozen=True, slots=True)
class CounselorJourneyView:
    proposal_id: uuid.UUID
    member_a_display_name: str
    member_b_display_name: str
    journey_id: uuid.UUID | None
    template_name: str | None
    template_version: str | None
    started_at: datetime | None
    members: tuple[CounselorJourneyMemberView, ...]
