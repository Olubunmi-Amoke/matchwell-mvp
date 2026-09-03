import uuid
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from matchwell.domain.matching import (
    MAX_PARTNER_AGE,
    MIN_PARTNER_AGE,
    BlockInput,
    CandidateReviewItem,
    CounselorReviewDecision,
    IntroductionView,
    MatchedPairView,
    MatchPreferencesInput,
    MatchPreferencesView,
    MemberResponseDecision,
    ReportInput,
    SafetyCategory,
)
from matchwell.domain.pilot import (
    AssessmentAnswers,
    AssessmentView,
    ConsentView,
    CounselorDecisionStatus,
    InvitationInput,
    InvitationView,
    MemberProgress,
    OperationsMember,
    ProfileInput,
    ScreeningStatus,
)


class PilotRepository(Protocol):
    def resolve_identity(
        self,
        identity: OidcIdentity,
        admin_emails: frozenset[str],
    ) -> AuthenticatedUser | None: ...

    def get_active_consent(self, member_id: uuid.UUID) -> ConsentView: ...

    def accept_consent(
        self,
        member_id: uuid.UUID,
        consent_version_id: uuid.UUID,
    ) -> None: ...

    def get_profile(self, member_id: uuid.UUID) -> ProfileInput | None: ...

    def save_profile(self, member_id: uuid.UUID, profile: ProfileInput) -> None: ...

    def get_assessment(self, member_id: uuid.UUID) -> AssessmentView: ...

    def submit_assessment(
        self,
        member_id: uuid.UUID,
        assignment_id: uuid.UUID,
        answers: AssessmentAnswers,
    ) -> None: ...

    def get_progress(self, member_id: uuid.UUID) -> MemberProgress: ...

    def create_invitation(
        self,
        actor: AuthenticatedUser,
        invitation: InvitationInput,
    ) -> InvitationView: ...

    def list_invitations(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[InvitationView]: ...

    def list_members(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]: ...

    def list_counselors(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[AuthenticatedUser]: ...

    def assign_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        counselor_id: uuid.UUID,
    ) -> None: ...

    def list_assigned_members(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]: ...

    def record_counselor_decision(
        self,
        counselor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: CounselorDecisionStatus,
        reason_code: str | None,
    ) -> None: ...

    def record_screening_status(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: ScreeningStatus,
        provider_event_id: str,
        provider_reference: str,
        reason_code: str | None,
    ) -> bool: ...

    def apply_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None: ...

    def release_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
    ) -> None: ...

    def get_match_preferences(self, member_id: uuid.UUID) -> MatchPreferencesView: ...

    def save_match_preferences(
        self,
        member_id: uuid.UUID,
        preferences: MatchPreferencesInput,
    ) -> None: ...

    def generate_candidates(self, actor: AuthenticatedUser) -> int: ...

    def candidate_queue(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CandidateReviewItem]: ...

    def review_candidate(
        self,
        counselor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        decision: CounselorReviewDecision,
        reason_code: str | None,
    ) -> None: ...

    def get_introduction(
        self,
        member_id: uuid.UUID,
    ) -> IntroductionView | None: ...

    def respond_to_introduction(
        self,
        member_id: uuid.UUID,
        proposal_id: uuid.UUID,
        decision: MemberResponseDecision,
    ) -> None: ...

    def get_matched_pair(
        self,
        member_id: uuid.UUID,
    ) -> MatchedPairView | None: ...

    def get_recent_match(
        self,
        member_id: uuid.UUID,
    ) -> IntroductionView | None: ...

    def block_member(
        self,
        actor: AuthenticatedUser,
        block: BlockInput,
    ) -> None: ...

    def report_member(
        self,
        actor: AuthenticatedUser,
        report: ReportInput,
    ) -> None: ...


