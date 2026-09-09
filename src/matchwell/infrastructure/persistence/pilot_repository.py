import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matchwell.application.pilot import PaymentGateway
from matchwell.domain.access import (
    AccountStatus,
    AuthenticatedUser,
    OidcIdentity,
    Role,
    normalize_email,
)
from matchwell.domain.alerts import (
    AlertSnapshot,
    evaluate_auth_failures,
    evaluate_backup_drill_age,
    evaluate_overdue_queues,
    evaluate_provider_failures,
    evaluate_safety_activity,
)
from matchwell.domain.analytics import (
    FunnelSnapshot,
    PilotAnalyticsSnapshot,
    ProviderFailureSnapshot,
    SafetySnapshot,
    suppress_small_cell,
)
from matchwell.domain.billing import (
    BILLING_SYSTEM_ACTOR_ID,
    PILOT_INTAKE_CREDIT_MINOR_UNITS,
    PILOT_PLAN_CURRENCY,
    AdminBillingRow,
    BillingEventSource,
    BillingPortalSessionView,
    BillingWebhookEvent,
    CheckoutSessionView,
    CounselorEarningsView,
    EarningEntryType,
    EntitlementView,
    LedgerEntryView,
    ProviderEventType,
    SubscriptionStatus,
    WebhookFailureView,
)
from matchwell.domain.errors import (
    AccountDisabledError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from matchwell.domain.journey import (
    PILOT_CURRICULUM_KEY,
    CheckInMilestone,
    CounselorCheckInView,
    CounselorJourneyMemberView,
    CounselorJourneyView,
    JourneyTaskView,
    MemberCheckInView,
    MemberJourneyView,
    RelationshipStatus,
    ReminderState,
    TaskScope,
    reminder_state,
)
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
    SCREENING_SYSTEM_ACTOR_ID,
    AccountRow,
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
    ScreeningFailureView,
    ScreeningProviderEvent,
    ScreeningReasonCode,
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
    BackupDrillRunRecord,
    BillingCustomerRecord,
    BillingWebhookReceiptRecord,
    CenterRecord,
    CommunityRecord,
    ConsentAcceptanceRecord,
    ConsentVersionRecord,
    CounselorAssignmentRecord,
    CounselorDecisionRecord,
    CounselorEarningRecord,
    EntitlementHistoryRecord,
    HoldRecord,
    InvitationRecord,
    JourneyCheckInRecord,
    JourneyTaskCompletionRecord,
    JourneyTemplateRecord,
    JourneyTemplateTaskRecord,
    MatchedPairMessageRecord,
    MatchProposalRecord,
    MemberBlockRecord,
    MemberMatchPreferencesRecord,
    MemberProfileRecord,
    MemberReportRecord,
    OutboxMessageRecord,
    PairJourneyRecord,
    PilotPlanRecord,
    ReadinessDecisionRecord,
    ScreeningCaseRecord,
    ScreeningEventReceiptRecord,
    SubscriptionRecord,
    UserRecord,
)

