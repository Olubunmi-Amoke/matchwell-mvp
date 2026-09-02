import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matchwell.domain.access import (
    AuthenticatedUser,
    OidcIdentity,
    Role,
    normalize_email,
)
from matchwell.domain.errors import ConflictError, NotFoundError, ValidationError
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
    MemberProfileRecord,
    OutboxMessageRecord,
    ReadinessDecisionRecord,
    ScreeningCaseRecord,
    ScreeningEventReceiptRecord,
    UserRecord,
)

PILOT_CENTER_SLUG = "matchwell-pilot"
READINESS_CONFIGURATION_VERSION = "pilot-v1"


class SqlAlchemyPilotRepository:
    def __init__(
        self,
        sessions: DatabaseSessionFactory,
        evaluator: ReadinessEvaluator,
    ) -> None:
        self._sessions = sessions
        self._evaluator = evaluator

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
                member = self._member(session, member_id, actor.center_id)
                counselor = self._role_user(
                    session,
                    counselor_id,
                    Role.COUNSELOR,
                    actor.center_id,
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

    def _evidence(self, session: Session, member_id: uuid.UUID) -> ReadinessEvidence:
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
        assignment, _ = self._ensure_current_assessment(session, member_id)
        assessment_complete = (
            assignment.completed_at is not None
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
            eligible=readiness.eligible,
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
    ) -> UserRecord:
        conditions = [
            UserRecord.id == member_id,
            UserRecord.role == Role.MEMBER.value,
        ]
        if center_id is not None:
            conditions.append(UserRecord.center_id == center_id)
        member = session.scalar(select(UserRecord).where(*conditions))
        if member is None:
            raise NotFoundError("Member was not found in the authorized Center.")
        return member

    @staticmethod
    def _role_user(
        session: Session,
        user_id: uuid.UUID,
        role: Role,
        center_id: uuid.UUID,
    ) -> UserRecord:
        user = session.scalar(
            select(UserRecord).where(
                UserRecord.id == user_id,
                UserRecord.role == role.value,
                UserRecord.center_id == center_id,
            )
        )
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
