import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Gender(StrEnum):
    MAN = "man"
    WOMAN = "woman"


class SafetyCategory(StrEnum):
    HARASSMENT = "harassment"
    DISHONESTY = "dishonesty"
    SAFETY_CONCERN = "safety_concern"
    INAPPROPRIATE_CONTACT = "inappropriate_contact"
    OTHER = "other"


class ProposalStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    INTRODUCED = "introduced"
    ACTIVE = "active"
    CLOSED = "closed"


class CounselorReviewDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class MemberResponseDecision(StrEnum):
    ACCEPTED = "accepted"
    DECLINED = "declined"


MIN_PARTNER_AGE = 18
MAX_PARTNER_AGE = 99


@dataclass(frozen=True, slots=True)
class MatchPreferencesInput:
    gender: Gender
    min_partner_age: int
    max_partner_age: int


@dataclass(frozen=True, slots=True)
class MatchPreferencesView:
    gender: Gender | None
    min_partner_age: int | None
    max_partner_age: int | None
    completed: bool


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Safe, minimized attributes used for deterministic candidate scoring.

    Assessment answers, screening details, counselor notes, exact birth dates,
    emails, and contact details are intentionally excluded.
    """

    member_id: uuid.UUID
    gender: Gender
    age: int
    min_partner_age: int
    max_partner_age: int
    city: str
    state: str
    denomination: str
    relationship_intent: str


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    label: str
    weight: float
    points: float


@dataclass(frozen=True, slots=True)
class MatchScore:
    total: float
    contributions: tuple[ScoreContribution, ...]

    @property
    def explanations(self) -> tuple[str, ...]:
        return tuple(
            f"{item.label}: {round(item.points)} of {round(item.weight)} points"
            for item in self.contributions
        )


class MatchScorer:
    """Deterministic, explainable, weighted candidate compatibility scoring.

    Scoring never reads assessment answers, screening details, or counseling
    notes. Only community-safe preference and profile attributes contribute.
    """

    LOCATION_WEIGHT = 25.0
    DENOMINATION_WEIGHT = 20.0
    AGE_WEIGHT = 25.0
    INTENT_WEIGHT = 30.0

    def is_reciprocally_compatible(
        self,
        a: CandidateEvidence,
        b: CandidateEvidence,
    ) -> bool:
        if {a.gender, b.gender} != {Gender.MAN, Gender.WOMAN}:
            return False
        return (
            a.min_partner_age <= b.age <= a.max_partner_age
            and b.min_partner_age <= a.age <= b.max_partner_age
        )

    def score(self, a: CandidateEvidence, b: CandidateEvidence) -> MatchScore:
        contributions = (
            self._location(a, b),
            self._denomination(a, b),
            self._age(a, b),
            self._intent(a, b),
        )
        total = sum(item.points for item in contributions)
        return MatchScore(total=total, contributions=contributions)

    def _location(
        self,
        a: CandidateEvidence,
        b: CandidateEvidence,
    ) -> ScoreContribution:
        city_a, city_b = a.city.strip().casefold(), b.city.strip().casefold()
        state_a, state_b = a.state.strip().casefold(), b.state.strip().casefold()
        if city_a and city_a == city_b:
            points = self.LOCATION_WEIGHT
        elif state_a and state_a == state_b:
            points = self.LOCATION_WEIGHT * 0.6
        else:
            points = 0.0
        return ScoreContribution("Location", self.LOCATION_WEIGHT, points)

    def _denomination(
        self,
        a: CandidateEvidence,
        b: CandidateEvidence,
    ) -> ScoreContribution:
        value_a = a.denomination.strip().casefold()
        value_b = b.denomination.strip().casefold()
        points = self.DENOMINATION_WEIGHT if value_a and value_a == value_b else 0.0
        return ScoreContribution("Denomination", self.DENOMINATION_WEIGHT, points)

    def _age(
        self,
        a: CandidateEvidence,
        b: CandidateEvidence,
    ) -> ScoreContribution:
        gap = abs(a.age - b.age)
        span = max(
            a.max_partner_age - a.min_partner_age,
            b.max_partner_age - b.min_partner_age,
            1,
        )
        closeness = max(0.0, 1 - (gap / span))
        points = self.AGE_WEIGHT * closeness
        return ScoreContribution("Age preference", self.AGE_WEIGHT, points)

    def _intent(
        self,
        a: CandidateEvidence,
        b: CandidateEvidence,
    ) -> ScoreContribution:
        words_a = set(a.relationship_intent.strip().casefold().split())
        words_b = set(b.relationship_intent.strip().casefold().split())
        if not words_a or not words_b:
            points = 0.0
        else:
            overlap = len(words_a & words_b) / len(words_a | words_b)
            points = self.INTENT_WEIGHT * overlap
        return ScoreContribution("Relationship intent", self.INTENT_WEIGHT, points)


@dataclass(frozen=True, slots=True)
class CandidateReviewItem:
    proposal_id: uuid.UUID
    member_id: uuid.UUID
    member_display_name: str
    partner_id: uuid.UUID
    partner_display_name: str
    score: float
    explanations: tuple[str, ...]
    my_decision: CounselorReviewDecision
    partner_counselor_decision: CounselorReviewDecision
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IntroductionView:
    proposal_id: uuid.UUID
    partner_id: uuid.UUID
    partner_display_name: str
    partner_city: str
    partner_state: str
    partner_denomination: str
    partner_relationship_intent: str
    explanations: tuple[str, ...]
    my_response: MemberResponseDecision | None
    status: ProposalStatus


@dataclass(frozen=True, slots=True)
class MatchedPairView:
    proposal_id: uuid.UUID
    partner_id: uuid.UUID
    partner_display_name: str
    activated_at: datetime


@dataclass(frozen=True, slots=True)
class BlockInput:
    blocked_member_id: uuid.UUID
    category: SafetyCategory
    context: str | None


@dataclass(frozen=True, slots=True)
class ReportInput:
    reported_member_id: uuid.UUID
    category: SafetyCategory
    context: str
