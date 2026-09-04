import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matchwell.domain.access import (
    AuthenticatedUser,
    OidcIdentity,
    Role,
    normalize_email,
)
from matchwell.domain.errors import ConflictError, NotFoundError, ValidationError
from matchwell.domain.matching import (
    BlockInput,
    CandidateEvidence,
    CandidateGenerationDiagnostics,
    CandidateMemberDiagnostic,
    CandidatePairDiagnostic,
    CandidateReviewItem,
    CounselorConversationStatus,
    CounselorReviewDecision,
    Gender,
    IntroductionView,
    MatchedPairView,
    MatchPreferencesInput,
    MatchPreferencesView,
    MatchScorer,
    MemberResponseDecision,
    MessageView,
    ProposalStatus,
    ReportInput,
)
from matchwell.domain.pilot import (
    AssessmentAnswers,
    AssessmentQuestion,
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
from matchwell.domain.readiness import (
    ReadinessEvaluator,
    ReadinessEvidence,
    ReadinessResult,
)
from matchwell.infrastructure.persistence.database import DatabaseSessionFactory
from matchwell.infrastructure.persistence.models import (
    AssessmentAssignmentRecord,
    AssessmentDefinitionRecord,
    AuditEventRecord,
    CenterRecord,
    CommunityRecord,
    ConsentAcceptanceRecord,
    ConsentVersionRecord,
    CounselorAssignmentRecord,
    CounselorDecisionRecord,
    HoldRecord,
    InvitationRecord,
    MatchedPairMessageRecord,
    MatchProposalRecord,
    MemberBlockRecord,
    MemberMatchPreferencesRecord,
    MemberProfileRecord,
    MemberReportRecord,
    OutboxMessageRecord,
    ReadinessDecisionRecord,
    ScreeningCaseRecord,
    ScreeningEventReceiptRecord,
    UserRecord,
)

PILOT_CENTER_SLUG = "matchwell-pilot"
READINESS_CONFIGURATION_VERSION = "pilot-v1"
_OPEN_PROPOSAL_STATUSES = (
    ProposalStatus.PENDING_REVIEW.value,
    ProposalStatus.INTRODUCED.value,
    ProposalStatus.ACTIVE.value,
)


class SqlAlchemyPilotRepository:
    def __init__(
        self,
        sessions: DatabaseSessionFactory,
        evaluator: ReadinessEvaluator,
        scorer: MatchScorer | None = None,
    ) -> None:
        self._sessions = sessions
        self._evaluator = evaluator
        self._scorer = scorer or MatchScorer()

    def resolve_identity(
        self,
        identity: OidcIdentity,
        admin_emails: frozenset[str],
    ) -> AuthenticatedUser | None:
        email = normalize_email(identity.email)
        now = self._now()
        with self._sessions.session() as session, session.begin():
            existing = session.scalar(
                select(UserRecord).where(
                    UserRecord.oidc_issuer == identity.issuer,
                    UserRecord.oidc_subject == identity.subject,
                )
            )
            if existing is not None:
                existing.name = identity.name.strip() or existing.name
                return self._user(existing)

            center = self._pilot_center(session)
            invitation = session.scalar(
                select(InvitationRecord)
                .where(
                    InvitationRecord.email == email,
                    InvitationRecord.accepted_at.is_(None),
                    InvitationRecord.expires_at > now,
                )
                .order_by(InvitationRecord.created_at.desc())
            )
            is_admin = email in admin_emails
            if invitation is None and not is_admin:
                return None

            if is_admin:
                role = Role.ADMIN
            elif invitation is not None:
                role = Role(invitation.role)
            else:
                return None
            user = UserRecord(
                center_id=center.id,
                oidc_issuer=identity.issuer,
                oidc_subject=identity.subject,
                email=email,
                name=identity.name.strip() or email,
                role=role.value,
            )
            session.add(user)
            session.flush()

            if invitation is not None:
                invitation.accepted_at = now
                invitation.accepted_by_user_id = user.id

            if role is Role.MEMBER:
                definition = self._active_assessment_definition(session)
                session.add(
                    AssessmentAssignmentRecord(
                        member_id=user.id,
                        definition_id=definition.id,
                        assigned_at=now,
                        expires_at=now + timedelta(days=90),
                    )
                )

            self._audit(
                session,
                actor_id=user.id,
                action="identity.account_created",
                subject_id=user.id,
                center_id=center.id,
                metadata={"role": role.value},
            )
            if role is Role.MEMBER:
                session.flush()
                self._reevaluate(session, user.id, user.id)
            return self._user(user)

    def get_active_consent(self, member_id: uuid.UUID) -> ConsentView:
        with self._sessions.session() as session:
            consent = self._active_consent(session)
            accepted = session.scalar(
                select(ConsentAcceptanceRecord.id).where(
                    ConsentAcceptanceRecord.user_id == member_id,
                    ConsentAcceptanceRecord.consent_version_id == consent.id,
                )
            )
            return ConsentView(
                id=consent.id,
                title=consent.title,
                version=consent.version,
                body_markdown=consent.body_markdown,
                accepted=accepted is not None,
            )

    def accept_consent(
        self,
        member_id: uuid.UUID,
        consent_version_id: uuid.UUID,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            consent = self._active_consent(session)
            if consent.id != consent_version_id:
                raise ValidationError("The consent version is no longer current.")
            existing = session.scalar(
                select(ConsentAcceptanceRecord.id).where(
                    ConsentAcceptanceRecord.user_id == member_id,
                    ConsentAcceptanceRecord.consent_version_id == consent.id,
                )
            )
            if existing is not None:
                return
            session.add(
                ConsentAcceptanceRecord(
                    user_id=member_id,
                    consent_version_id=consent.id,
                )
            )
            self._audit(
                session,
                actor_id=member_id,
                action="consent.accepted",
                subject_id=member_id,
                center_id=self._member_center_id(session, member_id),
                metadata={"policy_key": consent.policy_key, "version": consent.version},
            )
            session.flush()
            self._reevaluate(session, member_id, member_id)

    def get_profile(self, member_id: uuid.UUID) -> ProfileInput | None:
        with self._sessions.session() as session:
            profile = session.get(MemberProfileRecord, member_id)
            if profile is None:
                return None
            return ProfileInput(
                display_name=profile.display_name,
                birth_date=profile.birth_date,
                faith_affirmed=profile.faith_affirmed,
                relationship_intent=profile.relationship_intent,
                denomination=profile.denomination,
                city=profile.city,
                state=profile.state,
            )

    def save_profile(self, member_id: uuid.UUID, profile: ProfileInput) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id)
            record = session.get(MemberProfileRecord, member_id)
            values = {
                "display_name": profile.display_name.strip(),
                "birth_date": profile.birth_date,
                "faith_affirmed": profile.faith_affirmed,
                "relationship_intent": profile.relationship_intent.strip(),
                "denomination": profile.denomination.strip(),
                "city": profile.city.strip(),
                "state": profile.state.strip(),
                "completed_at": self._now(),
            }
            if record is None:
                session.add(MemberProfileRecord(user_id=member_id, **values))
            else:
                for attribute, value in values.items():
                    setattr(record, attribute, value)
            self._audit(
                session,
                actor_id=member_id,
                action="profile.completed",
                subject_id=member_id,
                center_id=self._member_center_id(session, member_id),
                metadata={},
            )
            session.flush()
            self._reevaluate(session, member_id, member_id)

    def get_assessment(self, member_id: uuid.UUID) -> AssessmentView:
        with self._sessions.session() as session, session.begin():
            assignment, definition = self._ensure_current_assessment(session, member_id)
            self._reevaluate(
                session,
                member_id,
                member_id,
                only_if_changed=True,
            )
            questions = tuple(
                AssessmentQuestion(id=item["id"], prompt=item["prompt"])
                for item in definition.questions
            )
            return AssessmentView(
                assignment_id=assignment.id,
                title=definition.title,
                description=definition.description,
                questions=questions,
                completed=assignment.completed_at is not None,
            )

    def submit_assessment(
        self,
        member_id: uuid.UUID,
        assignment_id: uuid.UUID,
        answers: AssessmentAnswers,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            assignment, definition = self._member_assessment(session, member_id)
            if assignment.id != assignment_id:
                raise ValidationError("The assessment assignment is no longer current.")
            if self._is_expired(assignment.expires_at):
                raise ValidationError("The assessment assignment has expired.")
            expected = {item["id"] for item in definition.questions}
            if set(answers) != expected:
                raise ValidationError("Answer every item in the assigned assessment.")
            assignment.answers = dict(answers)
            assignment.completed_at = self._now()
            self._audit(
                session,
                actor_id=member_id,
                action="assessment.completed",
                subject_id=member_id,
                center_id=self._member_center_id(session, member_id),
                metadata={
                    "definition_key": definition.key,
                    "version": definition.version,
                },
            )
            session.flush()
            self._reevaluate(session, member_id, member_id)

    def get_progress(self, member_id: uuid.UUID) -> MemberProgress:
        with self._sessions.session() as session, session.begin():
            member = self._member(session, member_id)
            readiness = self._reevaluate(
                session,
                member_id,
                member_id,
                only_if_changed=True,
            )
            counselor_status = self._counselor_status(session, member_id)
            screening_status = self._screening_status(session, member_id)
            community = self._community(session, member.center_id)
            profile = session.get(MemberProfileRecord, member_id)
            return MemberProgress(
                member_id=member_id,
                display_name=profile.display_name
                if profile is not None
                else member.name,
                readiness=readiness,
                counselor_status=counselor_status,
                screening_status=screening_status,
                community_name=community.name,
            )

    def create_invitation(
        self,
        actor: AuthenticatedUser,
        invitation: InvitationInput,
    ) -> InvitationView:
        email = normalize_email(invitation.email)
        with self._sessions.session() as session, session.begin():
            if invitation.expires_at <= self._now():
                raise ValidationError("Invitation expiry must be in the future.")
            existing_user = session.scalar(
                select(UserRecord.id).where(UserRecord.email == email)
            )
            if existing_user is not None:
                raise ConflictError("That email already has a Matchwell account.")
            active_invitation = session.scalar(
                select(InvitationRecord.id).where(
                    InvitationRecord.email == email,
                    InvitationRecord.accepted_at.is_(None),
                    InvitationRecord.expires_at > self._now(),
                )
            )
            if active_invitation is not None:
                raise ConflictError("That email already has an active invitation.")
            record = InvitationRecord(
                center_id=actor.center_id,
                email=email,
                role=invitation.role.value,
                invited_by_id=actor.id,
                expires_at=invitation.expires_at,
            )
            session.add(record)
            session.flush()
            self._audit(
                session,
                actor_id=actor.id,
                action="invitation.created",
                subject_id=record.id,
                center_id=actor.center_id,
                metadata={"role": invitation.role.value},
            )
            return self._invitation(record)

    def list_invitations(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[InvitationView]:
        with self._sessions.session() as session:
            records = session.scalars(
                select(InvitationRecord)
                .where(InvitationRecord.center_id == actor.center_id)
                .order_by(InvitationRecord.created_at.desc())
            ).all()
            return tuple(self._invitation(record) for record in records)

    def list_members(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]:
        with self._sessions.session() as session, session.begin():
            members = session.scalars(
                select(UserRecord)
                .where(
                    UserRecord.center_id == actor.center_id,
                    UserRecord.role == Role.MEMBER.value,
                )
                .order_by(UserRecord.name)
            ).all()
            result = tuple(
                self._operations_member(session, member, actor.id) for member in members
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="admin.member_queue_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(result)},
            )
            return result

    def list_counselors(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[AuthenticatedUser]:
        with self._sessions.session() as session:
            records = session.scalars(
                select(UserRecord)
                .where(
                    UserRecord.center_id == actor.center_id,
                    UserRecord.role == Role.COUNSELOR.value,
                )
                .order_by(UserRecord.name)
            ).all()
            return tuple(self._user(record) for record in records)

    def assign_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        counselor_id: uuid.UUID,
    ) -> None:
        try:
            with self._sessions.session() as session, session.begin():
                member = self._member(
                    session,
                    member_id,
                    actor.center_id,
                    for_update=True,
                )
                counselor = self._role_user(
                    session,
                    counselor_id,
                    Role.COUNSELOR,
                    actor.center_id,
                    for_update=True,
                )
                now = self._now()
                active = session.scalars(
                    select(CounselorAssignmentRecord).where(
                        CounselorAssignmentRecord.member_id == member.id,
                        CounselorAssignmentRecord.ended_at.is_(None),
                    )
                ).all()
                if any(item.counselor_id == counselor.id for item in active):
                    return
                for assignment in active:
                    assignment.ended_at = now
                session.add(
                    CounselorAssignmentRecord(
                        center_id=actor.center_id,
                        member_id=member.id,
                        counselor_id=counselor.id,
                        assigned_by_id=actor.id,
                    )
                )
                self._audit(
                    session,
                    actor_id=actor.id,
                    action="counselor.assigned",
                    subject_id=member.id,
                    center_id=actor.center_id,
                    metadata={"counselor_id": str(counselor.id)},
                )
                session.flush()
                self._reevaluate(session, member.id, actor.id)
        except IntegrityError as error:
            raise ConflictError(
                "The member's counselor assignment changed. Please retry."
            ) from error

    def reassign_member_to_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            member = session.scalar(
                select(UserRecord)
                .where(
                    UserRecord.id == member_id,
                    UserRecord.center_id == actor.center_id,
                )
                .with_for_update()
            )
            if member is None:
                raise NotFoundError("The member was not found in this Center.")
            if member.role != Role.MEMBER.value:
                raise ConflictError("Only an existing member can become a counselor.")
            if normalize_email(confirmation_email) != member.email:
                raise ValidationError(
                    "The confirmation email does not match the member."
                )

            now = self._now()
            active_assignment = self._active_counselor_assignment(session, member.id)
            if active_assignment is not None:
                active_assignment.ended_at = now
            self._close_open_proposals_for_member(
                session,
                member.id,
                actor.id,
                "member_role_changed",
            )
            member.role = Role.COUNSELOR.value
            self._audit(
                session,
                actor_id=actor.id,
                action="identity.role_reassigned",
                subject_id=member.id,
                center_id=actor.center_id,
                metadata={
                    "previous_role": Role.MEMBER.value,
                    "new_role": Role.COUNSELOR.value,
                    "reason_code": reason_code,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="identity.role_reassigned",
                    payload={
                        "user_id": str(member.id),
                        "center_id": str(actor.center_id),
                        "previous_role": Role.MEMBER.value,
                        "new_role": Role.COUNSELOR.value,
                        "reason_code": reason_code,
                    },
                )
            )

    def reassign_counselor_to_member(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            counselor = session.scalar(
                select(UserRecord)
                .where(
                    UserRecord.id == counselor_id,
                    UserRecord.center_id == actor.center_id,
                )
                .with_for_update()
            )
            if counselor is None:
                raise NotFoundError("The counselor was not found in this Center.")
            if counselor.role != Role.COUNSELOR.value:
                raise ConflictError("Only an existing counselor can become a member.")
            if normalize_email(confirmation_email) != counselor.email:
                raise ValidationError(
                    "The confirmation email does not match the counselor."
                )

            active_assignments = session.scalars(
                select(CounselorAssignmentRecord)
                .where(
                    CounselorAssignmentRecord.counselor_id == counselor.id,
                    CounselorAssignmentRecord.ended_at.is_(None),
                )
                .with_for_update()
            ).all()
            if active_assignments:
                raise ConflictError(
                    "Reassign this counselor's active members before changing their role."
                )

            open_reviews = session.scalars(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.status == ProposalStatus.PENDING_REVIEW.value,
                    or_(
                        and_(
                            MatchProposalRecord.counselor_a_id == counselor.id,
                            MatchProposalRecord.counselor_a_decision
                            == CounselorReviewDecision.PENDING.value,
                        ),
                        and_(
                            MatchProposalRecord.counselor_b_id == counselor.id,
                            MatchProposalRecord.counselor_b_decision
                            == CounselorReviewDecision.PENDING.value,
                        ),
                    ),
                )
                .with_for_update()
            ).all()
            if open_reviews:
                raise ConflictError(
                    "Resolve or reassign this counselor's open match reviews "
                    "before changing their role."
                )

            now = self._now()
            screening = session.scalar(
                select(ScreeningCaseRecord)
                .where(ScreeningCaseRecord.member_id == counselor.id)
                .with_for_update()
            )
            if screening is not None:
                screening.expires_at = now
                screening.updated_at = now

            definition = self._active_assessment_definition(session)
            session.add(
                AssessmentAssignmentRecord(
                    member_id=counselor.id,
                    definition_id=definition.id,
                    assigned_at=now,
                    expires_at=now + timedelta(days=90),
                )
            )
            counselor.role = Role.MEMBER.value
            session.flush()
            self._reevaluate(session, counselor.id, actor.id)
            self._audit(
                session,
                actor_id=actor.id,
                action="identity.role_reassigned",
                subject_id=counselor.id,
                center_id=actor.center_id,
                metadata={
                    "previous_role": Role.COUNSELOR.value,
                    "new_role": Role.MEMBER.value,
                    "reason_code": reason_code,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="identity.role_reassigned",
                    payload={
                        "user_id": str(counselor.id),
                        "center_id": str(actor.center_id),
                        "previous_role": Role.COUNSELOR.value,
                        "new_role": Role.MEMBER.value,
                        "reason_code": reason_code,
                    },
                )
            )

    def list_assigned_members(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[OperationsMember]:
        with self._sessions.session() as session, session.begin():
            member_ids = session.scalars(
                select(CounselorAssignmentRecord.member_id).where(
                    CounselorAssignmentRecord.center_id == counselor.center_id,
                    CounselorAssignmentRecord.counselor_id == counselor.id,
                    CounselorAssignmentRecord.ended_at.is_(None),
                )
            ).all()
            members = session.scalars(
                select(UserRecord)
                .where(UserRecord.id.in_(member_ids))
                .order_by(UserRecord.name)
            ).all()
            result = tuple(
                self._operations_member(session, member, counselor.id)
                for member in members
            )
            self._audit(
                session,
                actor_id=counselor.id,
                action="counselor.member_queue_accessed",
                subject_id=counselor.id,
                center_id=counselor.center_id,
                metadata={"record_count": len(result)},
            )
            return result

    def record_counselor_decision(
        self,
        counselor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: CounselorDecisionStatus,
        reason_code: str | None,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            assignment = session.scalar(
                select(CounselorAssignmentRecord).where(
                    CounselorAssignmentRecord.member_id == member_id,
                    CounselorAssignmentRecord.counselor_id == counselor.id,
                    CounselorAssignmentRecord.center_id == counselor.center_id,
                    CounselorAssignmentRecord.ended_at.is_(None),
                )
            )
            if assignment is None:
                raise NotFoundError("No active counselor assignment was found.")
            now = self._now()
            session.add(
                CounselorDecisionRecord(
                    assignment_id=assignment.id,
                    status=status.value,
                    reason_code=reason_code.strip() if reason_code else None,
                    decided_by_id=counselor.id,
                    decided_at=now,
                    expires_at=now + timedelta(days=180),
                )
            )
            self._audit(
                session,
                actor_id=counselor.id,
                action="counselor.decision_recorded",
                subject_id=member_id,
                center_id=counselor.center_id,
                metadata={"status": status.value, "reason_code": reason_code},
            )
            session.flush()
            self._reevaluate(session, member_id, counselor.id)

    def record_screening_status(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: ScreeningStatus,
        provider_event_id: str,
        provider_reference: str,
        reason_code: str | None,
    ) -> bool:
        provider = "manual-pilot"
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id, actor.center_id)
            try:
                with session.begin_nested():
                    session.add(
                        ScreeningEventReceiptRecord(
                            provider=provider,
                            provider_event_id=provider_event_id,
                        )
                    )
                    session.flush()
            except IntegrityError:
                return False
            now = self._now()
            case = session.scalar(
                select(ScreeningCaseRecord).where(
                    ScreeningCaseRecord.member_id == member_id
                )
            )
            if case is None:
                case = ScreeningCaseRecord(
                    member_id=member_id,
                    provider=provider,
                    provider_reference=provider_reference,
                    status=status.value,
                    reason_code=reason_code,
                    requested_at=now,
                    updated_at=now,
                )
                session.add(case)
            else:
                case.provider_reference = provider_reference
                case.status = status.value
                case.reason_code = reason_code
                case.updated_at = now
            case.expires_at = (
                now + timedelta(days=365)
                if status is ScreeningStatus.ELIGIBLE
                else None
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="screening.status_recorded",
                subject_id=member_id,
                center_id=actor.center_id,
                metadata={
                    "provider": provider,
                    "status": status.value,
                    "reason_code": reason_code,
                    "event_id": provider_event_id,
                },
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)
            return True

    def apply_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id, actor.center_id)
            existing = session.scalar(
                select(HoldRecord.id).where(
                    HoldRecord.member_id == member_id,
                    HoldRecord.released_at.is_(None),
                )
            )
            if existing is not None:
                raise ConflictError("This member already has an active hold.")
            session.add(
                HoldRecord(
                    member_id=member_id,
                    center_context_id=actor.center_id,
                    hold_type="administrative",
                    reason_code=reason_code,
                    applied_by_id=actor.id,
                    applied_at=self._now(),
                )
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="hold.applied",
                subject_id=member_id,
                center_id=actor.center_id,
                metadata={"hold_type": "administrative", "reason_code": reason_code},
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)

    def release_hold(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id, actor.center_id)
            hold = session.scalar(
                select(HoldRecord)
                .where(
                    HoldRecord.member_id == member_id,
                    HoldRecord.released_at.is_(None),
                )
                .order_by(HoldRecord.applied_at.desc())
            )
            if hold is None:
                raise NotFoundError("No active hold was found.")
            hold.released_by_id = actor.id
            hold.released_at = self._now()
            self._audit(
                session,
                actor_id=actor.id,
                action="hold.released",
                subject_id=member_id,
                center_id=actor.center_id,
                metadata={"hold_type": hold.hold_type},
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)

    def get_match_preferences(self, member_id: uuid.UUID) -> MatchPreferencesView:
        with self._sessions.session() as session:
            record = session.get(MemberMatchPreferencesRecord, member_id)
            if record is None:
                return MatchPreferencesView(
                    gender=None,
                    min_partner_age=None,
                    max_partner_age=None,
                    completed=False,
                )
            return MatchPreferencesView(
                gender=Gender(record.gender),
                min_partner_age=record.min_partner_age,
                max_partner_age=record.max_partner_age,
                completed=True,
            )

    def save_match_preferences(
        self,
        member_id: uuid.UUID,
        preferences: MatchPreferencesInput,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id)
            record = session.get(MemberMatchPreferencesRecord, member_id)
            now = self._now()
            if record is None:
                session.add(
                    MemberMatchPreferencesRecord(
                        user_id=member_id,
                        gender=preferences.gender.value,
                        min_partner_age=preferences.min_partner_age,
                        max_partner_age=preferences.max_partner_age,
                        completed_at=now,
                    )
                )
            else:
                record.gender = preferences.gender.value
                record.min_partner_age = preferences.min_partner_age
                record.max_partner_age = preferences.max_partner_age
                record.completed_at = now
            self._audit(
                session,
                actor_id=member_id,
                action="matching.preferences_saved",
                subject_id=member_id,
                center_id=self._member_center_id(session, member_id),
                metadata={"gender": preferences.gender.value},
            )
            session.flush()

    def generate_candidates(self, actor: AuthenticatedUser) -> int:
        with self._sessions.session() as session, session.begin():
            community = self._community(session, actor.center_id)
            members = session.scalars(
                select(UserRecord)
                .where(
                    UserRecord.center_id == actor.center_id,
                    UserRecord.role == Role.MEMBER.value,
                )
                .with_for_update()
            ).all()
            evidences: list[CandidateEvidence] = []
            for member in members:
                evidence, _ = self._matching_member_evidence(
                    session,
                    member,
                    actor.id,
                )
                if evidence is not None:
                    evidences.append(evidence)
            existing_pairs, restricted_pairs = self._matching_pair_sets(session)
            scored_pairs: list[tuple[float, CandidateEvidence, CandidateEvidence]] = []
            for index, candidate_a in enumerate(evidences):
                for candidate_b in evidences[index + 1 :]:
                    pair = self._pair_key(candidate_a.member_id, candidate_b.member_id)
                    if self._candidate_pair_exclusion_reasons(
                        candidate_a,
                        candidate_b,
                        pair,
                        existing_pairs,
                        restricted_pairs,
                    ):
                        continue
                    score = self._scorer.score(candidate_a, candidate_b)
                    scored_pairs.append((score.total, candidate_a, candidate_b))

            created = 0
            matched_members: set[uuid.UUID] = set()
            for _, candidate_a, candidate_b in sorted(
                scored_pairs,
                key=lambda item: (
                    -item[0],
                    str(item[1].member_id),
                    str(item[2].member_id),
                ),
            ):
                if (
                    candidate_a.member_id in matched_members
                    or candidate_b.member_id in matched_members
                ):
                    continue
                pair = self._pair_key(candidate_a.member_id, candidate_b.member_id)
                score = self._scorer.score(candidate_a, candidate_b)
                member_a_id, member_b_id = pair
                counselor_a = self._active_counselor_assignment(session, member_a_id)
                counselor_b = self._active_counselor_assignment(session, member_b_id)
                session.add(
                    MatchProposalRecord(
                        center_id=actor.center_id,
                        community_id=community.id,
                        member_a_id=member_a_id,
                        member_b_id=member_b_id,
                        status=ProposalStatus.PENDING_REVIEW.value,
                        score=score.total,
                        score_breakdown=[
                            {
                                "label": item.label,
                                "weight": item.weight,
                                "points": item.points,
                            }
                            for item in score.contributions
                        ],
                        counselor_a_id=(
                            counselor_a.counselor_id
                            if counselor_a is not None
                            else None
                        ),
                        counselor_a_decision=CounselorReviewDecision.PENDING.value,
                        counselor_b_id=(
                            counselor_b.counselor_id
                            if counselor_b is not None
                            else None
                        ),
                        counselor_b_decision=CounselorReviewDecision.PENDING.value,
                    )
                )
                existing_pairs.add(pair)
                matched_members.update(pair)
                created += 1
            if created:
                session.flush()
                self._audit(
                    session,
                    actor_id=actor.id,
                    action="matching.candidates_generated",
                    subject_id=community.id,
                    center_id=actor.center_id,
                    metadata={"created_count": created},
                )
            return created

    def candidate_generation_diagnostics(
        self,
        actor: AuthenticatedUser,
    ) -> CandidateGenerationDiagnostics:
        with self._sessions.session() as session, session.begin():
            members = session.scalars(
                select(UserRecord)
                .where(
                    UserRecord.center_id == actor.center_id,
                    UserRecord.role == Role.MEMBER.value,
                )
                .order_by(UserRecord.name)
            ).all()
            member_diagnostics: list[CandidateMemberDiagnostic] = []
            evidence_by_member: dict[uuid.UUID, CandidateEvidence] = {}
            names: dict[uuid.UUID, str] = {}
            for member in members:
                profile = session.get(MemberProfileRecord, member.id)
                display_name = (
                    profile.display_name if profile is not None else member.name
                )
                names[member.id] = display_name
                evidence, reasons = self._matching_member_evidence(
                    session,
                    member,
                    actor.id,
                    reevaluate=False,
                )
                if evidence is not None:
                    evidence_by_member[member.id] = evidence
                member_diagnostics.append(
                    CandidateMemberDiagnostic(
                        member_id=member.id,
                        display_name=display_name,
                        ready_for_pairing=not reasons,
                        reasons=reasons,
                    )
                )

            existing_pairs, restricted_pairs = self._matching_pair_sets(session)
            evidences = tuple(evidence_by_member.values())
            pair_diagnostics: list[CandidatePairDiagnostic] = []
            for index, candidate_a in enumerate(evidences):
                for candidate_b in evidences[index + 1 :]:
                    pair = self._pair_key(
                        candidate_a.member_id,
                        candidate_b.member_id,
                    )
                    reasons = self._candidate_pair_exclusion_reasons(
                        candidate_a,
                        candidate_b,
                        pair,
                        existing_pairs,
                        restricted_pairs,
                    )
                    pair_diagnostics.append(
                        CandidatePairDiagnostic(
                            member_a_id=candidate_a.member_id,
                            member_a_display_name=names[candidate_a.member_id],
                            member_b_id=candidate_b.member_id,
                            member_b_display_name=names[candidate_b.member_id],
                            eligible=not reasons,
                            reasons=reasons,
                        )
                    )

            diagnostics = CandidateGenerationDiagnostics(
                total_members=len(members),
                ready_members=len(evidences),
                evaluated_pairs=len(pair_diagnostics),
                eligible_pairs=sum(item.eligible for item in pair_diagnostics),
                members=tuple(member_diagnostics),
                pairs=tuple(pair_diagnostics),
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="matching.diagnostics_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={
                    "total_members": diagnostics.total_members,
                    "ready_members": diagnostics.ready_members,
                    "evaluated_pairs": diagnostics.evaluated_pairs,
                    "eligible_pairs": diagnostics.eligible_pairs,
                },
            )
            return diagnostics

    def _matching_member_evidence(
        self,
        session: Session,
        member: UserRecord,
        actor_id: uuid.UUID,
        *,
        reevaluate: bool = True,
    ) -> tuple[CandidateEvidence | None, tuple[str, ...]]:
        reasons: list[str] = []
        readiness = (
            self._reevaluate(
                session,
                member.id,
                actor_id,
                only_if_changed=True,
            )
            if reevaluate
            else self._evaluator.evaluate(
                self._evidence(
                    session,
                    member.id,
                    ensure_assessment=False,
                )
            )
        )
        if not readiness.eligible:
            reasons.extend(readiness.explanations)
        preferences = session.get(MemberMatchPreferencesRecord, member.id)
        if preferences is None:
            reasons.append(
                "Complete matching preferences, including gender and partner age range."
            )
        profile = session.get(MemberProfileRecord, member.id)
        if profile is None and readiness.eligible:
            reasons.append("Complete the member profile.")
        if self._has_open_proposal(session, member.id):
            reasons.append(
                "An existing candidate, introduction, or matched pair is still open."
            )
        if reasons or preferences is None or profile is None:
            return None, tuple(reasons)
        return (
            CandidateEvidence(
                member_id=member.id,
                gender=Gender(preferences.gender),
                age=self._age_on(profile.birth_date, self._now().date()),
                min_partner_age=preferences.min_partner_age,
                max_partner_age=preferences.max_partner_age,
                city=profile.city,
                state=profile.state,
                denomination=profile.denomination,
                relationship_intent=profile.relationship_intent,
            ),
            (),
        )

    def _matching_pair_sets(
        self,
        session: Session,
    ) -> tuple[set[tuple[uuid.UUID, uuid.UUID]], set[tuple[uuid.UUID, uuid.UUID]]]:
        existing_pairs = {
            self._pair_key(row[0], row[1])
            for row in session.execute(
                select(
                    MatchProposalRecord.member_a_id,
                    MatchProposalRecord.member_b_id,
                )
            )
        }
        restricted_pairs = {
            self._pair_key(row[0], row[1])
            for row in session.execute(
                select(MemberBlockRecord.blocker_id, MemberBlockRecord.blocked_id)
            )
        }
        restricted_pairs |= {
            self._pair_key(row[0], row[1])
            for row in session.execute(
                select(
                    MemberReportRecord.reporter_id,
                    MemberReportRecord.reported_id,
                )
            )
        }
        return existing_pairs, restricted_pairs

    def _candidate_pair_exclusion_reasons(
        self,
        candidate_a: CandidateEvidence,
        candidate_b: CandidateEvidence,
        pair: tuple[uuid.UUID, uuid.UUID],
        existing_pairs: set[tuple[uuid.UUID, uuid.UUID]],
        restricted_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if {candidate_a.gender, candidate_b.gender} != {
            Gender.MAN,
            Gender.WOMAN,
        }:
            reasons.append("The pilot currently generates only Man/Woman pairs.")
        elif not self._scorer.is_reciprocally_compatible(candidate_a, candidate_b):
            reasons.append("Their partner age preferences are not reciprocal.")
        if pair in restricted_pairs:
            reasons.append("A safety restriction prevents this pair from matching.")
        if pair in existing_pairs:
            reasons.append(
                "This pair already has proposal history and cannot be generated again."
            )
        return tuple(reasons)

    def candidate_queue(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CandidateReviewItem]:
        with self._sessions.session() as session, session.begin():
            rows = session.scalars(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.center_id == counselor.center_id,
                    MatchProposalRecord.status == ProposalStatus.PENDING_REVIEW.value,
                    or_(
                        and_(
                            MatchProposalRecord.counselor_a_id == counselor.id,
                            MatchProposalRecord.counselor_a_decision
                            == CounselorReviewDecision.PENDING.value,
                        ),
                        and_(
                            MatchProposalRecord.counselor_b_id == counselor.id,
                            MatchProposalRecord.counselor_b_decision
                            == CounselorReviewDecision.PENDING.value,
                        ),
                    ),
                )
                .order_by(
                    MatchProposalRecord.score.desc(),
                    MatchProposalRecord.created_at,
                )
            ).all()
            items = tuple(
                self._candidate_review_item(session, row, counselor.id) for row in rows
            )
            self._audit(
                session,
                actor_id=counselor.id,
                action="matching.queue_accessed",
                subject_id=counselor.id,
                center_id=counselor.center_id,
                metadata={"record_count": len(items)},
            )
            return items

    def review_candidate(
        self,
        counselor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        decision: CounselorReviewDecision,
        reason_code: str | None,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._role_user(
                session,
                counselor.id,
                Role.COUNSELOR,
                counselor.center_id,
                for_update=True,
            )
            proposal = session.scalar(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.id == proposal_id,
                    MatchProposalRecord.center_id == counselor.center_id,
                )
                .with_for_update()
            )
            if proposal is None:
                raise NotFoundError("Candidate proposal was not found.")
            if proposal.status != ProposalStatus.PENDING_REVIEW.value:
                raise ConflictError("This candidate is no longer pending review.")
            is_counselor_a = proposal.counselor_a_id == counselor.id
            is_counselor_b = proposal.counselor_b_id == counselor.id
            if not is_counselor_a and not is_counselor_b:
                raise NotFoundError(
                    "You are not the assigned counselor for either member."
                )
            now = self._now()
            if is_counselor_a:
                if (
                    proposal.counselor_a_decision
                    != CounselorReviewDecision.PENDING.value
                ):
                    raise ConflictError("You already reviewed this candidate.")
                proposal.counselor_a_decision = decision.value
                proposal.counselor_a_decided_at = now
                proposal.counselor_a_reason_code = reason_code
            if is_counselor_b:
                if (
                    proposal.counselor_b_decision
                    != CounselorReviewDecision.PENDING.value
                ):
                    raise ConflictError("You already reviewed this candidate.")
                proposal.counselor_b_decision = decision.value
                proposal.counselor_b_decided_at = now
                proposal.counselor_b_reason_code = reason_code

            self._audit(
                session,
                actor_id=counselor.id,
                action="matching.candidate_reviewed",
                subject_id=proposal.id,
                center_id=counselor.center_id,
                metadata={"decision": decision.value, "reason_code": reason_code},
            )

            both_approved = (
                proposal.counselor_a_decision == CounselorReviewDecision.APPROVED.value
                and proposal.counselor_b_decision
                == CounselorReviewDecision.APPROVED.value
            )
            if decision is CounselorReviewDecision.DECLINED:
                proposal.status = ProposalStatus.CLOSED.value
                proposal.closed_at = now
                proposal.closed_reason = "counselor_declined"
                self._close_proposal_audit(
                    session, counselor.id, proposal, "counselor_declined"
                )
            elif both_approved:
                if self._has_other_open_proposal(
                    session, proposal.member_a_id, proposal.id
                ) or self._has_other_open_proposal(
                    session, proposal.member_b_id, proposal.id
                ):
                    raise ConflictError(
                        "A member in this candidate already has an active match process."
                    )
                proposal.status = ProposalStatus.INTRODUCED.value
                proposal.introduced_at = now
                self._audit(
                    session,
                    actor_id=counselor.id,
                    action="matching.introduced",
                    subject_id=proposal.id,
                    center_id=counselor.center_id,
                    metadata={},
                )
                session.add(
                    OutboxMessageRecord(
                        event_type="matching.introduced",
                        payload={"proposal_id": str(proposal.id)},
                    )
                )
            session.flush()

    def get_introduction(
        self,
        member_id: uuid.UUID,
    ) -> IntroductionView | None:
        with self._sessions.session() as session:
            proposal = session.scalar(
                select(MatchProposalRecord).where(
                    MatchProposalRecord.status == ProposalStatus.INTRODUCED.value,
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
            )
            if proposal is None:
                return None
            return self._introduction_view(session, proposal, member_id)

    def respond_to_introduction(
        self,
        member_id: uuid.UUID,
        proposal_id: uuid.UUID,
        decision: MemberResponseDecision,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            proposal = session.scalar(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.id == proposal_id,
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
                .with_for_update()
            )
            if proposal is None:
                raise NotFoundError("Introduction was not found.")
            if proposal.status != ProposalStatus.INTRODUCED.value:
                raise ConflictError(
                    "This introduction is no longer awaiting a response."
                )
            now = self._now()
            if proposal.member_a_id == member_id:
                if proposal.member_a_response is not None:
                    raise ConflictError("You already responded to this introduction.")
                proposal.member_a_response = decision.value
                proposal.member_a_responded_at = now
                other_response = proposal.member_b_response
            else:
                if proposal.member_b_response is not None:
                    raise ConflictError("You already responded to this introduction.")
                proposal.member_b_response = decision.value
                proposal.member_b_responded_at = now
                other_response = proposal.member_a_response

            self._audit(
                session,
                actor_id=member_id,
                action="matching.member_responded",
                subject_id=proposal.id,
                center_id=proposal.center_id,
                metadata={"decision": decision.value},
            )

            if (
                decision is MemberResponseDecision.DECLINED
                or other_response == MemberResponseDecision.DECLINED.value
            ):
                proposal.status = ProposalStatus.CLOSED.value
                proposal.closed_at = now
                proposal.closed_reason = "member_declined"
                self._close_proposal_audit(
                    session, member_id, proposal, "member_declined"
                )
            elif other_response == MemberResponseDecision.ACCEPTED.value:
                if self._has_other_open_proposal(
                    session, proposal.member_a_id, proposal.id
                ) or self._has_other_open_proposal(
                    session, proposal.member_b_id, proposal.id
                ):
                    raise ConflictError(
                        "A member in this introduction already has an active match."
                    )
                proposal.status = ProposalStatus.ACTIVE.value
                proposal.activated_at = now
                self._audit(
                    session,
                    actor_id=member_id,
                    action="matching.activated",
                    subject_id=proposal.id,
                    center_id=proposal.center_id,
                    metadata={},
                )
                session.add(
                    OutboxMessageRecord(
                        event_type="matching.activated",
                        payload={"proposal_id": str(proposal.id)},
                    )
                )
            session.flush()

    def get_matched_pair(
        self,
        member_id: uuid.UUID,
    ) -> MatchedPairView | None:
        with self._sessions.session() as session:
            proposal = session.scalar(
                select(MatchProposalRecord).where(
                    MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
            )
            if proposal is None or proposal.activated_at is None:
                return None
            partner_id = (
                proposal.member_b_id
                if proposal.member_a_id == member_id
                else proposal.member_a_id
            )
            return MatchedPairView(
                proposal_id=proposal.id,
                partner_id=partner_id,
                partner_display_name=self._display_name(session, partner_id),
                activated_at=proposal.activated_at,
            )

    def get_recent_match(
        self,
        member_id: uuid.UUID,
    ) -> IntroductionView | None:
        with self._sessions.session() as session:
            proposal = session.scalar(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.introduced_at.is_not(None),
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
                .order_by(MatchProposalRecord.created_at.desc())
            )
            if proposal is None:
                return None
            return self._introduction_view(session, proposal, member_id)

    def list_recent_messages(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
        limit: int,
    ) -> Sequence[MessageView]:
        with self._sessions.session() as session, session.begin():
            proposal = self._active_participant_proposal(
                session, member, proposal_id, lock=True
            )
            records = list(
                session.scalars(
                    select(MatchedPairMessageRecord)
                    .where(
                        MatchedPairMessageRecord.proposal_id == proposal.id,
                        MatchedPairMessageRecord.center_id == member.center_id,
                    )
                    .order_by(
                        MatchedPairMessageRecord.sent_at.desc(),
                        MatchedPairMessageRecord.id.desc(),
                    )
                    .limit(limit)
                )
            )
            records.reverse()
            names = {
                proposal.member_a_id: self._display_name(session, proposal.member_a_id),
                proposal.member_b_id: self._display_name(session, proposal.member_b_id),
            }
            return [
                MessageView(
                    id=record.id,
                    proposal_id=record.proposal_id,
                    sender_id=record.sender_id,
                    sender_display_name=names[record.sender_id],
                    body=record.body,
                    sent_at=record.sent_at,
                    is_mine=record.sender_id == member.id,
                )
                for record in records
            ]

    def send_message(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
        body: str,
    ) -> MessageView:
        with self._sessions.session() as session, session.begin():
            proposal = self._active_participant_proposal(
                session, member, proposal_id, lock=True
            )
            record = MatchedPairMessageRecord(
                center_id=member.center_id,
                proposal_id=proposal.id,
                sender_id=member.id,
                body=body,
                sent_at=self._now(),
            )
            session.add(record)
            session.flush()
            self._audit(
                session,
                actor_id=member.id,
                action="messaging.sent",
                subject_id=record.id,
                center_id=member.center_id,
                metadata={
                    "proposal_id": str(proposal.id),
                    "sender_id": str(member.id),
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="messaging.sent",
                    payload={
                        "message_id": str(record.id),
                        "proposal_id": str(proposal.id),
                        "sender_id": str(member.id),
                    },
                )
            )
            return MessageView(
                id=record.id,
                proposal_id=proposal.id,
                sender_id=member.id,
                sender_display_name=self._display_name(session, member.id),
                body=record.body,
                sent_at=record.sent_at,
                is_mine=True,
            )

    def list_conversation_statuses(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CounselorConversationStatus]:
        with self._sessions.session() as session, session.begin():
            proposals = session.scalars(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.center_id == counselor.center_id,
                    MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
                    or_(
                        MatchProposalRecord.counselor_a_id == counselor.id,
                        MatchProposalRecord.counselor_b_id == counselor.id,
                    ),
                )
                .order_by(MatchProposalRecord.activated_at.desc())
                .with_for_update()
            ).all()
            statuses: list[CounselorConversationStatus] = []
            for proposal in proposals:
                message_count, latest_activity_at = session.execute(
                    select(
                        func.count(MatchedPairMessageRecord.id),
                        func.max(MatchedPairMessageRecord.sent_at),
                    ).where(
                        MatchedPairMessageRecord.proposal_id == proposal.id,
                        MatchedPairMessageRecord.center_id == counselor.center_id,
                    )
                ).one()
                statuses.append(
                    CounselorConversationStatus(
                        proposal_id=proposal.id,
                        member_a_display_name=self._display_name(
                            session, proposal.member_a_id
                        ),
                        member_b_display_name=self._display_name(
                            session, proposal.member_b_id
                        ),
                        message_count=message_count,
                        latest_activity_at=latest_activity_at,
                    )
                )
            return statuses

    def block_member(
        self,
        actor: AuthenticatedUser,
        block: BlockInput,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            blocked_member = self._member(
                session, block.blocked_member_id, actor.center_id
            )
            proposal = self._existing_proposal(session, actor.id, blocked_member.id)
            if proposal is None:
                raise NotFoundError(
                    "You can only block a member you were introduced to."
                )
            existing = session.scalar(
                select(MemberBlockRecord.id).where(
                    MemberBlockRecord.blocker_id == actor.id,
                    MemberBlockRecord.blocked_id == blocked_member.id,
                )
            )
            if existing is None:
                session.add(
                    MemberBlockRecord(
                        center_id=actor.center_id,
                        blocker_id=actor.id,
                        blocked_id=blocked_member.id,
                        category=block.category.value,
                        context=block.context,
                    )
                )
            self._audit(
                session,
                actor_id=actor.id,
                action="safety.block_created",
                subject_id=blocked_member.id,
                center_id=actor.center_id,
                metadata={
                    "category": block.category.value,
                    "context_provided": bool(block.context),
                },
            )
            if proposal.status in _OPEN_PROPOSAL_STATUSES:
                proposal.status = ProposalStatus.CLOSED.value
                proposal.closed_at = self._now()
                proposal.closed_reason = "member_block"
                self._close_proposal_audit(session, actor.id, proposal, "member_block")
            session.flush()

    def report_member(
        self,
        actor: AuthenticatedUser,
        report: ReportInput,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            reported_member = self._member(
                session, report.reported_member_id, actor.center_id
            )
            proposal = self._existing_proposal(session, actor.id, reported_member.id)
            if proposal is None:
                raise NotFoundError(
                    "You can only report a member you were introduced to."
                )
            session.add(
                MemberReportRecord(
                    center_id=actor.center_id,
                    reporter_id=actor.id,
                    reported_id=reported_member.id,
                    category=report.category.value,
                    context=report.context,
                )
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="safety.report_created",
                subject_id=reported_member.id,
                center_id=actor.center_id,
                metadata={
                    "category": report.category.value,
                    "context_length": len(report.context),
                },
            )
            if proposal.status in _OPEN_PROPOSAL_STATUSES:
                proposal.status = ProposalStatus.CLOSED.value
                proposal.closed_at = self._now()
                proposal.closed_reason = "member_report"
                self._close_proposal_audit(session, actor.id, proposal, "member_report")
            session.flush()

    def _candidate_review_item(
        self,
        session: Session,
        proposal: MatchProposalRecord,
        counselor_id: uuid.UUID,
    ) -> CandidateReviewItem:
        if proposal.counselor_a_id == counselor_id:
            member_id, partner_id = proposal.member_a_id, proposal.member_b_id
            my_decision = CounselorReviewDecision(proposal.counselor_a_decision)
            partner_decision = CounselorReviewDecision(proposal.counselor_b_decision)
        else:
            member_id, partner_id = proposal.member_b_id, proposal.member_a_id
            my_decision = CounselorReviewDecision(proposal.counselor_b_decision)
            partner_decision = CounselorReviewDecision(proposal.counselor_a_decision)
        return CandidateReviewItem(
            proposal_id=proposal.id,
            member_id=member_id,
            member_display_name=self._display_name(session, member_id),
            partner_id=partner_id,
            partner_display_name=self._display_name(session, partner_id),
            score=proposal.score,
            explanations=self._explanations(proposal.score_breakdown),
            my_decision=my_decision,
            partner_counselor_decision=partner_decision,
            created_at=proposal.created_at,
        )

    def _introduction_view(
        self,
        session: Session,
        proposal: MatchProposalRecord,
        member_id: uuid.UUID,
    ) -> IntroductionView:
        if proposal.member_a_id == member_id:
            partner_id = proposal.member_b_id
            my_response = proposal.member_a_response
        else:
            partner_id = proposal.member_a_id
            my_response = proposal.member_b_response
        partner_profile = session.get(MemberProfileRecord, partner_id)
        return IntroductionView(
            proposal_id=proposal.id,
            partner_id=partner_id,
            partner_display_name=self._display_name(session, partner_id),
            partner_city=partner_profile.city if partner_profile is not None else "",
            partner_state=partner_profile.state if partner_profile is not None else "",
            partner_denomination=(
                partner_profile.denomination if partner_profile is not None else ""
            ),
            partner_relationship_intent=(
                partner_profile.relationship_intent
                if partner_profile is not None
                else ""
            ),
            explanations=self._explanations(proposal.score_breakdown),
            my_response=(
                MemberResponseDecision(my_response) if my_response is not None else None
            ),
            status=ProposalStatus(proposal.status),
        )

    def _existing_proposal(
        self,
        session: Session,
        member_id: uuid.UUID,
        other_member_id: uuid.UUID,
    ) -> MatchProposalRecord | None:
        member_a_id, member_b_id = self._pair_key(member_id, other_member_id)
        return session.scalar(
            select(MatchProposalRecord).where(
                MatchProposalRecord.member_a_id == member_a_id,
                MatchProposalRecord.member_b_id == member_b_id,
                MatchProposalRecord.introduced_at.is_not(None),
            )
        )

    def _has_open_proposal(self, session: Session, member_id: uuid.UUID) -> bool:
        return (
            session.scalar(
                select(MatchProposalRecord.id).where(
                    MatchProposalRecord.status.in_(_OPEN_PROPOSAL_STATUSES),
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
            )
            is not None
        )

    @staticmethod
    def _has_other_open_proposal(
        session: Session,
        member_id: uuid.UUID,
        proposal_id: uuid.UUID,
    ) -> bool:
        return (
            session.scalar(
                select(MatchProposalRecord.id).where(
                    MatchProposalRecord.id != proposal_id,
                    MatchProposalRecord.status.in_(_OPEN_PROPOSAL_STATUSES),
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
            )
            is not None
        )

    def _close_open_proposals_for_member(
        self,
        session: Session,
        member_id: uuid.UUID,
        actor_id: uuid.UUID,
        reason: str,
    ) -> None:
        proposals = session.scalars(
            select(MatchProposalRecord).where(
                MatchProposalRecord.status.in_(_OPEN_PROPOSAL_STATUSES),
                or_(
                    MatchProposalRecord.member_a_id == member_id,
                    MatchProposalRecord.member_b_id == member_id,
                ),
            )
        ).all()
        now = self._now()
        for proposal in proposals:
            proposal.status = ProposalStatus.CLOSED.value
            proposal.closed_at = now
            proposal.closed_reason = reason
            self._close_proposal_audit(session, actor_id, proposal, reason)

    def _close_proposal_audit(
        self,
        session: Session,
        actor_id: uuid.UUID,
        proposal: MatchProposalRecord,
        reason: str,
    ) -> None:
        self._audit(
            session,
            actor_id=actor_id,
            action="matching.closed",
            subject_id=proposal.id,
            center_id=proposal.center_id,
            metadata={"reason": reason},
        )
        session.add(
            OutboxMessageRecord(
                event_type="matching.closed",
                payload={"proposal_id": str(proposal.id), "reason": reason},
            )
        )

    @staticmethod
    def _pair_key(
        a: uuid.UUID,
        b: uuid.UUID,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        return (a, b) if str(a) < str(b) else (b, a)

    @staticmethod
    def _display_name(session: Session, member_id: uuid.UUID) -> str:
        profile = session.get(MemberProfileRecord, member_id)
        if profile is not None:
            return profile.display_name
        member = session.get(UserRecord, member_id)
        return member.name if member is not None else "Member"

    @staticmethod
    def _explanations(score_breakdown: list[dict[str, Any]]) -> tuple[str, ...]:
        return tuple(
            f"{item['label']}: {round(item['points'])} of {round(item['weight'])} "
            "points"
            for item in score_breakdown
        )

    def _reevaluate(
        self,
        session: Session,
        member_id: uuid.UUID,
        actor_id: uuid.UUID,
        *,
        only_if_changed: bool = False,
    ) -> ReadinessResult:
        member = self._member(session, member_id)
        community = self._community(session, member.center_id)
        evidence = self._evidence(session, member_id)
        result = self._evaluator.evaluate(evidence)
        if not result.eligible:
            self._close_open_proposals_for_member(
                session,
                member_id,
                actor_id,
                "hold_applied" if evidence.active_hold else "readiness_lost",
            )
        previous = session.scalar(
            select(ReadinessDecisionRecord)
            .where(ReadinessDecisionRecord.member_id == member_id)
            .order_by(ReadinessDecisionRecord.evaluated_at.desc())
        )
        now = self._now()
        evidence_versions = self._evidence_versions(session, member_id)
        unmet_requirements = [item.value for item in result.unmet_requirements]
        if (
            only_if_changed
            and previous is not None
            and previous.eligible == result.eligible
            and previous.unmet_requirements == unmet_requirements
            and previous.evidence_versions == evidence_versions
            and previous.configuration_version == READINESS_CONFIGURATION_VERSION
        ):
            return result
        session.add(
            ReadinessDecisionRecord(
                member_id=member_id,
                community_id=community.id,
                eligible=result.eligible,
                unmet_requirements=unmet_requirements,
                evidence_versions=evidence_versions,
                configuration_version=READINESS_CONFIGURATION_VERSION,
                evaluated_at=now,
            )
        )
        self._audit(
            session,
            actor_id=actor_id,
            action="readiness.evaluated",
            subject_id=member_id,
            center_id=member.center_id,
            metadata={
                "eligible": result.eligible,
                "unmet_requirements": [
                    item.value for item in result.unmet_requirements
                ],
                "configuration_version": READINESS_CONFIGURATION_VERSION,
            },
        )
        if previous is None or previous.eligible != result.eligible:
            session.add(
                OutboxMessageRecord(
                    event_type="readiness.eligibility_changed",
                    payload={
                        "member_id": str(member_id),
                        "community_id": str(community.id),
                        "eligible": result.eligible,
                    },
                )
            )
        return result

    def _evidence(
        self,
        session: Session,
        member_id: uuid.UUID,
        *,
        ensure_assessment: bool = True,
    ) -> ReadinessEvidence:
        now = self._now()
        profile = session.get(MemberProfileRecord, member_id)
        adult_and_faith = (
            profile is not None
            and self._age_on(profile.birth_date, now.date()) >= 18
            and profile.faith_affirmed
        )
        profile_complete = profile is not None and all(
            (
                profile.display_name.strip(),
                profile.relationship_intent.strip(),
                profile.city.strip(),
                profile.state.strip(),
            )
        )
        consent = self._active_consent(session)
        consent_complete = (
            session.scalar(
                select(ConsentAcceptanceRecord.id).where(
                    ConsentAcceptanceRecord.user_id == member_id,
                    ConsentAcceptanceRecord.consent_version_id == consent.id,
                )
            )
            is not None
        )
        assignment: AssessmentAssignmentRecord | None
        if ensure_assessment:
            assignment, _ = self._ensure_current_assessment(session, member_id)
        else:
            definition = self._active_assessment_definition(session)
            assignment = session.scalar(
                select(AssessmentAssignmentRecord)
                .where(
                    AssessmentAssignmentRecord.member_id == member_id,
                    AssessmentAssignmentRecord.definition_id == definition.id,
                )
                .order_by(AssessmentAssignmentRecord.assigned_at.desc())
            )
        assessment_complete = (
            assignment is not None
            and assignment.completed_at is not None
            and not self._is_expired(assignment.expires_at)
        )
        return ReadinessEvidence(
            adult_and_faith_complete=adult_and_faith,
            consent_complete=consent_complete,
            profile_complete=profile_complete,
            assessment_complete=assessment_complete,
            counselor_approved=(
                self._counselor_status(session, member_id)
                is CounselorDecisionStatus.APPROVED
            ),
            screening_eligible=(
                self._screening_status(session, member_id) is ScreeningStatus.ELIGIBLE
            ),
            active_hold=(
                session.scalar(
                    select(HoldRecord.id).where(
                        HoldRecord.member_id == member_id,
                        HoldRecord.released_at.is_(None),
                    )
                )
                is not None
            ),
        )

    def _evidence_versions(
        self,
        session: Session,
        member_id: uuid.UUID,
    ) -> dict[str, str]:
        consent = self._active_consent(session)
        acceptance_id = session.scalar(
            select(ConsentAcceptanceRecord.id).where(
                ConsentAcceptanceRecord.user_id == member_id,
                ConsentAcceptanceRecord.consent_version_id == consent.id,
            )
        )
        profile = session.get(MemberProfileRecord, member_id)
        assignment, assessment = self._ensure_current_assessment(session, member_id)
        counselor_assignment = self._active_counselor_assignment(session, member_id)
        counselor_decision = (
            session.scalar(
                select(CounselorDecisionRecord)
                .where(CounselorDecisionRecord.assignment_id == counselor_assignment.id)
                .order_by(CounselorDecisionRecord.decided_at.desc())
            )
            if counselor_assignment is not None
            else None
        )
        screening = session.scalar(
            select(ScreeningCaseRecord).where(
                ScreeningCaseRecord.member_id == member_id
            )
        )
        hold = session.scalar(
            select(HoldRecord)
            .where(
                HoldRecord.member_id == member_id,
                HoldRecord.released_at.is_(None),
            )
            .order_by(HoldRecord.applied_at.desc())
        )
        return {
            "consent_version": consent.version,
            "consent_acceptance_id": str(acceptance_id or "none"),
            "profile_completed_at": (
                profile.completed_at.isoformat() if profile is not None else "none"
            ),
            "assessment_version": assessment.version,
            "assessment_assignment_id": str(assignment.id),
            "counselor_assignment_id": str(
                counselor_assignment.id if counselor_assignment else "none"
            ),
            "counselor_decision_id": str(
                counselor_decision.id if counselor_decision else "none"
            ),
            "screening_case_id": str(screening.id if screening else "none"),
            "screening_updated_at": (
                screening.updated_at.isoformat() if screening else "none"
            ),
            "active_hold_id": str(hold.id if hold else "none"),
        }

    def _operations_member(
        self,
        session: Session,
        member: UserRecord,
        actor_id: uuid.UUID,
    ) -> OperationsMember:
        assignment = self._active_counselor_assignment(session, member.id)
        profile = session.get(MemberProfileRecord, member.id)
        readiness = self._reevaluate(
            session,
            member.id,
            actor_id,
            only_if_changed=True,
        )
        hold_active = (
            session.scalar(
                select(HoldRecord.id).where(
                    HoldRecord.member_id == member.id,
                    HoldRecord.released_at.is_(None),
                )
            )
            is not None
        )
        return OperationsMember(
            id=member.id,
            email=member.email,
            display_name=profile.display_name if profile is not None else member.name,
            center_id=member.center_id,
            counselor_id=assignment.counselor_id if assignment is not None else None,
            counselor_status=self._counselor_status(session, member.id),
            screening_status=self._screening_status(session, member.id),
            hold_active=hold_active,
            readiness=readiness,
        )

    def _counselor_status(
        self,
        session: Session,
        member_id: uuid.UUID,
    ) -> CounselorDecisionStatus:
        assignment = self._active_counselor_assignment(session, member_id)
        if assignment is None:
            return CounselorDecisionStatus.PENDING
        decision = session.scalar(
            select(CounselorDecisionRecord)
            .where(CounselorDecisionRecord.assignment_id == assignment.id)
            .order_by(CounselorDecisionRecord.decided_at.desc())
        )
        if decision is None:
            return CounselorDecisionStatus.PENDING
        if self._is_expired(decision.expires_at):
            return CounselorDecisionStatus.PENDING
        return CounselorDecisionStatus(decision.status)

    def _screening_status(
        self,
        session: Session,
        member_id: uuid.UUID,
    ) -> ScreeningStatus:
        case = session.scalar(
            select(ScreeningCaseRecord).where(
                ScreeningCaseRecord.member_id == member_id
            )
        )
        if case is None:
            return ScreeningStatus.NOT_STARTED
        if self._is_expired(case.expires_at):
            return ScreeningStatus.NOT_STARTED
        return ScreeningStatus(case.status)

    @staticmethod
    def _active_counselor_assignment(
        session: Session,
        member_id: uuid.UUID,
    ) -> CounselorAssignmentRecord | None:
        return session.scalar(
            select(CounselorAssignmentRecord)
            .where(
                CounselorAssignmentRecord.member_id == member_id,
                CounselorAssignmentRecord.ended_at.is_(None),
            )
            .order_by(CounselorAssignmentRecord.assigned_at.desc())
        )

    @staticmethod
    def _audit(
        session: Session,
        *,
        actor_id: uuid.UUID,
        action: str,
        subject_id: uuid.UUID,
        center_id: uuid.UUID | None,
        metadata: dict[str, object],
    ) -> None:
        session.add(
            AuditEventRecord(
                actor_id=str(actor_id),
                action=action,
                subject_id=str(subject_id),
                center_id=center_id,
                correlation_id=uuid.uuid4(),
                safe_metadata=metadata,
            )
        )

    @staticmethod
    def _active_participant_proposal(
        session: Session,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> MatchProposalRecord:
        statement = select(MatchProposalRecord).where(
            MatchProposalRecord.id == proposal_id,
            MatchProposalRecord.center_id == member.center_id,
            or_(
                MatchProposalRecord.member_a_id == member.id,
                MatchProposalRecord.member_b_id == member.id,
            ),
        )
        if lock:
            statement = statement.with_for_update()
        proposal = session.scalar(statement)
        if proposal is None:
            raise NotFoundError("Matched-pair conversation was not found.")
        if proposal.status != ProposalStatus.ACTIVE.value:
            raise ConflictError("This matched-pair conversation is no longer active.")
        return proposal

    @staticmethod
    def _user(record: UserRecord) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=record.id,
            email=record.email,
            name=record.name,
            role=Role(record.role),
            center_id=record.center_id,
        )

    @staticmethod
    def _invitation(record: InvitationRecord) -> InvitationView:
        return InvitationView(
            id=record.id,
            email=record.email,
            role=Role(record.role),
            expires_at=record.expires_at,
            accepted_at=record.accepted_at,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _age_on(birth_date: date, on_date: date) -> int:
        return (
            on_date.year
            - birth_date.year
            - ((on_date.month, on_date.day) < (birth_date.month, birth_date.day))
        )

    @classmethod
    def _is_expired(cls, value: datetime | None) -> bool:
        if value is None:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value <= cls._now()

    @staticmethod
    def _pilot_center(session: Session) -> CenterRecord:
        center = session.scalar(
            select(CenterRecord).where(CenterRecord.slug == PILOT_CENTER_SLUG)
        )
        if center is None:
            raise NotFoundError("The pilot Center has not been configured.")
        return center

    @staticmethod
    def _community(session: Session, center_id: uuid.UUID) -> CommunityRecord:
        community = session.scalar(
            select(CommunityRecord).where(CommunityRecord.center_id == center_id)
        )
        if community is None:
            raise NotFoundError("The pilot community has not been configured.")
        return community

    @staticmethod
    def _active_consent(session: Session) -> ConsentVersionRecord:
        consent = session.scalar(
            select(ConsentVersionRecord)
            .where(ConsentVersionRecord.is_active.is_(True))
            .order_by(ConsentVersionRecord.effective_at.desc())
        )
        if consent is None:
            raise NotFoundError("No active consent version is configured.")
        return consent

    @staticmethod
    def _active_assessment_definition(
        session: Session,
    ) -> AssessmentDefinitionRecord:
        definition = session.scalar(
            select(AssessmentDefinitionRecord)
            .where(AssessmentDefinitionRecord.is_active.is_(True))
            .order_by(AssessmentDefinitionRecord.version.desc())
        )
        if definition is None:
            raise NotFoundError("No active readiness assessment is configured.")
        return definition

    def _ensure_current_assessment(
        self,
        session: Session,
        member_id: uuid.UUID,
    ) -> tuple[AssessmentAssignmentRecord, AssessmentDefinitionRecord]:
        definition = self._active_assessment_definition(session)
        row = session.execute(
            select(AssessmentAssignmentRecord, AssessmentDefinitionRecord)
            .join(
                AssessmentDefinitionRecord,
                AssessmentDefinitionRecord.id
                == AssessmentAssignmentRecord.definition_id,
            )
            .where(
                AssessmentAssignmentRecord.member_id == member_id,
                AssessmentAssignmentRecord.definition_id == definition.id,
            )
            .order_by(AssessmentAssignmentRecord.assigned_at.desc())
        ).first()
        if row is not None and not self._is_expired(row[0].expires_at):
            return row._tuple()
        now = self._now()
        assignment = AssessmentAssignmentRecord(
            member_id=member_id,
            definition_id=definition.id,
            assigned_at=now,
            expires_at=now + timedelta(days=90),
        )
        session.add(assignment)
        session.flush()
        return assignment, definition

    @staticmethod
    def _member_assessment(
        session: Session,
        member_id: uuid.UUID,
    ) -> tuple[AssessmentAssignmentRecord, AssessmentDefinitionRecord]:
        row = session.execute(
            select(AssessmentAssignmentRecord, AssessmentDefinitionRecord)
            .join(
                AssessmentDefinitionRecord,
                AssessmentDefinitionRecord.id
                == AssessmentAssignmentRecord.definition_id,
            )
            .where(AssessmentAssignmentRecord.member_id == member_id)
            .order_by(AssessmentAssignmentRecord.assigned_at.desc())
        ).first()
        if row is None:
            raise NotFoundError("No readiness assessment is assigned.")
        return row._tuple()

    @staticmethod
    def _member(
        session: Session,
        member_id: uuid.UUID,
        center_id: uuid.UUID | None = None,
        *,
        for_update: bool = False,
    ) -> UserRecord:
        conditions = [
            UserRecord.id == member_id,
            UserRecord.role == Role.MEMBER.value,
        ]
        if center_id is not None:
            conditions.append(UserRecord.center_id == center_id)
        statement = select(UserRecord).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        member = session.scalar(statement)
        if member is None:
            raise NotFoundError("Member was not found in the authorized Center.")
        return member

    @staticmethod
    def _role_user(
        session: Session,
        user_id: uuid.UUID,
        role: Role,
        center_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> UserRecord:
        statement = select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.role == role.value,
            UserRecord.center_id == center_id,
        )
        if for_update:
            statement = statement.with_for_update()
        user = session.scalar(statement)
        if user is None:
            raise NotFoundError(f"{role.value.title()} was not found.")
        return user

    @staticmethod
    def _member_center_id(session: Session, member_id: uuid.UUID) -> uuid.UUID:
        center_id = session.scalar(
            select(UserRecord.center_id).where(UserRecord.id == member_id)
        )
        if center_id is None:
            raise NotFoundError("Member was not found.")
        return center_id