class PilotService:
    def __init__(
        self,
        repository: PilotRepository,
        admin_emails: frozenset[str],
    ) -> None:
        self._repository = repository
        self._admin_emails = admin_emails

    def sign_in(self, identity: OidcIdentity) -> AuthenticatedUser | None:
        if identity.issuer.rstrip("/") != "https://accounts.google.com":
            raise AuthenticationError("The identity provider is not authorized.")
        if not identity.subject.strip() or not identity.email.strip():
            raise AuthenticationError(
                "Google did not return the required identity claims."
            )
        if not identity.email_verified:
            raise ValidationError("Google must verify the account email.")
        return self._repository.resolve_identity(identity, self._admin_emails)

    def consent(self, actor: AuthenticatedUser) -> ConsentView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_active_consent(actor.id)

    def accept_consent(
        self,
        actor: AuthenticatedUser,
        consent_version_id: uuid.UUID,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        self._repository.accept_consent(actor.id, consent_version_id)

    def profile(self, actor: AuthenticatedUser) -> ProfileInput | None:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_profile(actor.id)

    def save_profile(self, actor: AuthenticatedUser, profile: ProfileInput) -> None:
        self._require_role(actor, Role.MEMBER)
        if self._age_on(profile.birth_date, date.today()) < 18:
            raise ValidationError("Members must be at least 18 years old.")
        if not profile.faith_affirmed:
            raise ValidationError("Christian faith affirmation is required.")
        required_text = (
            profile.display_name,
            profile.relationship_intent,
            profile.city,
            profile.state,
        )
        if any(not value.strip() for value in required_text):
            raise ValidationError("Complete every required profile field.")
        self._repository.save_profile(actor.id, profile)

    def assessment(self, actor: AuthenticatedUser) -> AssessmentView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_assessment(actor.id)

    def submit_assessment(
        self,
        actor: AuthenticatedUser,
        assignment_id: uuid.UUID,
        answers: AssessmentAnswers,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        if not answers or any(value < 1 or value > 5 for value in answers.values()):
            raise ValidationError("Answer every assessment item from 1 to 5.")
        self._repository.submit_assessment(actor.id, assignment_id, answers)

    def progress(self, actor: AuthenticatedUser) -> MemberProgress:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_progress(actor.id)

    def create_invitation(
        self,
        actor: AuthenticatedUser,
        invitation: InvitationInput,
    ) -> InvitationView:
        self._require_role(actor, Role.ADMIN)
        if invitation.role not in {Role.MEMBER, Role.COUNSELOR}:
            raise ValidationError("Only members and counselors can be invited.")
        return self._repository.create_invitation(actor, invitation)

    def invitations(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[InvitationView]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_invitations(actor)

    def members(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_members(actor)

    def counselors(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[AuthenticatedUser]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_counselors(actor)

    def assign_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        counselor_id: uuid.UUID,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        self._repository.assign_counselor(actor, member_id, counselor_id)

    def assigned_members(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.list_assigned_members(actor)

    def record_counselor_decision(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: CounselorDecisionStatus,
        reason_code: str | None = None,
    ) -> None:
        self._require_role(actor, Role.COUNSELOR)
        self._repository.record_counselor_decision(
            actor,
            member_id,
            status,
            reason_code,
        )

    def record_screening_status(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: ScreeningStatus,
        provider_event_id: str,
        provider_reference: str,
        reason_code: str | None = None,
    ) -> bool:
        self._require_role(actor, Role.ADMIN)
        if not provider_event_id.strip() or not provider_reference.strip():
            raise ValidationError("Provider event and reference IDs are required.")
        return self._repository.record_screening_status(
            actor,
            member_id,
            status,
            provider_event_id.strip(),
            provider_reference.strip(),
            reason_code,
        )

    def apply_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        if not reason_code.strip():
            raise ValidationError("A safe reason code is required.")
        self._repository.apply_hold(actor, member_id, reason_code.strip())

    def release_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        self._repository.release_hold(actor, member_id)

    def match_preferences(self, actor: AuthenticatedUser) -> MatchPreferencesView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_match_preferences(actor.id)

    def save_match_preferences(
        self,
        actor: AuthenticatedUser,
        preferences: MatchPreferencesInput,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        if preferences.min_partner_age < MIN_PARTNER_AGE:
            raise ValidationError(
                f"The minimum partner age must be at least {MIN_PARTNER_AGE}."
            )
        if preferences.max_partner_age > MAX_PARTNER_AGE:
            raise ValidationError(
                f"The maximum partner age must be at most {MAX_PARTNER_AGE}."
            )
        if preferences.min_partner_age > preferences.max_partner_age:
            raise ValidationError(
                "The minimum partner age cannot exceed the maximum partner age."
            )
        self._repository.save_match_preferences(actor.id, preferences)

    def generate_candidates(self, actor: AuthenticatedUser) -> int:
        self._require_role(actor, Role.ADMIN)
        return self._repository.generate_candidates(actor)

    def candidate_queue(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[CandidateReviewItem]:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.candidate_queue(actor)

    def review_candidate(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        decision: CounselorReviewDecision,
        reason_code: str | None = None,
    ) -> None:
        self._require_role(actor, Role.COUNSELOR)
        if decision is CounselorReviewDecision.PENDING:
            raise ValidationError("Record an approved or declined review decision.")
        self._repository.review_candidate(
            actor,
            proposal_id,
            decision,
            reason_code.strip() if reason_code else None,
        )

    def introduction(self, actor: AuthenticatedUser) -> IntroductionView | None:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_introduction(actor.id)

    def respond_to_introduction(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        decision: MemberResponseDecision,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        self._repository.respond_to_introduction(actor.id, proposal_id, decision)

    def matched_pair(self, actor: AuthenticatedUser) -> MatchedPairView | None:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_matched_pair(actor.id)

    def recent_match(self, actor: AuthenticatedUser) -> IntroductionView | None:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_recent_match(actor.id)

    def block_member(
        self,
        actor: AuthenticatedUser,
        blocked_member_id: uuid.UUID,
        category: SafetyCategory,
        context: str | None = None,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        if blocked_member_id == actor.id:
            raise ValidationError("You cannot block yourself.")
        safe_context = context.strip() if context else None
        if safe_context and len(safe_context) > 500:
            raise ValidationError("Keep the block context under 500 characters.")
        self._repository.block_member(
            actor,
            BlockInput(
                blocked_member_id=blocked_member_id,
                category=category,
                context=safe_context or None,
            ),
        )

    def report_member(
        self,
        actor: AuthenticatedUser,
        reported_member_id: uuid.UUID,
        category: SafetyCategory,
        context: str,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        if reported_member_id == actor.id:
            raise ValidationError("You cannot report yourself.")
        safe_context = context.strip()
        if len(safe_context) < 10:
            raise ValidationError(
                "Describe the concern with at least 10 characters of context."
            )
        if len(safe_context) > 500:
            raise ValidationError("Keep the report context under 500 characters.")
        self._repository.report_member(
            actor,
            ReportInput(
                reported_member_id=reported_member_id,
                category=category,
                context=safe_context,
            ),
        )

    @staticmethod
    def _require_role(actor: AuthenticatedUser, role: Role) -> None:
        if actor.role is not role:
            raise AuthorizationError("This workspace is not available to your role.")

    @staticmethod
    def _age_on(birth_date: date, on_date: date) -> int:
        return (
            on_date.year
            - birth_date.year
            - ((on_date.month, on_date.day) < (birth_date.month, birth_date.day))
        )
