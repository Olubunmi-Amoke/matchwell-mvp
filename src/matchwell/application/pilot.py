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
