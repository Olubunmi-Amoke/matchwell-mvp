import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from matchwell.domain.access import (
    AccountDisableReasonCode,
    AccountReactivateReasonCode,
    AuthenticatedUser,
    OidcIdentity,
    Role,
    normalize_email,
)
from matchwell.domain.alerts import AlertSnapshot
from matchwell.domain.analytics import PilotAnalyticsSnapshot
from matchwell.domain.billing import (
    AdminBillingRow,
    BillingPortalSessionView,
    BillingWebhookEvent,
    CheckoutSessionView,
    CounselorEarningsView,
    EntitlementView,
    LedgerEntryView,
    WebhookFailureView,
)
from matchwell.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from matchwell.domain.journey import (
    CheckInMilestone,
    CounselorJourneyView,
    MemberJourneyView,
    RelationshipStatus,
)
from matchwell.domain.matching import (
    MAX_PARTNER_AGE,
    MIN_PARTNER_AGE,
    BlockInput,
    CandidateGenerationDiagnostics,
    CandidateReviewItem,
    CounselorConversationStatus,
    CounselorReviewDecision,
    HistoricalRematchPair,
    IntroductionView,
    MatchedPairView,
    MatchPreferencesInput,
    MatchPreferencesView,
    MemberResponseDecision,
    MessageView,
    RematchAuthorizationView,
    RematchReasonCode,
    ReportInput,
    SafetyCategory,
    SelfPacedSuggestion,
    SuggestionInterestStatus,
)
from matchwell.domain.personality import (
    PersonalityAnswers,
    PersonalityInventoryView,
    PersonalityStatus,
)
from matchwell.domain.pilot import (
    AccountRow,
    AssessmentAnswers,
    AssessmentView,
    CommunityAssignmentReasonCode,
    CommunityCovenantView,
    CommunityView,
    ConsentView,
    CounselorDecisionStatus,
    DenominationCode,
    IntroductorySessionReasonCode,
    IntroductorySessionView,
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

ROLE_REASSIGNMENT_REASON_CODES = (
    "counselor-onboarding",
    "account-role-correction",
    "pilot-staffing-change",
)
COUNSELOR_TO_MEMBER_REASON_CODES = (
    "returning-to-member-journey",
    "account-role-correction",
    "pilot-staffing-change",
)


class PaymentGateway(Protocol):
    """Injected boundary to the payment provider.

    Keeping every Stripe-specific object behind this port lets repository and
    service tests use deterministic fakes and keeps provider objects out of
    the domain and other modules.
    """

    def create_checkout_session(
        self,
        *,
        member_id: uuid.UUID,
        member_email: str,
        existing_provider_customer_id: str | None,
    ) -> CheckoutSessionView: ...

    def create_billing_portal_session(
        self,
        *,
        provider_customer_id: str,
    ) -> BillingPortalSessionView: ...

    def verify_and_parse_webhook(
        self,
        *,
        payload: bytes,
        signature_header: str,
    ) -> BillingWebhookEvent: ...


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
        acknowledgement_keys: frozenset[str],
    ) -> None: ...

    def get_current_community_covenant(
        self, member_id: uuid.UUID
    ) -> CommunityCovenantView: ...

    def accept_community_covenant(
        self,
        member_id: uuid.UUID,
        covenant_definition_id: uuid.UUID,
        affirmation_keys: frozenset[str],
    ) -> None: ...

    def get_profile(self, member_id: uuid.UUID) -> ProfileInput | None: ...

    def save_profile(self, member_id: uuid.UUID, profile: ProfileInput) -> None: ...

    def introductory_session(
        self,
        member_id: uuid.UUID,
        center_id: uuid.UUID | None = None,
    ) -> IntroductorySessionView: ...

    def schedule_introductory_session(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        counselor_id: uuid.UUID,
        scheduled_at: datetime,
    ) -> IntroductorySessionView: ...

    def cancel_introductory_session(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: IntroductorySessionReasonCode,
    ) -> IntroductorySessionView: ...

    def complete_introductory_session(
        self, counselor: AuthenticatedUser, member_id: uuid.UUID
    ) -> IntroductorySessionView: ...

    def get_assessment(self, member_id: uuid.UUID) -> AssessmentView: ...

    def submit_assessment(
        self,
        member_id: uuid.UUID,
        assignment_id: uuid.UUID,
        answers: AssessmentAnswers,
    ) -> None: ...

    def get_personality_inventory(
        self, member_id: uuid.UUID
    ) -> PersonalityInventoryView: ...

    def submit_personality_inventory(
        self,
        member_id: uuid.UUID,
        assignment_id: uuid.UUID,
        answers: PersonalityAnswers,
    ) -> None: ...

    def personality_status(self, member_id: uuid.UUID) -> PersonalityStatus: ...

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

    def list_communities(self, actor: AuthenticatedUser) -> Sequence[CommunityView]: ...

    def assign_community(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        community_id: uuid.UUID,
        reason_code: CommunityAssignmentReasonCode,
    ) -> None: ...

    def reassign_member_to_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
    ) -> None: ...

    def reassign_counselor_to_member(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
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
        reason_code: ScreeningReasonCode | None,
    ) -> bool: ...

    def screening_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[ScreeningFailureView]: ...

    def analytics_snapshot(
        self,
        actor: AuthenticatedUser,
    ) -> PilotAnalyticsSnapshot: ...

    def alert_snapshot(self, actor: AuthenticatedUser) -> AlertSnapshot: ...

    def disable_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: str,
        admin_emails: frozenset[str],
    ) -> None: ...

    def reactivate_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: str,
    ) -> None: ...

    def list_accounts(self, actor: AuthenticatedUser) -> Sequence[AccountRow]: ...

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

    def list_self_paced_suggestions(
        self, member: AuthenticatedUser
    ) -> Sequence[SelfPacedSuggestion]: ...

    def set_suggestion_interest(
        self,
        member: AuthenticatedUser,
        candidate_member_id: uuid.UUID,
        status: SuggestionInterestStatus,
    ) -> uuid.UUID | None: ...

    def generate_candidates(self, actor: AuthenticatedUser) -> int: ...

    def list_historical_rematch_pairs(
        self, actor: AuthenticatedUser
    ) -> Sequence[HistoricalRematchPair]: ...

    def request_rematch_authorization(
        self,
        actor: AuthenticatedUser,
        member_a_id: uuid.UUID,
        member_b_id: uuid.UUID,
        reason_code: RematchReasonCode,
    ) -> uuid.UUID: ...

    def list_rematch_authorizations(
        self, actor: AuthenticatedUser
    ) -> Sequence[RematchAuthorizationView]: ...

    def approve_rematch_authorization(
        self, actor: AuthenticatedUser, authorization_id: uuid.UUID
    ) -> None: ...

    def candidate_generation_diagnostics(
        self,
        actor: AuthenticatedUser,
    ) -> CandidateGenerationDiagnostics: ...

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

    def list_recent_messages(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
        limit: int,
    ) -> Sequence[MessageView]: ...

    def send_message(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
        body: str,
    ) -> MessageView: ...

    def list_conversation_statuses(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CounselorConversationStatus]: ...

    def get_member_journey(
        self,
        member: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> MemberJourneyView | None: ...

    def assign_guided_journey(
        self,
        counselor: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> uuid.UUID: ...

    def set_journey_task_completion(
        self,
        member: AuthenticatedUser,
        journey_id: uuid.UUID,
        task_id: uuid.UUID,
        completed: bool,
    ) -> None: ...

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
    ) -> None: ...

    def list_counselor_journeys(
        self,
        counselor: AuthenticatedUser,
    ) -> Sequence[CounselorJourneyView]: ...

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

    def billing_status(self, member_id: uuid.UUID) -> EntitlementView: ...

    def create_checkout_session(
        self,
        actor: AuthenticatedUser,
    ) -> CheckoutSessionView: ...

    def create_billing_portal_session(
        self,
        actor: AuthenticatedUser,
    ) -> BillingPortalSessionView: ...

    def billing_queue(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[AdminBillingRow]: ...

    def billing_webhook_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[WebhookFailureView]: ...

    def grant_complimentary_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None: ...

    def suspend_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None: ...

    def counselor_earnings(
        self,
        actor: AuthenticatedUser,
    ) -> CounselorEarningsView: ...

    def ledger(self, actor: AuthenticatedUser) -> Sequence[LedgerEntryView]: ...

    def record_earnings_adjustment(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        amount_minor_units: int,
        reason_code: str,
    ) -> None: ...

    def process_billing_webhook_event(self, event: BillingWebhookEvent) -> bool: ...

    def process_screening_provider_event(
        self, event: ScreeningProviderEvent
    ) -> bool: ...


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
        acknowledgement_keys: frozenset[str] = frozenset(),
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        consent = self._repository.get_active_consent(actor.id)
        required = {item.key for item in consent.required_acknowledgements}
        if acknowledgement_keys != required:
            raise ValidationError("Accept every required acknowledgement exactly.")
        self._repository.accept_consent(
            actor.id, consent_version_id, acknowledgement_keys
        )

    def community_covenant(self, actor: AuthenticatedUser) -> CommunityCovenantView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_current_community_covenant(actor.id)

    def accept_community_covenant(
        self,
        actor: AuthenticatedUser,
        covenant_definition_id: uuid.UUID,
        affirmation_keys: frozenset[str] = frozenset(),
    ) -> None:
        """Accept only the complete current covenant, never sensitive proxies.

        Eligibility and matching must not use sexual orientation, attitudes
        toward LGBT people, or proxy attributes. The covenant records only
        explicit participation commitments and exact affirmation key names.
        """
        self._require_role(actor, Role.MEMBER)
        covenant = self._repository.get_current_community_covenant(actor.id)
        required = {item.key for item in covenant.required_affirmations}
        if covenant.id != covenant_definition_id:
            raise ValidationError("The covenant version is no longer current.")
        if affirmation_keys != required:
            raise ValidationError("Affirm every required commitment exactly.")
        self._repository.accept_community_covenant(
            actor.id, covenant_definition_id, affirmation_keys
        )

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
        if profile.denomination_code is DenominationCode.OTHER:
            if not profile.denomination_other or not profile.denomination_other.strip():
                raise ValidationError("Enter your denomination or church tradition.")
            if len(profile.denomination_other.strip()) > 100:
                raise ValidationError("Keep the denomination under 100 characters.")
        elif profile.denomination_other is not None:
            raise ValidationError("Other denomination text is only valid for Other.")
        self._repository.save_profile(actor.id, profile)

    def introductory_session(self, actor: AuthenticatedUser) -> IntroductorySessionView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.introductory_session(actor.id)

    def member_introductory_session(
        self, actor: AuthenticatedUser, member_id: uuid.UUID
    ) -> IntroductorySessionView:
        self._require_role(actor, Role.ADMIN)
        return self._repository.introductory_session(member_id, actor.center_id)

    def member_introductory_session_for_counselor(
        self, actor: AuthenticatedUser, member_id: uuid.UUID
    ) -> IntroductorySessionView:
        self._require_role(actor, Role.COUNSELOR)
        if not any(
            item.id == member_id
            for item in self._repository.list_assigned_members(actor)
        ):
            raise AuthorizationError("The member is not assigned to this counselor.")
        return self._repository.introductory_session(member_id, actor.center_id)

    def schedule_introductory_session(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        counselor_id: uuid.UUID,
        scheduled_at: datetime,
    ) -> IntroductorySessionView:
        self._require_role(actor, Role.ADMIN)
        if scheduled_at.tzinfo is None:
            raise ValidationError("Scheduled time must include a timezone.")
        if scheduled_at <= datetime.now(scheduled_at.tzinfo):
            raise ValidationError("Schedule the session for a future time.")
        return self._repository.schedule_introductory_session(
            actor, member_id, counselor_id, scheduled_at
        )

    def cancel_introductory_session(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: IntroductorySessionReasonCode,
    ) -> IntroductorySessionView:
        self._require_role(actor, Role.ADMIN)
        return self._repository.cancel_introductory_session(
            actor, member_id, reason_code
        )

    def complete_introductory_session(
        self, actor: AuthenticatedUser, member_id: uuid.UUID
    ) -> IntroductorySessionView:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.complete_introductory_session(actor, member_id)

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

    def personality_inventory(
        self, actor: AuthenticatedUser
    ) -> PersonalityInventoryView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_personality_inventory(actor.id)

    def submit_personality_inventory(
        self,
        actor: AuthenticatedUser,
        assignment_id: uuid.UUID,
        answers: PersonalityAnswers,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        if not answers or any(value < 1 or value > 5 for value in answers.values()):
            raise ValidationError("Answer every personality item from 1 to 5.")
        self._repository.submit_personality_inventory(actor.id, assignment_id, answers)

    def personality_status(self, actor: AuthenticatedUser) -> PersonalityStatus:
        self._require_role(actor, Role.MEMBER)
        return self._repository.personality_status(actor.id)

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

    def communities(self, actor: AuthenticatedUser) -> Sequence[CommunityView]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_communities(actor)

    def assign_community(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        community_id: uuid.UUID,
        reason_code: CommunityAssignmentReasonCode,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        if not isinstance(reason_code, CommunityAssignmentReasonCode):
            raise ValidationError("Select a valid community assignment reason.")
        self._repository.assign_community(actor, member_id, community_id, reason_code)

    def reassign_member_to_counselor(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        if member_id == actor.id:
            raise ValidationError("You cannot reassign your own administrator account.")
        if not confirmation_email.strip():
            raise ValidationError("Type the member's exact email to confirm.")
        try:
            confirmed_email = normalize_email(confirmation_email)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        safe_reason = reason_code.strip()
        if safe_reason not in ROLE_REASSIGNMENT_REASON_CODES:
            raise ValidationError("Select a valid role reassignment reason.")
        self._repository.reassign_member_to_counselor(
            actor,
            member_id,
            confirmed_email,
            safe_reason,
        )

    def reassign_counselor_to_member(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        confirmation_email: str,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        if counselor_id == actor.id:
            raise ValidationError("You cannot reassign your own administrator account.")
        if not confirmation_email.strip():
            raise ValidationError("Type the counselor's exact email to confirm.")
        try:
            confirmed_email = normalize_email(confirmation_email)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        safe_reason = reason_code.strip()
        if safe_reason not in COUNSELOR_TO_MEMBER_REASON_CODES:
            raise ValidationError("Select a valid role reassignment reason.")
        self._repository.reassign_counselor_to_member(
            actor,
            counselor_id,
            confirmed_email,
            safe_reason,
        )

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
        reason_code: ScreeningReasonCode | None = None,
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

    def screening_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[ScreeningFailureView]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.screening_failures(actor)

    def analytics(self, actor: AuthenticatedUser) -> PilotAnalyticsSnapshot:
        self._require_role(actor, Role.ADMIN)
        return self._repository.analytics_snapshot(actor)

    def alerts(self, actor: AuthenticatedUser) -> AlertSnapshot:
        self._require_role(actor, Role.ADMIN)
        return self._repository.alert_snapshot(actor)

    def accounts(self, actor: AuthenticatedUser) -> Sequence[AccountRow]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_accounts(actor)

    def disable_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: AccountDisableReasonCode,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        self._repository.disable_account(
            actor,
            target_user_id,
            reason_code.value,
            self._admin_emails,
        )

    def reactivate_account(
        self,
        actor: AuthenticatedUser,
        target_user_id: uuid.UUID,
        reason_code: AccountReactivateReasonCode,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        self._repository.reactivate_account(actor, target_user_id, reason_code.value)

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

    def self_paced_suggestions(
        self, actor: AuthenticatedUser
    ) -> Sequence[SelfPacedSuggestion]:
        self._require_role(actor, Role.MEMBER)
        return self._repository.list_self_paced_suggestions(actor)

    def set_suggestion_interest(
        self,
        actor: AuthenticatedUser,
        candidate_member_id: uuid.UUID,
        status: SuggestionInterestStatus,
    ) -> uuid.UUID | None:
        self._require_role(actor, Role.MEMBER)
        if status not in {
            SuggestionInterestStatus.INTERESTED,
            SuggestionInterestStatus.DISMISSED,
        }:
            raise ValidationError("Select interest or dismiss.")
        return self._repository.set_suggestion_interest(
            actor, candidate_member_id, status
        )

    def generate_candidates(self, actor: AuthenticatedUser) -> int:
        self._require_role(actor, Role.ADMIN)
        return self._repository.generate_candidates(actor)

    def historical_rematch_pairs(
        self, actor: AuthenticatedUser
    ) -> Sequence[HistoricalRematchPair]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.list_historical_rematch_pairs(actor)

    def request_rematch_authorization(
        self,
        actor: AuthenticatedUser,
        member_a_id: uuid.UUID,
        member_b_id: uuid.UUID,
        reason_code: RematchReasonCode,
    ) -> uuid.UUID:
        self._require_role(actor, Role.ADMIN)
        if member_a_id == member_b_id:
            raise ValidationError("Select two different members.")
        if not isinstance(reason_code, RematchReasonCode):
            raise ValidationError("Select a valid rematch reason.")
        return self._repository.request_rematch_authorization(
            actor, member_a_id, member_b_id, reason_code
        )

    def rematch_authorizations(
        self, actor: AuthenticatedUser
    ) -> Sequence[RematchAuthorizationView]:
        if actor.role not in {Role.ADMIN, Role.COUNSELOR}:
            raise AuthorizationError("This action is not available for your role.")
        return self._repository.list_rematch_authorizations(actor)

    def approve_rematch_authorization(
        self, actor: AuthenticatedUser, authorization_id: uuid.UUID
    ) -> None:
        self._require_role(actor, Role.COUNSELOR)
        self._repository.approve_rematch_authorization(actor, authorization_id)

    def candidate_generation_diagnostics(
        self,
        actor: AuthenticatedUser,
    ) -> CandidateGenerationDiagnostics:
        self._require_role(actor, Role.ADMIN)
        return self._repository.candidate_generation_diagnostics(actor)

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

    def recent_messages(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        limit: int = 50,
    ) -> Sequence[MessageView]:
        self._require_role(actor, Role.MEMBER)
        if not 1 <= limit <= 100:
            raise ValidationError("Message history limit must be between 1 and 100.")
        return self._repository.list_recent_messages(actor, proposal_id, limit)

    def send_message(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
        body: str,
    ) -> MessageView:
        self._require_role(actor, Role.MEMBER)
        safe_body = body.strip()
        if not safe_body:
            raise ValidationError("Enter a message before sending.")
        if len(safe_body) > 1000:
            raise ValidationError("Keep messages under 1,000 characters.")
        return self._repository.send_message(actor, proposal_id, safe_body)

    def conversation_statuses(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[CounselorConversationStatus]:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.list_conversation_statuses(actor)

    def guided_journey(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> MemberJourneyView | None:
        self._require_role(actor, Role.MEMBER)
        return self._repository.get_member_journey(actor, proposal_id)

    def assign_guided_journey(
        self,
        actor: AuthenticatedUser,
        proposal_id: uuid.UUID,
    ) -> uuid.UUID:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.assign_guided_journey(actor, proposal_id)

    def set_journey_task_completion(
        self,
        actor: AuthenticatedUser,
        journey_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        completed: bool,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        self._repository.set_journey_task_completion(
            actor,
            journey_id,
            task_id,
            completed,
        )

    def submit_journey_check_in(
        self,
        actor: AuthenticatedUser,
        journey_id: uuid.UUID,
        milestone: CheckInMilestone,
        relationship_status: RelationshipStatus,
        *,
        support_requested: bool,
        concern_flag: bool,
        private_reflection: str,
        share_with_counselor: bool,
    ) -> None:
        self._require_role(actor, Role.MEMBER)
        reflection = private_reflection.strip()
        if len(reflection) > 2000:
            raise ValidationError("Keep check-in reflections under 2,000 characters.")
        if share_with_counselor and not reflection:
            raise ValidationError("Enter a reflection before sharing it.")
        self._repository.submit_journey_check_in(
            actor,
            journey_id,
            milestone,
            relationship_status,
            support_requested,
            concern_flag,
            reflection,
            share_with_counselor,
        )

    def counselor_journeys(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[CounselorJourneyView]:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.list_counselor_journeys(actor)

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

    def billing_status(self, actor: AuthenticatedUser) -> EntitlementView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.billing_status(actor.id)

    def create_checkout_session(
        self,
        actor: AuthenticatedUser,
    ) -> CheckoutSessionView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.create_checkout_session(actor)

    def create_billing_portal_session(
        self,
        actor: AuthenticatedUser,
    ) -> BillingPortalSessionView:
        self._require_role(actor, Role.MEMBER)
        return self._repository.create_billing_portal_session(actor)

    def billing_queue(self, actor: AuthenticatedUser) -> Sequence[AdminBillingRow]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.billing_queue(actor)

    def billing_webhook_failures(
        self,
        actor: AuthenticatedUser,
    ) -> Sequence[WebhookFailureView]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.billing_webhook_failures(actor)

    def grant_complimentary_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        safe_reason = reason_code.strip()
        if not safe_reason:
            raise ValidationError("A safe reason code is required.")
        self._repository.grant_complimentary_entitlement(
            actor,
            member_id,
            safe_reason,
        )

    def suspend_entitlement(
        self,
        actor: AuthenticatedUser,
        member_id: uuid.UUID,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        safe_reason = reason_code.strip()
        if not safe_reason:
            raise ValidationError("A safe reason code is required.")
        self._repository.suspend_entitlement(actor, member_id, safe_reason)

    def counselor_earnings(self, actor: AuthenticatedUser) -> CounselorEarningsView:
        self._require_role(actor, Role.COUNSELOR)
        return self._repository.counselor_earnings(actor)

    def ledger(self, actor: AuthenticatedUser) -> Sequence[LedgerEntryView]:
        self._require_role(actor, Role.ADMIN)
        return self._repository.ledger(actor)

    def record_earnings_adjustment(
        self,
        actor: AuthenticatedUser,
        counselor_id: uuid.UUID,
        amount_minor_units: int,
        reason_code: str,
    ) -> None:
        self._require_role(actor, Role.ADMIN)
        safe_reason = reason_code.strip()
        if not safe_reason:
            raise ValidationError("A safe reason code is required.")
        if amount_minor_units == 0:
            raise ValidationError("Enter a non-zero adjustment amount.")
        self._repository.record_earnings_adjustment(
            actor,
            counselor_id,
            amount_minor_units,
            safe_reason,
        )

    def process_billing_webhook_event(self, event: BillingWebhookEvent) -> bool:
        return self._repository.process_billing_webhook_event(event)

    def process_screening_provider_event(self, event: ScreeningProviderEvent) -> bool:
        return self._repository.process_screening_provider_event(event)

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