PILOT_CENTER_SLUG = "matchwell-pilot"
READINESS_CONFIGURATION_VERSION = "pilot-v1"
DEFAULT_GRACE_PERIOD = timedelta(days=7)
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
        payment_gateway: PaymentGateway | None = None,
        grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    ) -> None:
        self._sessions = sessions
        self._evaluator = evaluator
        self._scorer = scorer or MatchScorer()
        self._payment_gateway = payment_gateway
        self._grace_period = grace_period

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
                # Disabled accounts are checked first and fail closed before
                # any role reconciliation or actor is ever returned. Adding
                # the email back to MATCHWELL_ADMIN_EMAILS never reactivates
                # a disabled account -- that remains an explicit privileged
                # administrator action (see reactivate_account).
                if existing.status == AccountStatus.DISABLED.value:
                    self._audit(
                        session,
                        actor_id=existing.id,
                        action="identity.sign_in_denied_disabled",
                        subject_id=existing.id,
                        center_id=existing.center_id,
                        metadata={"reason_code": existing.disabled_reason_code},
                    )
                    raise AccountDisabledError(
                        "This account has been disabled. Contact a pilot "
                        "administrator if you believe this is an error."
                    )
                existing.name = identity.name.strip() or existing.name
                self._reconcile_admin_access(session, existing, admin_emails)
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
                status=AccountStatus.ACTIVE.value,
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

    def _reconcile_admin_access(
        self,
        session: Session,
        user: UserRecord,
        admin_emails: frozenset[str],
    ) -> None:
        """Reconcile stored admin role against ``MATCHWELL_ADMIN_EMAILS``.

        Runs on every sign-in of an active account. Removing an email from
        the allow-list revokes admin access even though the stored role
        still says admin; adding it back grants admin access again. Neither
        direction ever touches ``status`` -- a disabled account is handled
        entirely by the caller before this runs.
        """
        is_admin = user.email in admin_emails
        stored_role = Role(user.role)
        if stored_role is Role.ADMIN and not is_admin:
            invited_role = session.scalar(
                select(InvitationRecord.role)
                .where(InvitationRecord.accepted_by_user_id == user.id)
                .order_by(InvitationRecord.accepted_at.desc())
            )
            restored_role = (
                Role(invited_role)
                if invited_role in {Role.MEMBER.value, Role.COUNSELOR.value}
                else Role.MEMBER
            )
            user.role = restored_role.value
            self._audit(
                session,
                actor_id=user.id,
                action="identity.admin_access_revoked",
                subject_id=user.id,
                center_id=user.center_id,
                metadata={
                    "reason": "email_removed_from_allowlist",
                    "restored_role": restored_role.value,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="identity.admin_access_revoked",
                    payload={"user_id": str(user.id)},
                )
            )
        elif stored_role is not Role.ADMIN and is_admin:
            user.role = Role.ADMIN.value
            self._audit(
                session,
                actor_id=user.id,
                action="identity.admin_access_granted",
                subject_id=user.id,
                center_id=user.center_id,
                metadata={
                    "reason": "email_added_to_allowlist",
                    "previous_role": stored_role.value,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="identity.admin_access_granted",
                    payload={"user_id": str(user.id)},
                )
            )

    def disable_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: str,
        admin_emails: frozenset[str],
    ) -> None:
        with self._sessions.session() as session, session.begin():
            if target_user_id == actor.id:
                raise ValidationError(
                    "An administrator cannot disable their own account."
                )
            target = session.scalar(
                select(UserRecord)
                .where(
                    UserRecord.id == target_user_id,
                    UserRecord.center_id == actor.center_id,
                )
                .with_for_update()
            )
            if target is None:
                raise NotFoundError("The account was not found in this Center.")
            if target.status == AccountStatus.DISABLED.value:
                raise ConflictError("This account is already disabled.")
            if target.role == Role.ADMIN.value:
                active_admins = session.scalar(
                    select(func.count(UserRecord.id)).where(
                        UserRecord.center_id == actor.center_id,
                        UserRecord.role == Role.ADMIN.value,
                        UserRecord.status == AccountStatus.ACTIVE.value,
                        UserRecord.email.in_(admin_emails),
                    )
                )
                if (active_admins or 0) <= 1:
                    raise ConflictError(
                        "The last active administrator in this Center cannot "
                        "be disabled."
                    )
            now = self._now()
            target.status = AccountStatus.DISABLED.value
            target.disabled_reason_code = reason_code
            target.disabled_at = now
            target.disabled_by_id = actor.id
            if target.role == Role.MEMBER.value:
                self._close_open_proposals_for_member(
                    session,
                    target.id,
                    actor.id,
                    "account_disabled",
                )
            self._audit(
                session,
                actor_id=actor.id,
                action="account.disabled",
                subject_id=target.id,
                center_id=actor.center_id,
                metadata={"reason_code": reason_code, "role": target.role},
            )
            session.add(
                OutboxMessageRecord(
                    event_type="account.disabled",
                    payload={
                        "user_id": str(target.id),
                        "center_id": str(actor.center_id),
                        "role": target.role,
                        "reason_code": reason_code,
                    },
                )
            )

    def reactivate_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            target = session.scalar(
                select(UserRecord)
                .where(
                    UserRecord.id == target_user_id,
                    UserRecord.center_id == actor.center_id,
                )
                .with_for_update()
            )
            if target is None:
                raise NotFoundError("The account was not found in this Center.")
            if target.status != AccountStatus.DISABLED.value:
                raise ConflictError("This account is not currently disabled.")
            now = self._now()
            target.status = AccountStatus.ACTIVE.value
            target.reactivated_reason_code = reason_code
            target.reactivated_at = now
            target.reactivated_by_id = actor.id
            self._audit(
                session,
                actor_id=actor.id,
                action="account.reactivated",
                subject_id=target.id,
                center_id=actor.center_id,
                metadata={"reason_code": reason_code, "role": target.role},
            )
            session.add(
                OutboxMessageRecord(
                    event_type="account.reactivated",
                    payload={
                        "user_id": str(target.id),
                        "center_id": str(actor.center_id),
                        "role": target.role,
                        "reason_code": reason_code,
                    },
                )
            )

    def list_accounts(self, actor: AuthenticatedUser) -> Sequence[AccountRow]:
        with self._sessions.session() as session, session.begin():
            records = session.scalars(
                select(UserRecord)
                .where(UserRecord.center_id == actor.center_id)
                .order_by(UserRecord.role, UserRecord.name)
            ).all()
            rows = [
                AccountRow(
                    id=record.id,
                    email=record.email,
                    display_name=record.name,
                    role=Role(record.role),
                    status=AccountStatus(record.status),
                    disabled_reason_code=record.disabled_reason_code,
                    disabled_at=record.disabled_at,
                    is_self=record.id == actor.id,
                    counselor_needs_reassignment=(
                        record.role == Role.COUNSELOR.value
                        and record.status == AccountStatus.DISABLED.value
                        and self._counselor_has_active_members(session, record.id)
                    ),
                )
                for record in records
            ]
            self._audit(
                session,
                actor_id=actor.id,
                action="admin.account_queue_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(rows)},
            )
            return rows

    @staticmethod
    def _counselor_has_active_members(
        session: Session, counselor_id: uuid.UUID
    ) -> bool:
        return (
            session.scalar(
                select(CounselorAssignmentRecord.id).where(
                    CounselorAssignmentRecord.counselor_id == counselor_id,
                    CounselorAssignmentRecord.ended_at.is_(None),
                )
            )
            is not None
        )

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
            if status is not CounselorDecisionStatus.PENDING:
                self._maybe_credit_intake_earning(session, counselor, member_id)
            session.flush()
            self._reevaluate(session, member_id, counselor.id)

    def _maybe_credit_intake_earning(
        self,
        session: Session,
        counselor: AuthenticatedUser,
        member_id: uuid.UUID,
    ) -> None:
        """Credit exactly one $25 earning for a member's first intake decision.

        The unique partial index on ``intake_member_id`` for
        ``entry_type = 'intake_credit'`` guarantees this can never duplicate or
        move to a later counselor, even after reassignment.
        """
        try:
            with session.begin_nested():
                session.add(
                    CounselorEarningRecord(
                        center_id=counselor.center_id,
                        counselor_id=counselor.id,
                        intake_member_id=member_id,
                        entry_type=EarningEntryType.INTAKE_CREDIT.value,
                        amount_minor_units=PILOT_INTAKE_CREDIT_MINOR_UNITS,
                        currency=PILOT_PLAN_CURRENCY,
                        reason_code=None,
                        created_by_id=counselor.id,
                    )
                )
                session.flush()
        except IntegrityError:
            return
        self._audit(
            session,
            actor_id=counselor.id,
            action="billing.earnings_credited",
            subject_id=member_id,
            center_id=counselor.center_id,
            metadata={
                "counselor_id": str(counselor.id),
                "amount_minor_units": PILOT_INTAKE_CREDIT_MINOR_UNITS,
            },
        )
        session.add(
            OutboxMessageRecord(
                event_type="billing.earnings_credited",
                payload={
                    "counselor_id": str(counselor.id),
                    "member_id": str(member_id),
                    "amount_minor_units": PILOT_INTAKE_CREDIT_MINOR_UNITS,
                },
            )
        )

    def record_screening_status(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        status: ScreeningStatus,
        provider_event_id: str,
        provider_reference: str,
        reason_code: ScreeningReasonCode | None,
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
                            member_id=member_id,
                            center_id=actor.center_id,
                            event_type="status_update",
                            applied=True,
                        )
                    )
                    session.flush()
            except IntegrityError:
                return False
            now = self._now()
            reason_value = reason_code.value if reason_code is not None else None
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
                    reason_code=reason_value,
                    requested_at=now,
                    updated_at=now,
                )
                session.add(case)
            else:
                case.provider_reference = provider_reference
                case.status = status.value
                case.reason_code = reason_value
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
                    "reason_code": reason_value,
                    "event_id": provider_event_id,
                },
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)
            return True

    def process_screening_provider_event(
        self,
        event: ScreeningProviderEvent,
    ) -> bool:
        """Idempotently process a normalized screening provider callback.

        Mirrors ``process_billing_webhook_event``: a receipt is always
        recorded first (duplicates are acknowledged without reprocessing),
        an unrecognized event type is recorded unapplied, and a reference
        that cannot be resolved to a member is recorded unapplied with a
        safe reason code. Never stores a screening report or free text.
        """
        with self._sessions.session() as session, session.begin():
            receipt = ScreeningEventReceiptRecord(
                provider=event.provider,
                provider_event_id=event.provider_event_id,
                event_type=(
                    event.status.value if event.status is not None else "unrecognized"
                ),
                applied=False,
                unresolved_reason=(
                    None
                    if event.status is not None
                    else ScreeningReasonCode.OTHER_OPERATIONAL.value
                ),
            )
            try:
                with session.begin_nested():
                    session.add(receipt)
                    session.flush()
            except IntegrityError:
                return False

            if event.status is None or event.provider_reference is None:
                return True

            case = session.scalar(
                select(ScreeningCaseRecord).where(
                    ScreeningCaseRecord.provider_reference == event.provider_reference
                )
            )
            if case is None:
                receipt.unresolved_reason = "manual_review_required"
                session.flush()
                return True

            receipt.member_id = case.member_id
            receipt.center_id = self._member_center_id(session, case.member_id)
            now = self._now()
            case.status = event.status.value
            case.reason_code = (
                event.reason_code.value if event.reason_code is not None else None
            )
            case.updated_at = now
            case.expires_at = (
                now + timedelta(days=365)
                if event.status is ScreeningStatus.ELIGIBLE
                else None
            )
            receipt.applied = True
            receipt.unresolved_reason = None
            self._audit(
                session,
                actor_id=SCREENING_SYSTEM_ACTOR_ID,
                action="screening.status_recorded",
                subject_id=case.member_id,
                center_id=receipt.center_id,
                metadata={
                    "provider": event.provider,
                    "status": event.status.value,
                    "reason_code": case.reason_code,
                    "event_id": event.provider_event_id,
                },
            )
            session.flush()
            self._reevaluate(session, case.member_id, SCREENING_SYSTEM_ACTOR_ID)
            return True

    def screening_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[ScreeningFailureView]:
        """Unapplied screening receipts an admin can triage.

        Scoped to the admin's own Center, same as billing webhook failures.
        A receipt that never resolved to a member has ``center_id IS NULL``
        and is therefore never returned to any Center admin here.
        """
        with self._sessions.session() as session, session.begin():
            rows = session.scalars(
                select(ScreeningEventReceiptRecord)
                .where(
                    ScreeningEventReceiptRecord.applied.is_(False),
                    ScreeningEventReceiptRecord.center_id == actor.center_id,
                )
                .order_by(ScreeningEventReceiptRecord.received_at.desc())
            ).all()
            views = [
                ScreeningFailureView(
                    id=row.id,
                    provider=row.provider,
                    provider_event_id=row.provider_event_id,
                    event_type=row.event_type,
                    member_id=row.member_id,
                    unresolved_reason=row.unresolved_reason,
                    received_at=row.received_at,
                )
                for row in rows
            ]
            self._audit(
                session,
                actor_id=actor.id,
                action="screening.failures_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(views)},
            )
            return views

    def analytics_snapshot(
        self,
        actor: AuthenticatedUser,
    ) -> PilotAnalyticsSnapshot:
        """Center-scoped, privacy-safe aggregate funnel/safety/provider
        counts. No raw member export, free text, or content of any kind is
        ever read here -- every value is a ``COUNT(*)``."""
        with self._sessions.session() as session, session.begin():
            center_id = actor.center_id
            member_ids = select(UserRecord.id).where(
                UserRecord.center_id == center_id,
                UserRecord.role == Role.MEMBER.value,
                UserRecord.status == AccountStatus.ACTIVE.value,
            )

            def _count(statement: Any) -> int:
                return session.scalar(statement) or 0

            invitations_sent = _count(
                select(func.count()).select_from(
                    select(InvitationRecord.id)
                    .where(InvitationRecord.center_id == center_id)
                    .subquery()
                )
            )
            accounts_created = _count(
                select(func.count()).select_from(member_ids.subquery())
            )
            profile_completed = _count(
                select(func.count(func.distinct(MemberProfileRecord.user_id))).where(
                    MemberProfileRecord.user_id.in_(member_ids)
                )
            )
            consent_accepted = _count(
                select(
                    func.count(func.distinct(ConsentAcceptanceRecord.user_id))
                ).where(ConsentAcceptanceRecord.user_id.in_(member_ids))
            )
            assessment_completed = _count(
                select(
                    func.count(func.distinct(AssessmentAssignmentRecord.member_id))
                ).where(
                    AssessmentAssignmentRecord.member_id.in_(member_ids),
                    AssessmentAssignmentRecord.completed_at.isnot(None),
                )
            )
            counselor_assigned = _count(
                select(
                    func.count(func.distinct(CounselorAssignmentRecord.member_id))
                ).where(
                    CounselorAssignmentRecord.member_id.in_(member_ids),
                    CounselorAssignmentRecord.ended_at.is_(None),
                )
            )
            screening_eligible = _count(
                select(func.count(func.distinct(ScreeningCaseRecord.member_id))).where(
                    ScreeningCaseRecord.member_id.in_(member_ids),
                    ScreeningCaseRecord.status == "eligible",
                )
            )
            subscription_ready = _count(
                select(func.count()).select_from(
                    select(SubscriptionRecord.id)
                    .where(
                        SubscriptionRecord.center_id == center_id,
                        SubscriptionRecord.status.in_(
                            (
                                SubscriptionStatus.ACTIVE.value,
                                SubscriptionStatus.GRACE.value,
                                SubscriptionStatus.COMPLIMENTARY.value,
                            )
                        ),
                    )
                    .subquery()
                )
            )
            latest_readiness = (
                select(
                    ReadinessDecisionRecord.member_id,
                    func.max(ReadinessDecisionRecord.evaluated_at).label("max_at"),
                )
                .where(ReadinessDecisionRecord.member_id.in_(member_ids))
                .group_by(ReadinessDecisionRecord.member_id)
                .subquery()
            )
            community_eligible = _count(
                select(func.count())
                .select_from(ReadinessDecisionRecord)
                .join(
                    latest_readiness,
                    and_(
                        ReadinessDecisionRecord.member_id
                        == latest_readiness.c.member_id,
                        ReadinessDecisionRecord.evaluated_at
                        == latest_readiness.c.max_at,
                    ),
                )
                .where(ReadinessDecisionRecord.eligible.is_(True))
            )
            proposals_generated = _count(
                select(func.count()).select_from(
                    select(MatchProposalRecord.id)
                    .where(MatchProposalRecord.center_id == center_id)
                    .subquery()
                )
            )
            introductions_awaiting_response = _count(
                select(func.count()).select_from(
                    select(MatchProposalRecord.id)
                    .where(
                        MatchProposalRecord.center_id == center_id,
                        MatchProposalRecord.status == ProposalStatus.INTRODUCED.value,
                    )
                    .subquery()
                )
            )
            active_matches = _count(
                select(func.count()).select_from(
                    select(MatchProposalRecord.id)
                    .where(
                        MatchProposalRecord.center_id == center_id,
                        MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
                    )
                    .subquery()
                )
            )
            guided_journeys_started = _count(
                select(func.count()).select_from(
                    select(PairJourneyRecord.id)
                    .where(PairJourneyRecord.center_id == center_id)
                    .subquery()
                )
            )
            checkins_submitted = _count(
                select(func.count()).select_from(
                    select(JourneyCheckInRecord.id)
                    .where(JourneyCheckInRecord.center_id == center_id)
                    .subquery()
                )
            )
            blocks = _count(
                select(func.count()).select_from(
                    select(MemberBlockRecord.id)
                    .where(MemberBlockRecord.center_id == center_id)
                    .subquery()
                )
            )
            reports = _count(
                select(func.count()).select_from(
                    select(MemberReportRecord.id)
                    .where(MemberReportRecord.center_id == center_id)
                    .subquery()
                )
            )
            active_holds = _count(
                select(func.count()).select_from(
                    select(HoldRecord.id)
                    .where(
                        HoldRecord.center_context_id == center_id,
                        HoldRecord.released_at.is_(None),
                    )
                    .subquery()
                )
            )
            billing_failures = _count(
                select(func.count()).select_from(
                    select(BillingWebhookReceiptRecord.id)
                    .where(
                        BillingWebhookReceiptRecord.applied.is_(False),
                        BillingWebhookReceiptRecord.center_id == center_id,
                    )
                    .subquery()
                )
            )
            screening_failure_count = _count(
                select(func.count()).select_from(
                    select(ScreeningEventReceiptRecord.id)
                    .where(
                        ScreeningEventReceiptRecord.applied.is_(False),
                        ScreeningEventReceiptRecord.center_id == center_id,
                    )
                    .subquery()
                )
            )

            snapshot = PilotAnalyticsSnapshot(
                funnel=FunnelSnapshot(
                    invitations_sent=invitations_sent,
                    accounts_created=accounts_created,
                    profile_completed=profile_completed,
                    consent_accepted=consent_accepted,
                    assessment_completed=assessment_completed,
                    counselor_assigned=counselor_assigned,
                    screening_eligible=screening_eligible,
                    subscription_ready=subscription_ready,
                    community_eligible=community_eligible,
                    proposals_generated=proposals_generated,
                    introductions_awaiting_response=introductions_awaiting_response,
                    active_matches=active_matches,
                    guided_journeys_started=guided_journeys_started,
                    checkins_submitted=checkins_submitted,
                ),
                safety=SafetySnapshot(
                    blocks=suppress_small_cell(blocks),
                    reports=suppress_small_cell(reports),
                    active_holds=suppress_small_cell(active_holds),
                ),
                provider_failures=ProviderFailureSnapshot(
                    billing_webhook_failures=suppress_small_cell(billing_failures),
                    screening_failures=suppress_small_cell(screening_failure_count),
                ),
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="admin.analytics_accessed",
                subject_id=actor.id,
                center_id=center_id,
                metadata={},
            )
            return snapshot

    def alert_snapshot(self, actor: AuthenticatedUser) -> AlertSnapshot:
        """Alert-ready aggregate metrics evaluated against real,
        DB-queryable signals only. No hosted monitoring provider is added;
        this is what the admin dashboard renders in its place."""
        with self._sessions.session() as session, session.begin():
            center_id = actor.center_id
            now = self._now()

            denied_sign_ins = (
                session.scalar(
                    select(func.count()).select_from(
                        select(AuditEventRecord.id)
                        .where(
                            AuditEventRecord.center_id == center_id,
                            AuditEventRecord.action
                            == "identity.sign_in_denied_disabled",
                            AuditEventRecord.occurred_at >= now - timedelta(hours=24),
                        )
                        .subquery()
                    )
                )
                or 0
            )
            billing_failures = (
                session.scalar(
                    select(func.count()).select_from(
                        select(BillingWebhookReceiptRecord.id)
                        .where(
                            BillingWebhookReceiptRecord.applied.is_(False),
                            BillingWebhookReceiptRecord.center_id == center_id,
                        )
                        .subquery()
                    )
                )
                or 0
            )
            screening_failure_count = (
                session.scalar(
                    select(func.count()).select_from(
                        select(ScreeningEventReceiptRecord.id)
                        .where(
                            ScreeningEventReceiptRecord.applied.is_(False),
                            ScreeningEventReceiptRecord.center_id == center_id,
                        )
                        .subquery()
                    )
                )
                or 0
            )
            overdue_check_ins = self._overdue_check_in_count(session, center_id, now)
            recent_safety_events = (
                session.scalar(
                    select(func.count()).select_from(
                        select(MemberBlockRecord.id)
                        .where(
                            MemberBlockRecord.center_id == center_id,
                            MemberBlockRecord.created_at >= now - timedelta(days=7),
                        )
                        .subquery()
                    )
                )
                or 0
            ) + (
                session.scalar(
                    select(func.count()).select_from(
                        select(MemberReportRecord.id)
                        .where(
                            MemberReportRecord.center_id == center_id,
                            MemberReportRecord.created_at >= now - timedelta(days=7),
                        )
                        .subquery()
                    )
                )
                or 0
            )
            latest_drill_at = session.scalar(
                select(func.max(BackupDrillRunRecord.performed_at)).where(
                    BackupDrillRunRecord.verification_passed.is_(True)
                )
            )
            drill_age_days = (
                (now - latest_drill_at).days if latest_drill_at is not None else None
            )

            metrics = (
                evaluate_auth_failures(denied_sign_ins),
                evaluate_provider_failures(billing_failures + screening_failure_count),
                evaluate_overdue_queues(overdue_check_ins),
                evaluate_safety_activity(recent_safety_events),
                evaluate_backup_drill_age(drill_age_days),
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="admin.alerts_accessed",
                subject_id=actor.id,
                center_id=center_id,
                metadata={"overall_status": AlertSnapshot(metrics).overall_status},
            )
            return AlertSnapshot(metrics=metrics)

    @staticmethod
    def _overdue_check_in_count(
        session: Session,
        center_id: uuid.UUID,
        now: datetime,
    ) -> int:
        """Count overdue per-member check-ins across every active pair
        journey in this Center.

        Two grouped queries (journeys+proposals, then check-ins), not a
        per-member round trip, using the same ``reminder_state`` due-date
        logic as the member/counselor journey views.
        """
        journey_rows = session.execute(
            select(
                PairJourneyRecord.id,
                PairJourneyRecord.started_at,
                MatchProposalRecord.member_a_id,
                MatchProposalRecord.member_b_id,
            )
            .join(
                MatchProposalRecord,
                MatchProposalRecord.id == PairJourneyRecord.proposal_id,
            )
            .where(
                PairJourneyRecord.center_id == center_id,
                MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
            )
        ).all()
        if not journey_rows:
            return 0
        journey_ids = [row.id for row in journey_rows]
        submitted = {
            (row.journey_id, row.member_id, row.milestone)
            for row in session.execute(
                select(
                    JourneyCheckInRecord.journey_id,
                    JourneyCheckInRecord.member_id,
                    JourneyCheckInRecord.milestone,
                ).where(JourneyCheckInRecord.journey_id.in_(journey_ids))
            )
        }
        overdue = 0
        for journey_id, started_at, member_a_id, member_b_id in journey_rows:
            for member_id in (member_a_id, member_b_id):
                for milestone in CheckInMilestone:
                    due_at = started_at + timedelta(days=milestone.days)
                    already_submitted = (
                        journey_id,
                        member_id,
                        milestone.value,
                    ) in submitted
                    submitted_at = now if already_submitted else None
                    if reminder_state(due_at, submitted_at, now) is (
                        ReminderState.OVERDUE
                    ):
                        overdue += 1
        return overdue

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
                    UserRecord.status == AccountStatus.ACTIVE.value,
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
            existing_pairs, restricted_pairs = self._matching_pair_sets(
                session, actor.center_id
            )
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
                    UserRecord.status == AccountStatus.ACTIVE.value,
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

            existing_pairs, restricted_pairs = self._matching_pair_sets(
                session, actor.center_id
            )
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
        center_id: uuid.UUID,
    ) -> tuple[set[tuple[uuid.UUID, uuid.UUID]], set[tuple[uuid.UUID, uuid.UUID]]]:
        # Proposal history is scoped to this Center: a proposal recorded in
        # another Center must never block or otherwise affect candidate
        # generation here. Blocks and reports are deliberately left
        # unscoped: they are global user-safety restrictions on the pair of
        # member IDs involved, and must hold regardless of which Center
        # context matching happens to run in.
        existing_pairs = {
            self._pair_key(row[0], row[1])
            for row in session.execute(
                select(
                    MatchProposalRecord.member_a_id,
                    MatchProposalRecord.member_b_id,
                ).where(MatchProposalRecord.center_id == center_id)
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
                self._candidate_review_item(session, row, counselor.id)
                for row in rows
                if self._pair_entitled(session, row)
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
            if not self._pair_entitled(session, proposal):
                self._reconcile_entitlement_lapse(session, proposal)
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
        with self._sessions.session() as session, session.begin():
            proposal = session.scalar(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.status == ProposalStatus.INTRODUCED.value,
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
                .with_for_update()
            )
            if proposal is None:
                return None
            if not self._pair_entitled(session, proposal):
                self._reconcile_entitlement_lapse(session, proposal)
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
            if not self._pair_entitled(session, proposal):
                self._reconcile_entitlement_lapse(session, proposal)
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
        with self._sessions.session() as session, session.begin():
            proposal = session.scalar(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
                    or_(
                        MatchProposalRecord.member_a_id == member_id,
                        MatchProposalRecord.member_b_id == member_id,
                    ),
                )
                .with_for_update()
            )
            if proposal is None or proposal.activated_at is None:
                return None
            if not self._pair_entitled(session, proposal):
                self._reconcile_entitlement_lapse(session, proposal)
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
            sent_at = self._now()
            latest_sent_at = session.scalar(
                select(func.max(MatchedPairMessageRecord.sent_at)).where(
                    MatchedPairMessageRecord.proposal_id == proposal.id
                )
            )
            if latest_sent_at is not None:
                if latest_sent_at.tzinfo is None:
                    latest_sent_at = latest_sent_at.replace(tzinfo=UTC)
                if sent_at <= latest_sent_at:
                    sent_at = latest_sent_at + timedelta(microseconds=1)
            record = MatchedPairMessageRecord(
                center_id=member.center_id,
                proposal_id=proposal.id,
                sender_id=member.id,
                body=body,
                sent_at=sent_at,
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
                if not self._pair_entitled(session, proposal):
                    self._reconcile_entitlement_lapse(
                        session,
                        proposal,
                        commit_immediately=False,
                    )
                    continue
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

    def get_member_journey(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> MemberJourneyView | None:
        with self._sessions.session() as session, session.begin():
            proposal = self._active_participant_proposal(
                session, member, proposal_id, lock=True
            )
            journey = session.scalar(
                select(PairJourneyRecord).where(
                    PairJourneyRecord.proposal_id == proposal.id,
                    PairJourneyRecord.center_id == member.center_id,
                )
            )
            if journey is None:
                return None
            return self._member_journey_view(session, proposal, journey, member.id)

    def assign_guided_journey(
        self,
        counselor: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> uuid.UUID:
        with self._sessions.session() as session, session.begin():
            proposal = self._active_counselor_proposal(
                session, counselor, proposal_id, lock=True
            )
            existing = session.scalar(
                select(PairJourneyRecord).where(
                    PairJourneyRecord.proposal_id == proposal.id
                )
            )
            if existing is not None:
                return existing.id
            template = session.scalar(
                select(JourneyTemplateRecord)
                .where(
                    JourneyTemplateRecord.key == PILOT_CURRICULUM_KEY,
                    JourneyTemplateRecord.is_active.is_(True),
                )
                .order_by(JourneyTemplateRecord.version.desc())
            )
            if template is None:
                raise NotFoundError("The pilot curriculum is not configured.")
            journey = PairJourneyRecord(
                center_id=counselor.center_id,
                proposal_id=proposal.id,
                template_id=template.id,
                assigned_by_id=counselor.id,
                started_at=self._now(),
            )
            session.add(journey)
            session.flush()
            self._audit(
                session,
                actor_id=counselor.id,
                action="journey.assigned",
                subject_id=journey.id,
                center_id=counselor.center_id,
                metadata={
                    "proposal_id": str(proposal.id),
                    "template_id": str(template.id),
                    "template_version": template.version,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="journey.assigned",
                    payload={
                        "journey_id": str(journey.id),
                        "proposal_id": str(proposal.id),
                        "template_id": str(template.id),
                    },
                )
            )
            return journey.id

    def set_journey_task_completion(
        self,
        member: AuthenticatedUser,
        journey_id: uuid.UUID,
        task_id: uuid.UUID,
        completed: bool,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            journey = self._member_active_journey(
                session, member, journey_id, lock=True
            )
            task = session.scalar(
                select(JourneyTemplateTaskRecord).where(
                    JourneyTemplateTaskRecord.id == task_id,
                    JourneyTemplateTaskRecord.template_id == journey.template_id,
                )
            )
            if task is None:
                raise NotFoundError("Journey activity was not found.")
            completion = session.scalar(
                select(JourneyTaskCompletionRecord)
                .where(
                    JourneyTaskCompletionRecord.journey_id == journey.id,
                    JourneyTaskCompletionRecord.task_id == task.id,
                    JourneyTaskCompletionRecord.member_id == member.id,
                )
                .with_for_update()
            )
            was_completed = (
                completion is not None and completion.completed_at is not None
            )
            if was_completed == completed:
                return
            now = self._now()
            if completion is None:
                completion = JourneyTaskCompletionRecord(
                    center_id=member.center_id,
                    journey_id=journey.id,
                    task_id=task.id,
                    member_id=member.id,
                    completed_at=now if completed else None,
                    updated_at=now,
                )
                session.add(completion)
            else:
                completion.completed_at = now if completed else None
                completion.updated_at = now
            session.flush()
            event_type = (
                "journey.task_completed" if completed else "journey.task_reopened"
            )
            metadata: dict[str, object] = {
                "journey_id": str(journey.id),
                "task_id": str(task.id),
                "member_id": str(member.id),
            }
            self._audit(
                session,
                actor_id=member.id,
                action=event_type,
                subject_id=completion.id,
                center_id=member.center_id,
                metadata=metadata,
            )
            session.add(OutboxMessageRecord(event_type=event_type, payload=metadata))

    def submit_journey_check_in(
        self,
        member: AuthenticatedUser,
        journey_id: uuid.UUID,
        milestone: CheckInMilestone,
        relationship_status: RelationshipStatus,
        support_requested: bool,
        concern_flag: bool,
        private_reflection: str,
        share_with_counselor: bool,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            journey = self._member_active_journey(
                session, member, journey_id, lock=True
            )
            now = self._now()
            due_at = self._as_utc(journey.started_at) + timedelta(days=milestone.days)
            existing = session.scalar(
                select(JourneyCheckInRecord)
                .where(
                    JourneyCheckInRecord.journey_id == journey.id,
                    JourneyCheckInRecord.member_id == member.id,
                    JourneyCheckInRecord.milestone == milestone.value,
                )
                .with_for_update()
            )
            if (
                existing is None
                and reminder_state(due_at, None, now) is ReminderState.SCHEDULED
            ):
                raise ConflictError(
                    f"This check-in opens on "
                    f"{(due_at - timedelta(days=7)).strftime('%B %d, %Y')}."
                )
            if existing is None:
                existing = JourneyCheckInRecord(
                    center_id=member.center_id,
                    journey_id=journey.id,
                    member_id=member.id,
                    milestone=milestone.value,
                    relationship_status=relationship_status.value,
                    support_requested=support_requested,
                    concern_flag=concern_flag,
                    private_reflection=private_reflection,
                    share_with_counselor=share_with_counselor,
                    submitted_at=now,
                    updated_at=now,
                )
                session.add(existing)
            else:
                existing.relationship_status = relationship_status.value
                existing.support_requested = support_requested
                existing.concern_flag = concern_flag
                existing.private_reflection = private_reflection
                existing.share_with_counselor = share_with_counselor
                existing.updated_at = now
            session.flush()
            safe_metadata: dict[str, object] = {
                "journey_id": str(journey.id),
                "member_id": str(member.id),
                "milestone": milestone.value,
                "support_requested": support_requested,
                "concern_flag": concern_flag,
                "reflection_shared": share_with_counselor,
            }
            self._audit(
                session,
                actor_id=member.id,
                action="journey.check_in_submitted",
                subject_id=existing.id,
                center_id=member.center_id,
                metadata=safe_metadata,
            )
            session.add(
                OutboxMessageRecord(
                    event_type="journey.check_in_submitted",
                    payload={
                        "check_in_id": str(existing.id),
                        **safe_metadata,
                    },
                )
            )

    def list_counselor_journeys(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CounselorJourneyView]:
        with self._sessions.session() as session, session.begin():
            member_ids = list(
                session.scalars(
                    select(CounselorAssignmentRecord.member_id).where(
                        CounselorAssignmentRecord.center_id == counselor.center_id,
                        CounselorAssignmentRecord.counselor_id == counselor.id,
                        CounselorAssignmentRecord.ended_at.is_(None),
                    )
                )
            )
            if not member_ids:
                return []
            proposals = session.scalars(
                select(MatchProposalRecord)
                .where(
                    MatchProposalRecord.center_id == counselor.center_id,
                    MatchProposalRecord.status == ProposalStatus.ACTIVE.value,
                    or_(
                        MatchProposalRecord.member_a_id.in_(member_ids),
                        MatchProposalRecord.member_b_id.in_(member_ids),
                    ),
                )
                .order_by(MatchProposalRecord.activated_at.desc())
                .with_for_update()
            ).all()
            views: list[CounselorJourneyView] = []
            for proposal in proposals:
                if not self._pair_entitled(session, proposal):
                    self._reconcile_entitlement_lapse(
                        session,
                        proposal,
                        commit_immediately=False,
                    )
                    continue
                views.append(
                    self._counselor_journey_view(
                        session,
                        proposal,
                        session.scalar(
                            select(PairJourneyRecord).where(
                                PairJourneyRecord.proposal_id == proposal.id
                            )
                        ),
                        counselor,
                    )
                )
            return views

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

    def billing_status(self, member_id: uuid.UUID) -> EntitlementView:
        with self._sessions.session() as session:
            return self._entitlement_view(session, member_id)

    def create_checkout_session(
        self,
        actor: AuthenticatedUser,
    ) -> CheckoutSessionView:
        if self._payment_gateway is None:
            raise ValidationError("Billing is not configured for this environment.")
        with self._sessions.session() as session, session.begin():
            member = self._member(session, actor.id, actor.center_id)
            customer = self._billing_customer(session, actor.id)
            result = self._payment_gateway.create_checkout_session(
                member_id=member.id,
                member_email=member.email,
                existing_provider_customer_id=(
                    customer.provider_customer_id if customer is not None else None
                ),
            )
            if customer is None:
                session.add(
                    BillingCustomerRecord(
                        member_id=actor.id,
                        center_id=actor.center_id,
                        provider="stripe",
                        provider_customer_id=result.provider_customer_id,
                    )
                )
            elif customer.provider_customer_id != result.provider_customer_id:
                customer.provider_customer_id = result.provider_customer_id
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.checkout_session_created",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"provider_session_id": result.provider_session_id},
            )
            session.flush()
            return CheckoutSessionView(
                url=result.url,
                provider_session_id=result.provider_session_id,
                provider_customer_id=result.provider_customer_id,
            )

    def create_billing_portal_session(
        self,
        actor: AuthenticatedUser,
    ) -> BillingPortalSessionView:
        if self._payment_gateway is None:
            raise ValidationError("Billing is not configured for this environment.")
        with self._sessions.session() as session, session.begin():
            self._member(session, actor.id, actor.center_id)
            customer = self._billing_customer(session, actor.id)
            if customer is None:
                raise NotFoundError("Start checkout before opening the billing portal.")
            result = self._payment_gateway.create_billing_portal_session(
                provider_customer_id=customer.provider_customer_id,
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.portal_session_created",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={},
            )
            return result

    def billing_queue(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[AdminBillingRow]:
        with self._sessions.session() as session, session.begin():
            members = session.scalars(
                select(UserRecord).where(
                    UserRecord.center_id == actor.center_id,
                    UserRecord.role == Role.MEMBER.value,
                )
            ).all()
            rows = []
            for member in members:
                view = self._entitlement_view(session, member.id)
                profile = session.get(MemberProfileRecord, member.id)
                rows.append(
                    AdminBillingRow(
                        member_id=member.id,
                        display_name=(
                            profile.display_name if profile is not None else member.name
                        ),
                        email=member.email,
                        status=view.status,
                        current_period_end=view.current_period_end,
                        cancel_at_period_end=view.cancel_at_period_end,
                        grace_expires_at=view.grace_expires_at,
                        updated_at=view.updated_at,
                    )
                )
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.queue_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(rows)},
            )
            return rows

    def billing_webhook_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[WebhookFailureView]:
        """Unapplied webhook receipts an admin can triage and reprocess.

        Deliberately safe: only identifiers, the recognized-or-not event
        type, and a safe reason code are exposed. The raw payload, signature,
        and provider secrets are never stored on the receipt, so there is
        nothing sensitive to leak here.

        Scoped to the admin's own Center. A receipt whose member/customer
        could not be resolved has ``center_id IS NULL`` and is therefore
        never returned to any Center admin here -- this pilot has no
        platform-wide scope, so those events require direct engineering
        investigation (see docs/runbooks/provider-failure-recovery.md).
        """
        with self._sessions.session() as session, session.begin():
            rows = session.scalars(
                select(BillingWebhookReceiptRecord)
                .where(
                    BillingWebhookReceiptRecord.applied.is_(False),
                    BillingWebhookReceiptRecord.center_id == actor.center_id,
                )
                .order_by(BillingWebhookReceiptRecord.received_at.desc())
            ).all()
            views = [
                WebhookFailureView(
                    id=row.id,
                    provider=row.provider,
                    provider_event_id=row.provider_event_id,
                    event_type=row.event_type,
                    unresolved_reason=row.unresolved_reason,
                    received_at=row.received_at,
                )
                for row in rows
            ]
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.webhook_failures_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(views)},
            )
            return views

    def grant_complimentary_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id, actor.center_id)
            self._transition_subscription(
                session,
                member_id=member_id,
                center_id=actor.center_id,
                to_status=SubscriptionStatus.COMPLIMENTARY,
                provider="complimentary",
                provider_subscription_id=None,
                current_period_end=None,
                cancel_at_period_end=False,
                grace_expires_at=None,
                source=BillingEventSource.ADMIN_COMPLIMENTARY,
                reason_code=reason_code,
                provider_event_id=None,
                occurred_at=self._now(),
                actor_id=actor.id,
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)

    def suspend_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            self._member(session, member_id, actor.center_id)
            record = self._subscription(session, member_id)
            self._transition_subscription(
                session,
                member_id=member_id,
                center_id=actor.center_id,
                to_status=SubscriptionStatus.SUSPENDED,
                provider=record.provider if record is not None else "manual",
                provider_subscription_id=(
                    record.provider_subscription_id if record is not None else None
                ),
                current_period_end=(
                    record.current_period_end if record is not None else None
                ),
                cancel_at_period_end=(
                    record.cancel_at_period_end if record is not None else False
                ),
                grace_expires_at=None,
                source=BillingEventSource.ADMIN_CORRECTION,
                reason_code=reason_code,
                provider_event_id=None,
                occurred_at=self._now(),
                actor_id=actor.id,
            )
            session.flush()
            self._reevaluate(session, member_id, actor.id)

    def counselor_earnings(self, actor: AuthenticatedUser) -> CounselorEarningsView:
        with self._sessions.session() as session:
            rows = session.scalars(
                select(CounselorEarningRecord)
                .where(
                    CounselorEarningRecord.counselor_id == actor.id,
                    CounselorEarningRecord.center_id == actor.center_id,
                )
                .order_by(CounselorEarningRecord.created_at)
            ).all()
            return CounselorEarningsView(
                entries=tuple(self._ledger_entry(session, row) for row in rows)
            )

    def ledger(self, actor: AuthenticatedUser) -> Sequence[LedgerEntryView]:
        with self._sessions.session() as session, session.begin():
            rows = session.scalars(
                select(CounselorEarningRecord)
                .where(CounselorEarningRecord.center_id == actor.center_id)
                .order_by(CounselorEarningRecord.created_at)
            ).all()
            entries = [self._ledger_entry(session, row) for row in rows]
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.ledger_accessed",
                subject_id=actor.id,
                center_id=actor.center_id,
                metadata={"record_count": len(entries)},
            )
            return entries

    def record_earnings_adjustment(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        amount_minor_units: int,
        reason_code: str,
    ) -> None:
        with self._sessions.session() as session, session.begin():
            counselor = self._role_user(
                session, counselor_id, Role.COUNSELOR, actor.center_id
            )
            session.add(
                CounselorEarningRecord(
                    center_id=actor.center_id,
                    counselor_id=counselor.id,
                    intake_member_id=None,
                    entry_type=EarningEntryType.ADMIN_ADJUSTMENT.value,
                    amount_minor_units=amount_minor_units,
                    currency=PILOT_PLAN_CURRENCY,
                    reason_code=reason_code,
                    created_by_id=actor.id,
                )
            )
            self._audit(
                session,
                actor_id=actor.id,
                action="billing.earnings_adjusted",
                subject_id=counselor.id,
                center_id=actor.center_id,
                metadata={
                    "amount_minor_units": amount_minor_units,
                    "reason_code": reason_code,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="billing.earnings_adjusted",
                    payload={
                        "counselor_id": str(counselor.id),
                        "amount_minor_units": amount_minor_units,
                    },
                )
            )

    def process_billing_webhook_event(self, event: BillingWebhookEvent) -> bool:
        with self._sessions.session() as session, session.begin():
            receipt = BillingWebhookReceiptRecord(
                provider=event.provider,
                provider_event_id=event.provider_event_id,
                event_type=(
                    event.event_type.value
                    if event.event_type is not None
                    else "unrecognized"
                ),
                # A receipt always starts unapplied: it only flips to applied
                # once a recognized event has actually changed domain state
                # below. Nothing here ever claims success prematurely.
                applied=False,
                unresolved_reason=(
                    None if event.event_type is not None else "unrecognized_event_type"
                ),
            )
            try:
                with session.begin_nested():
                    session.add(receipt)
                    session.flush()
            except IntegrityError:
                # Duplicate provider event ID: already recorded (applied or
                # not). Acknowledge without reprocessing or overwriting the
                # existing receipt's outcome.
                return False

            if event.event_type is None:
                return True

            member_id = self._resolve_billing_member(session, event)
            if member_id is None:
                receipt.unresolved_reason = "unresolvable_member"
                session.flush()
                return True

            center_id = self._member_center_id(session, member_id)
            receipt.center_id = center_id
            applied, reason = self._apply_billing_event(
                session, member_id, center_id, event
            )
            if applied:
                receipt.applied = True
                receipt.unresolved_reason = None
                session.flush()
                self._reevaluate(session, member_id, BILLING_SYSTEM_ACTOR_ID)
            else:
                receipt.unresolved_reason = reason
                session.flush()
            return True

    def _apply_billing_event(
        self,
        session: Session,
        member_id: uuid.UUID,
        center_id: uuid.UUID,
        event: BillingWebhookEvent,
    ) -> tuple[bool, str | None]:
        record = self._subscription(session, member_id)
        if event.event_type is ProviderEventType.CHECKOUT_COMPLETED:
            self._sync_billing_customer(session, member_id, center_id, event)
            applied = self._transition_subscription(
                session,
                member_id=member_id,
                center_id=center_id,
                to_status=SubscriptionStatus.ACTIVE,
                provider="stripe",
                provider_subscription_id=event.provider_subscription_id,
                current_period_end=(
                    event.current_period_end
                    if event.current_period_end is not None
                    else (record.current_period_end if record is not None else None)
                ),
                cancel_at_period_end=(
                    record.cancel_at_period_end if record is not None else False
                ),
                grace_expires_at=None,
                source=BillingEventSource.STRIPE_WEBHOOK,
                reason_code=None,
                provider_event_id=event.provider_event_id,
                occurred_at=event.occurred_at,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                enforce_ordering=True,
            )
            return applied, (None if applied else "out_of_order_event")
        elif event.event_type is ProviderEventType.SUBSCRIPTION_UPDATED:
            status = self._normalize_provider_status(event.provider_status)
            grace_expires_at = (
                self._grace_window(record, event.occurred_at)
                if status is SubscriptionStatus.GRACE
                else None
            )
            applied = self._transition_subscription(
                session,
                member_id=member_id,
                center_id=center_id,
                to_status=status,
                provider="stripe",
                provider_subscription_id=event.provider_subscription_id,
                current_period_end=(
                    event.current_period_end
                    if event.current_period_end is not None
                    else (record.current_period_end if record is not None else None)
                ),
                cancel_at_period_end=event.cancel_at_period_end,
                grace_expires_at=grace_expires_at,
                source=BillingEventSource.STRIPE_WEBHOOK,
                reason_code=None,
                provider_event_id=event.provider_event_id,
                occurred_at=event.occurred_at,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                enforce_ordering=True,
            )
            return applied, (None if applied else "out_of_order_event")
        elif event.event_type is ProviderEventType.SUBSCRIPTION_DELETED:
            applied = self._transition_subscription(
                session,
                member_id=member_id,
                center_id=center_id,
                to_status=SubscriptionStatus.CANCELED,
                provider="stripe",
                provider_subscription_id=event.provider_subscription_id,
                current_period_end=(
                    event.current_period_end
                    if event.current_period_end is not None
                    else (record.current_period_end if record is not None else None)
                ),
                cancel_at_period_end=True,
                grace_expires_at=None,
                source=BillingEventSource.STRIPE_WEBHOOK,
                reason_code=None,
                provider_event_id=event.provider_event_id,
                occurred_at=event.occurred_at,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                enforce_ordering=True,
            )
            return applied, (None if applied else "out_of_order_event")
        elif event.event_type is ProviderEventType.INVOICE_PAID:
            applied = self._transition_subscription(
                session,
                member_id=member_id,
                center_id=center_id,
                to_status=SubscriptionStatus.ACTIVE,
                provider="stripe",
                provider_subscription_id=(
                    event.provider_subscription_id
                    or (record.provider_subscription_id if record is not None else None)
                ),
                current_period_end=(
                    event.current_period_end
                    if event.current_period_end is not None
                    else (record.current_period_end if record is not None else None)
                ),
                cancel_at_period_end=(
                    record.cancel_at_period_end if record is not None else False
                ),
                grace_expires_at=None,
                source=BillingEventSource.STRIPE_WEBHOOK,
                reason_code=None,
                provider_event_id=event.provider_event_id,
                occurred_at=event.occurred_at,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                enforce_ordering=True,
            )
            return applied, (None if applied else "out_of_order_event")
        elif event.event_type is ProviderEventType.INVOICE_PAYMENT_FAILED:
            if record is not None and record.status in (
                SubscriptionStatus.SUSPENDED.value,
                SubscriptionStatus.CANCELED.value,
            ):
                return False, "already_suspended_or_canceled"
            applied = self._transition_subscription(
                session,
                member_id=member_id,
                center_id=center_id,
                to_status=SubscriptionStatus.GRACE,
                provider="stripe",
                provider_subscription_id=(
                    event.provider_subscription_id
                    or (record.provider_subscription_id if record is not None else None)
                ),
                current_period_end=(
                    record.current_period_end if record is not None else None
                ),
                cancel_at_period_end=(
                    record.cancel_at_period_end if record is not None else False
                ),
                grace_expires_at=self._grace_window(record, event.occurred_at),
                source=BillingEventSource.STRIPE_WEBHOOK,
                reason_code=None,
                provider_event_id=event.provider_event_id,
                occurred_at=event.occurred_at,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                enforce_ordering=True,
            )
            return applied, (None if applied else "out_of_order_event")
        elif event.event_type is ProviderEventType.REFUND_ISSUED:
            self._audit(
                session,
                actor_id=BILLING_SYSTEM_ACTOR_ID,
                action="billing.refund_recorded",
                subject_id=member_id,
                center_id=center_id,
                metadata={
                    "amount_minor_units": event.amount_minor_units,
                    "currency": event.currency,
                },
            )
            session.add(
                OutboxMessageRecord(
                    event_type="billing.refund_recorded",
                    payload={
                        "member_id": str(member_id),
                        "amount_minor_units": event.amount_minor_units,
                    },
                )
            )
            return True, None
        return False, "unhandled_event_type"

    def _sync_billing_customer(
        self,
        session: Session,
        member_id: uuid.UUID,
        center_id: uuid.UUID,
        event: BillingWebhookEvent,
    ) -> None:
        if event.provider_customer_id is None:
            return
        customer = self._billing_customer(session, member_id)
        if customer is None:
            session.add(
                BillingCustomerRecord(
                    member_id=member_id,
                    center_id=center_id,
                    provider=event.provider,
                    provider_customer_id=event.provider_customer_id,
                )
            )
        elif customer.provider_customer_id != event.provider_customer_id:
            customer.provider_customer_id = event.provider_customer_id

    def _grace_window(
        self,
        record: SubscriptionRecord | None,
        occurred_at: datetime,
    ) -> datetime:
        if (
            record is not None
            and record.status == SubscriptionStatus.GRACE.value
            and record.grace_expires_at is not None
        ):
            return record.grace_expires_at
        return occurred_at + self._grace_period

    @staticmethod
    def _normalize_provider_status(provider_status: str | None) -> SubscriptionStatus:
        if provider_status in ("active", "trialing"):
            return SubscriptionStatus.ACTIVE
        if provider_status == "past_due":
            return SubscriptionStatus.GRACE
        if provider_status in ("canceled", "unpaid", "incomplete_expired"):
            return SubscriptionStatus.CANCELED
        if provider_status == "paused":
            return SubscriptionStatus.SUSPENDED
        return SubscriptionStatus.INCOMPLETE

    @staticmethod
    def _resolve_billing_member(
        session: Session,
        event: BillingWebhookEvent,
    ) -> uuid.UUID | None:
        if event.member_id is not None:
            return event.member_id
        if event.provider_customer_id is None:
            return None
        customer = session.scalar(
            select(BillingCustomerRecord).where(
                BillingCustomerRecord.provider == event.provider,
                BillingCustomerRecord.provider_customer_id
                == event.provider_customer_id,
            )
        )
        return customer.member_id if customer is not None else None

    def _transition_subscription(
        self,
        session: Session,
        *,
        member_id: uuid.UUID,
        center_id: uuid.UUID,
        to_status: SubscriptionStatus,
        provider: str,
        provider_subscription_id: str | None,
        current_period_end: datetime | None,
        cancel_at_period_end: bool,
        grace_expires_at: datetime | None,
        source: BillingEventSource,
        reason_code: str | None,
        provider_event_id: str | None,
        occurred_at: datetime,
        actor_id: uuid.UUID,
        enforce_ordering: bool = False,
    ) -> bool:
        plan = self._pilot_plan(session)
        record = self._subscription(session, member_id, for_update=True)
        now = self._now()
        if record is None:
            from_status = None
            session.add(
                SubscriptionRecord(
                    member_id=member_id,
                    center_id=center_id,
                    plan_id=plan.id,
                    status=to_status.value,
                    provider=provider,
                    provider_subscription_id=provider_subscription_id,
                    current_period_end=current_period_end,
                    cancel_at_period_end=cancel_at_period_end,
                    grace_expires_at=grace_expires_at,
                    last_provider_event_at=(occurred_at if enforce_ordering else None),
                    reason_code=reason_code,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            if (
                enforce_ordering
                and record.last_provider_event_at is not None
                and occurred_at < self._as_utc(record.last_provider_event_at)
            ):
                return False
            from_status = record.status
            record.status = to_status.value
            record.provider = provider
            if provider_subscription_id is not None:
                record.provider_subscription_id = provider_subscription_id
            record.current_period_end = current_period_end
            record.cancel_at_period_end = cancel_at_period_end
            record.grace_expires_at = grace_expires_at
            record.reason_code = reason_code
            record.updated_at = now
            if enforce_ordering:
                record.last_provider_event_at = occurred_at
        session.flush()
        session.add(
            EntitlementHistoryRecord(
                member_id=member_id,
                center_id=center_id,
                from_status=from_status,
                to_status=to_status.value,
                source=source.value,
                provider_event_id=provider_event_id,
                reason_code=reason_code,
                safe_metadata={
                    "current_period_end": (
                        current_period_end.isoformat()
                        if current_period_end is not None
                        else None
                    ),
                    "cancel_at_period_end": cancel_at_period_end,
                },
                occurred_at=occurred_at,
            )
        )
        self._audit(
            session,
            actor_id=actor_id,
            action="billing.entitlement_transitioned",
            subject_id=member_id,
            center_id=center_id,
            metadata={
                "from_status": from_status,
                "to_status": to_status.value,
                "source": source.value,
                "reason_code": reason_code,
            },
        )
        if from_status != to_status.value:
            session.add(
                OutboxMessageRecord(
                    event_type="billing.entitlement_changed",
                    payload={"member_id": str(member_id), "status": to_status.value},
                )
            )
        return True

    def _entitlement_view(
        self,
        session: Session,
        member_id: uuid.UUID,
    ) -> EntitlementView:
        plan = self._pilot_plan(session)
        record = self._subscription(session, member_id)
        now = self._now()
        if record is None:
            return EntitlementView(
                plan_name=plan.name,
                price_minor_units=plan.price_minor_units,
                currency=plan.currency,
                status=SubscriptionStatus.INCOMPLETE,
                active=False,
                current_period_end=None,
                cancel_at_period_end=False,
                grace_expires_at=None,
                has_provider_subscription=False,
                updated_at=None,
            )
        status = SubscriptionStatus(record.status)
        return EntitlementView(
            plan_name=plan.name,
            price_minor_units=plan.price_minor_units,
            currency=plan.currency,
            status=status,
            active=self._entitlement_active(
                status,
                record.current_period_end,
                record.grace_expires_at,
                now,
            ),
            current_period_end=record.current_period_end,
            cancel_at_period_end=record.cancel_at_period_end,
            grace_expires_at=record.grace_expires_at,
            has_provider_subscription=record.provider_subscription_id is not None,
            updated_at=record.updated_at,
        )

    def _billing_evidence(self, session: Session, member_id: uuid.UUID) -> bool:
        record = self._subscription(session, member_id)
        if record is None:
            return False
        return self._entitlement_active(
            SubscriptionStatus(record.status),
            record.current_period_end,
            record.grace_expires_at,
            self._now(),
        )

    def _pair_entitled(self, session: Session, proposal: MatchProposalRecord) -> bool:
        """Both participants of a matched pair must hold active entitlement.

        This is evaluated fresh on every access so a time-based grace or
        current-period expiry blocks direct operations immediately, without
        waiting for a webhook, admin action, or ``progress()`` call to
        reconcile it.
        """
        return self._billing_evidence(
            session, proposal.member_a_id
        ) and self._billing_evidence(session, proposal.member_b_id)

    def _reconcile_entitlement_lapse(
        self,
        session: Session,
        proposal: MatchProposalRecord,
        *,
        commit_immediately: bool = True,
    ) -> None:
        """Safely close a matched pair whose entitlement has lapsed.

        History is retained (the proposal row and its audit trail are never
        deleted); only its status transitions to closed so the pair loses
        access to further messaging and guided-journey activity.

        Callers that raise after detecting a lapse commit immediately so the
        surrounding transaction rollback does not discard the closure. Queue
        readers pass ``commit_immediately=False`` because they continue issuing
        queries and let their outer transaction commit all closures together.
        """
        if proposal.status not in _OPEN_PROPOSAL_STATUSES:
            return
        proposal.status = ProposalStatus.CLOSED.value
        proposal.closed_at = self._now()
        proposal.closed_reason = "entitlement_lapsed"
        self._close_proposal_audit(
            session, BILLING_SYSTEM_ACTOR_ID, proposal, "entitlement_lapsed"
        )
        if commit_immediately:
            session.commit()

    @classmethod
    def _entitlement_active(
        cls,
        status: SubscriptionStatus,
        current_period_end: datetime | None,
        grace_expires_at: datetime | None,
        now: datetime,
    ) -> bool:
        if status is SubscriptionStatus.COMPLIMENTARY:
            return True
        if status is SubscriptionStatus.ACTIVE:
            if current_period_end is not None and now > cls._as_utc(current_period_end):
                return False
            return True
        if status is SubscriptionStatus.GRACE:
            return grace_expires_at is not None and now <= cls._as_utc(grace_expires_at)
        return False

    @staticmethod
    def _ledger_entry(
        session: Session,
        record: CounselorEarningRecord,
    ) -> LedgerEntryView:
        counselor = session.get(UserRecord, record.counselor_id)
        return LedgerEntryView(
            id=record.id,
            counselor_id=record.counselor_id,
            counselor_name=counselor.name if counselor is not None else "Counselor",
            member_id=record.intake_member_id,
            entry_type=EarningEntryType(record.entry_type),
            amount_minor_units=record.amount_minor_units,
            currency=record.currency,
            reason_code=record.reason_code,
            created_at=record.created_at,
        )

    @staticmethod
    def _pilot_plan(session: Session) -> PilotPlanRecord:
        plan = session.scalar(
            select(PilotPlanRecord).where(PilotPlanRecord.is_active.is_(True))
        )
        if plan is None:
            raise NotFoundError("No active pilot plan is configured.")
        return plan

    @staticmethod
    def _subscription(
        session: Session,
        member_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> SubscriptionRecord | None:
        statement = select(SubscriptionRecord).where(
            SubscriptionRecord.member_id == member_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return session.scalar(statement)

    @staticmethod
    def _billing_customer(
        session: Session,
        member_id: uuid.UUID,
    ) -> BillingCustomerRecord | None:
        return session.scalar(
            select(BillingCustomerRecord).where(
                BillingCustomerRecord.member_id == member_id
            )
        )

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
            subscription_active=self._billing_evidence(session, member_id),
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
        subscription = self._subscription(session, member_id)
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
            "subscription_status": subscription.status if subscription else "none",
            "subscription_updated_at": (
                subscription.updated_at.isoformat() if subscription else "none"
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
        counselor_disabled = False
        if assignment is not None:
            counselor_status = session.scalar(
                select(UserRecord.status).where(
                    UserRecord.id == assignment.counselor_id
                )
            )
            counselor_disabled = counselor_status == AccountStatus.DISABLED.value
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
            account_status=AccountStatus(member.status),
            counselor_needs_reassignment=counselor_disabled,
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

    def _active_participant_proposal(
        self,
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
        if not self._pair_entitled(session, proposal):
            self._reconcile_entitlement_lapse(session, proposal)
            raise ConflictError("This matched-pair conversation is no longer active.")
        return proposal

    def _member_active_journey(
        self,
        session: Session,
        member: AuthenticatedUser,
        journey_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> PairJourneyRecord:
        statement = select(PairJourneyRecord).where(
            PairJourneyRecord.id == journey_id,
            PairJourneyRecord.center_id == member.center_id,
        )
        if lock:
            statement = statement.with_for_update()
        journey = session.scalar(statement)
        if journey is None:
            raise NotFoundError("Guided journey was not found.")
        self._active_participant_proposal(
            session,
            member,
            journey.proposal_id,
            lock=lock,
        )
        return journey

    def _active_counselor_proposal(
        self,
        session: Session,
        counselor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> MatchProposalRecord:
        statement = select(MatchProposalRecord).where(
            MatchProposalRecord.id == proposal_id,
            MatchProposalRecord.center_id == counselor.center_id,
        )
        if lock:
            statement = statement.with_for_update()
        proposal = session.scalar(statement)
        if proposal is None:
            raise NotFoundError("Matched pair was not found.")
        assigned_member_id = session.scalar(
            select(CounselorAssignmentRecord.member_id).where(
                CounselorAssignmentRecord.center_id == counselor.center_id,
                CounselorAssignmentRecord.counselor_id == counselor.id,
                CounselorAssignmentRecord.member_id.in_(
                    (proposal.member_a_id, proposal.member_b_id)
                ),
                CounselorAssignmentRecord.ended_at.is_(None),
            )
        )
        if assigned_member_id is None:
            raise NotFoundError("Matched pair was not found.")
        if proposal.status != ProposalStatus.ACTIVE.value:
            raise ConflictError("This matched pair is no longer active.")
        if not self._pair_entitled(session, proposal):
            self._reconcile_entitlement_lapse(session, proposal)
            raise ConflictError("This matched pair is no longer active.")
        return proposal

    def _member_journey_view(
        self,
        session: Session,
        proposal: MatchProposalRecord,
        journey: PairJourneyRecord,
        member_id: uuid.UUID,
    ) -> MemberJourneyView:
        template = session.get(JourneyTemplateRecord, journey.template_id)
        if template is None:
            raise NotFoundError("Journey curriculum was not found.")
        tasks = session.scalars(
            select(JourneyTemplateTaskRecord)
            .where(JourneyTemplateTaskRecord.template_id == template.id)
            .order_by(JourneyTemplateTaskRecord.sequence)
        ).all()
        completions = session.scalars(
            select(JourneyTaskCompletionRecord).where(
                JourneyTaskCompletionRecord.journey_id == journey.id,
                JourneyTaskCompletionRecord.center_id == journey.center_id,
            )
        ).all()
        completion_map = {
            (item.task_id, item.member_id): item.completed_at for item in completions
        }
        partner_id = (
            proposal.member_b_id
            if proposal.member_a_id == member_id
            else proposal.member_a_id
        )
        task_views = tuple(
            JourneyTaskView(
                id=task.id,
                sequence=task.sequence,
                title=task.title,
                description=task.description,
                scope=TaskScope(task.scope),
                due_day=task.due_day,
                completed_at=completion_map.get((task.id, member_id)),
                partner_completed_at=(
                    completion_map.get((task.id, partner_id))
                    if TaskScope(task.scope) is TaskScope.SHARED
                    else None
                ),
            )
            for task in tasks
        )
        check_ins = {
            CheckInMilestone(item.milestone): item
            for item in session.scalars(
                select(JourneyCheckInRecord).where(
                    JourneyCheckInRecord.journey_id == journey.id,
                    JourneyCheckInRecord.member_id == member_id,
                    JourneyCheckInRecord.center_id == journey.center_id,
                )
            )
        }
        now = self._now()
        started_at = self._as_utc(journey.started_at)
        check_in_views = tuple(
            self._member_check_in_view(
                milestone,
                started_at,
                check_ins.get(milestone),
                now,
            )
            for milestone in CheckInMilestone
        )
        return MemberJourneyView(
            id=journey.id,
            proposal_id=journey.proposal_id,
            template_name=template.name,
            template_version=template.version,
            started_at=started_at,
            tasks=task_views,
            check_ins=check_in_views,
        )

    @staticmethod
    def _member_check_in_view(
        milestone: CheckInMilestone,
        started_at: datetime,
        record: JourneyCheckInRecord | None,
        now: datetime,
    ) -> MemberCheckInView:
        due_at = started_at + timedelta(days=milestone.days)
        submitted_at = record.submitted_at if record is not None else None
        return MemberCheckInView(
            milestone=milestone,
            due_at=due_at,
            reminder_state=reminder_state(due_at, submitted_at, now),
            relationship_status=(
                RelationshipStatus(record.relationship_status)
                if record is not None
                else None
            ),
            support_requested=(
                record.support_requested if record is not None else False
            ),
            concern_flag=record.concern_flag if record is not None else False,
            private_reflection=(
                record.private_reflection if record is not None else ""
            ),
            share_with_counselor=(
                record.share_with_counselor if record is not None else False
            ),
            submitted_at=submitted_at,
        )

    def _counselor_journey_view(
        self,
        session: Session,
        proposal: MatchProposalRecord,
        journey: PairJourneyRecord | None,
        counselor: AuthenticatedUser,
    ) -> CounselorJourneyView:
        member_ids = (proposal.member_a_id, proposal.member_b_id)
        names = {
            member_id: self._display_name(session, member_id)
            for member_id in member_ids
        }
        if journey is None:
            return CounselorJourneyView(
                proposal_id=proposal.id,
                member_a_display_name=names[proposal.member_a_id],
                member_b_display_name=names[proposal.member_b_id],
                journey_id=None,
                template_name=None,
                template_version=None,
                started_at=None,
                members=(),
            )
        template = session.get(JourneyTemplateRecord, journey.template_id)
        if template is None:
            raise NotFoundError("Journey curriculum was not found.")
        task_ids = list(
            session.scalars(
                select(JourneyTemplateTaskRecord.id).where(
                    JourneyTemplateTaskRecord.template_id == template.id
                )
            )
        )
        completions = session.scalars(
            select(JourneyTaskCompletionRecord).where(
                JourneyTaskCompletionRecord.journey_id == journey.id,
                JourneyTaskCompletionRecord.center_id == counselor.center_id,
                JourneyTaskCompletionRecord.completed_at.is_not(None),
            )
        ).all()
        completed_counts = {
            member_id: sum(item.member_id == member_id for item in completions)
            for member_id in member_ids
        }
        check_ins = session.scalars(
            select(JourneyCheckInRecord).where(
                JourneyCheckInRecord.journey_id == journey.id,
                JourneyCheckInRecord.center_id == counselor.center_id,
            )
        ).all()
        check_in_map = {
            (item.member_id, CheckInMilestone(item.milestone)): item
            for item in check_ins
        }
        assignments = {
            member_id: self._active_counselor_assignment(session, member_id)
            for member_id in member_ids
        }
        my_member_ids = {
            member_id
            for member_id, assignment in assignments.items()
            if assignment is not None and assignment.counselor_id == counselor.id
        }
        now = self._now()
        started_at = self._as_utc(journey.started_at)
        members = tuple(
            self._counselor_journey_member_view(
                member_id,
                names[member_id],
                len(task_ids),
                completed_counts[member_id],
                member_id in my_member_ids,
                started_at,
                check_in_map,
                now,
            )
            for member_id in member_ids
        )
        return CounselorJourneyView(
            proposal_id=proposal.id,
            member_a_display_name=names[proposal.member_a_id],
            member_b_display_name=names[proposal.member_b_id],
            journey_id=journey.id,
            template_name=template.name,
            template_version=template.version,
            started_at=started_at,
            members=members,
        )

    @staticmethod
    def _counselor_journey_member_view(
        member_id: uuid.UUID,
        display_name: str,
        total_task_count: int,
        completed_task_count: int,
        is_my_member: bool,
        started_at: datetime,
        records: dict[
            tuple[uuid.UUID, CheckInMilestone],
            JourneyCheckInRecord,
        ],
        now: datetime,
    ) -> CounselorJourneyMemberView:
        check_ins: list[CounselorCheckInView] = []
        for milestone in CheckInMilestone:
            record = records.get((member_id, milestone))
            submitted_at = record.submitted_at if record is not None else None
            check_ins.append(
                CounselorCheckInView(
                    milestone=milestone,
                    due_at=started_at + timedelta(days=milestone.days),
                    reminder_state=reminder_state(
                        started_at + timedelta(days=milestone.days),
                        submitted_at,
                        now,
                    ),
                    submitted_at=submitted_at,
                    support_requested=(
                        record.support_requested
                        if record is not None and is_my_member
                        else None
                    ),
                    concern_flag=(
                        record.concern_flag
                        if record is not None and is_my_member
                        else None
                    ),
                    shared_reflection=(
                        record.private_reflection
                        if record is not None
                        and is_my_member
                        and record.share_with_counselor
                        else None
                    ),
                )
            )
        return CounselorJourneyMemberView(
            member_id=member_id,
            display_name=display_name,
            completed_task_count=completed_task_count,
            total_task_count=total_task_count,
            is_my_member=is_my_member,
            check_ins=tuple(check_ins),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

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
