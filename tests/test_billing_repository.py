import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AccountStatus, AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.billing import (
    BillingPortalSessionView,
    BillingWebhookEvent,
    CheckoutSessionView,
    ProviderEventType,
    SubscriptionStatus,
)
from matchwell.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from matchwell.domain.journey import CheckInMilestone, RelationshipStatus, TaskScope
from matchwell.domain.matching import (
    CounselorReviewDecision,
    Gender,
    MatchPreferencesInput,
    MemberResponseDecision,
    ProposalStatus,
)
from matchwell.domain.pilot import (
    AccountRow,
    CounselorDecisionStatus,
    InvitationInput,
    ProfileInput,
    ScreeningStatus,
)
from matchwell.domain.readiness import ReadinessEvaluator
from matchwell.infrastructure.persistence.database import (
    Base,
    DatabaseSessionFactory,
    create_database_engine,
)
from matchwell.infrastructure.persistence.models import (
    AssessmentDefinitionRecord,
    AuditEventRecord,
    BillingCustomerRecord,
    BillingWebhookReceiptRecord,
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    CounselorEarningRecord,
    EntitlementHistoryRecord,
    JourneyTemplateRecord,
    JourneyTemplateTaskRecord,
    MatchProposalRecord,
    OutboxMessageRecord,
    PilotPlanRecord,
    SubscriptionRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)

Pilot = tuple[PilotService, DatabaseSessionFactory, "FakePaymentGateway"]


def _seed_pilot_center(session: Session) -> None:
    """Seed the Center, community, consent, assessment, and plan every test needs."""
    center_id = uuid.uuid4()
    session.add(
        CenterRecord(id=center_id, slug="matchwell-pilot", name="Matchwell Center")
    )
    session.add(
        CommunityRecord(
            id=uuid.uuid4(),
            center_id=center_id,
            slug="intentional-relationships",
            name="Intentional Relationships Community",
        )
    )
    session.add(
        ConsentVersionRecord(
            id=uuid.uuid4(),
            policy_key="pilot-participation",
            version="1.0",
            title="Pilot consent",
            body_markdown="Safe pilot consent.",
            effective_at=datetime.now(UTC),
            is_active=True,
        )
    )
    session.add(
        AssessmentDefinitionRecord(
            id=uuid.uuid4(),
            key="relationship-readiness",
            version="1.0",
            title="Readiness reflection",
            description="Reflect honestly.",
            questions=[
                {"id": "communication", "prompt": "I communicate clearly."},
                {"id": "faith", "prompt": "Faith guides my relationships."},
            ],
            is_active=True,
        )
    )
    session.add(
        PilotPlanRecord(
            id=uuid.uuid4(),
            key="matchwell-pilot",
            name="Matchwell Pilot",
            price_minor_units=4_900,
            currency="usd",
            is_active=True,
        )
    )
    template_id = uuid.uuid4()
    session.add(
        JourneyTemplateRecord(
            id=template_id,
            key="pilot-foundations",
            version="1.0",
            name="Pilot Foundations",
            description="A guided pilot curriculum.",
            is_active=True,
        )
    )
    session.add(
        JourneyTemplateTaskRecord(
            id=uuid.uuid4(),
            template_id=template_id,
            sequence=1,
            title="Activity 1",
            description="Complete activity 1.",
            scope=TaskScope.SHARED.value,
            due_day=7,
        )
    )


@dataclass
class FakePaymentGateway:
    """Deterministic double for the Stripe adapter; never calls the network."""

    checkout_calls: list[dict[str, object]] = field(default_factory=list)
    portal_calls: list[dict[str, object]] = field(default_factory=list)
    next_customer_id: str = "cus_fake_1"

    def create_checkout_session(
        self,
        *,
        member_id: uuid.UUID,
        member_email: str,
        existing_provider_customer_id: str | None,
    ) -> CheckoutSessionView:
        self.checkout_calls.append(
            {
                "member_id": member_id,
                "member_email": member_email,
                "existing_provider_customer_id": existing_provider_customer_id,
            }
        )
        customer_id = existing_provider_customer_id or self.next_customer_id
        return CheckoutSessionView(
            url="https://stripe.test/checkout/sess_1",
            provider_session_id="sess_1",
            provider_customer_id=customer_id,
        )

    def create_billing_portal_session(
        self,
        *,
        provider_customer_id: str,
    ) -> BillingPortalSessionView:
        self.portal_calls.append({"provider_customer_id": provider_customer_id})
        return BillingPortalSessionView(url="https://stripe.test/portal/1")

    def verify_and_parse_webhook(
        self,
        *,
        payload: bytes,
        signature_header: str,
    ) -> BillingWebhookEvent:
        raise NotImplementedError("Not exercised at the repository layer.")


@pytest.fixture
def pilot() -> Pilot:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
        _seed_pilot_center(session)
    gateway = FakePaymentGateway()
    repository = SqlAlchemyPilotRepository(
        sessions,
        ReadinessEvaluator(),
        payment_gateway=gateway,
        grace_period=timedelta(days=7),
    )
    service = PilotService(repository, frozenset({"admin@example.com"}))
    return service, sessions, gateway


def identity(email: str, subject: str) -> OidcIdentity:
    return OidcIdentity(
        issuer="https://accounts.google.com",
        subject=subject,
        email=email,
        email_verified=True,
        name=email.split("@")[0].title(),
    )


def _invite_and_sign_in(
    service: PilotService,
    admin: AuthenticatedUser,
    role: Role,
    email: str,
    subject: str,
) -> AuthenticatedUser:
    service.create_invitation(
        admin,
        InvitationInput(
            email=email,
            role=role,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    actor = service.sign_in(identity(email, subject))
    assert actor is not None
    return actor


def _checkout_event(
    *,
    member_id: uuid.UUID,
    occurred_at: datetime,
    provider_customer_id: str = "cus_1",
    provider_subscription_id: str = "sub_1",
    period_end: datetime | None = None,
) -> BillingWebhookEvent:
    return BillingWebhookEvent(
        provider="stripe",
        provider_event_id=f"evt-checkout-{uuid.uuid4()}",
        event_type=ProviderEventType.CHECKOUT_COMPLETED,
        occurred_at=occurred_at,
        provider_customer_id=provider_customer_id,
        provider_subscription_id=provider_subscription_id,
        member_id=member_id,
        provider_status=None,
        current_period_end=period_end,
        cancel_at_period_end=False,
        amount_minor_units=None,
        currency=None,
    )


def _subscription_updated_event(
    *,
    provider_customer_id: str,
    occurred_at: datetime,
    status: str,
    provider_subscription_id: str = "sub_1",
    cancel_at_period_end: bool = False,
    period_end: datetime | None = None,
    provider_event_id: str | None = None,
) -> BillingWebhookEvent:
    return BillingWebhookEvent(
        provider="stripe",
        provider_event_id=provider_event_id or f"evt-sub-{uuid.uuid4()}",
        event_type=ProviderEventType.SUBSCRIPTION_UPDATED,
        occurred_at=occurred_at,
        provider_customer_id=provider_customer_id,
        provider_subscription_id=provider_subscription_id,
        member_id=None,
        provider_status=status,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
        amount_minor_units=None,
        currency=None,
    )


def _invoice_event(
    *,
    event_type: ProviderEventType,
    provider_customer_id: str,
    occurred_at: datetime,
    provider_subscription_id: str = "sub_1",
) -> BillingWebhookEvent:
    return BillingWebhookEvent(
        provider="stripe",
        provider_event_id=f"evt-invoice-{uuid.uuid4()}",
        event_type=event_type,
        occurred_at=occurred_at,
        provider_customer_id=provider_customer_id,
        provider_subscription_id=provider_subscription_id,
        member_id=None,
        provider_status=None,
        current_period_end=None,
        cancel_at_period_end=False,
        amount_minor_units=4_900 if event_type is ProviderEventType.INVOICE_PAID else 0,
        currency="usd",
    )


# --- Member checkout, portal, and ownership -------------------------------


def test_member_checkout_and_portal_are_scoped_to_self(pilot: Pilot) -> None:
    service, sessions, gateway = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    with pytest.raises(NotFoundError):
        service.create_billing_portal_session(member)

    checkout = service.create_checkout_session(member)
    assert checkout.url.startswith("https://stripe.test/")
    assert gateway.checkout_calls[0]["member_id"] == member.id
    assert gateway.checkout_calls[0]["existing_provider_customer_id"] is None

    # A second checkout reuses the stored provider customer mapping.
    service.create_checkout_session(member)
    assert gateway.checkout_calls[1]["existing_provider_customer_id"] == (
        checkout.provider_customer_id
    )

    portal = service.create_billing_portal_session(member)
    assert portal.url.startswith("https://stripe.test/")

    with sessions.session() as session:
        mapping_count = session.scalar(
            select(func.count(BillingCustomerRecord.id)).where(
                BillingCustomerRecord.member_id == member.id
            )
        )
        assert mapping_count == 1


def test_only_members_can_manage_their_own_billing(pilot: Pilot) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )

    with pytest.raises(AuthorizationError):
        service.billing_status(admin)
    with pytest.raises(AuthorizationError):
        service.create_checkout_session(counselor)
    with pytest.raises(AuthorizationError):
        service.billing_queue(counselor)
    with pytest.raises(AuthorizationError):
        service.counselor_earnings(admin)
    with pytest.raises(AuthorizationError):
        service.ledger(counselor)


def test_checkout_requires_configured_payment_gateway() -> None:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
        _seed_pilot_center(session)
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    service = PilotService(repository, frozenset({"admin@example.com"}))
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    with pytest.raises(ValidationError):
        service.create_checkout_session(member)


# --- Center isolation and admin billing queue -----------------------------


def test_billing_queue_is_center_scoped(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    with sessions.session() as session, session.begin():
        other_center = CenterRecord(
            id=uuid.uuid4(), slug="other-center", name="Other Center"
        )
        session.add(other_center)
        session.flush()
        session.add(
            UserRecord(
                center_id=other_center.id,
                oidc_issuer="https://accounts.google.com",
                oidc_subject="other-member",
                email="other@example.com",
                name="Other Member",
                role=Role.MEMBER.value,
            )
        )

    rows = service.billing_queue(admin)
    assert [row.member_id for row in rows] == [member.id]


def test_billing_queue_and_ledger_access_audits_actually_persist(
    pilot: Pilot,
) -> None:
    """Regression: both read paths must commit their audit rows.

    They previously ran without ``session.begin()``, so the audit insert
    was silently rolled back on ``session.close()`` and never persisted.
    """
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    _invite_and_sign_in(service, admin, Role.MEMBER, "member@example.com", "member-sub")

    service.billing_queue(admin)
    service.ledger(admin)

    with sessions.session() as session:
        actions = set(
            session.scalars(
                select(AuditEventRecord.action).where(
                    AuditEventRecord.action.in_(
                        ("billing.queue_accessed", "billing.ledger_accessed")
                    )
                )
            ).all()
        )
    assert actions == {"billing.queue_accessed", "billing.ledger_accessed"}


# --- Complimentary grants and manual corrections --------------------------


def test_complimentary_grant_unlocks_entitlement_and_is_reasoned(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    with pytest.raises(ValidationError):
        service.grant_complimentary_entitlement(admin, member.id, "  ")

    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.COMPLIMENTARY
    assert status.active

    with sessions.session() as session:
        history = session.scalars(
            select(EntitlementHistoryRecord).where(
                EntitlementHistoryRecord.member_id == member.id
            )
        ).all()
        assert len(history) == 1
        assert history[0].source == "admin_complimentary"
        assert history[0].reason_code == "pilot-migration"


def test_suspend_entitlement_requires_reason_and_blocks_access(pilot: Pilot) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")

    with pytest.raises(ValidationError):
        service.suspend_entitlement(admin, member.id, "")

    service.suspend_entitlement(admin, member.id, "fraud-review")
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.SUSPENDED
    assert not status.active
    assert not service.progress(member).readiness.eligible


def _ready_member(
    service: PilotService,
    admin: AuthenticatedUser,
    counselor: AuthenticatedUser,
    email: str,
    subject: str,
    name: str,
) -> AuthenticatedUser:
    member = _invite_and_sign_in(service, admin, Role.MEMBER, email, subject)
    service.save_profile(
        member,
        ProfileInput(
            display_name=name,
            birth_date=date(1990, 1, 1),
            faith_affirmed=True,
            relationship_intent="A healthy Christian marriage.",
            denomination="",
            city="Nashville",
            state="Tennessee",
        ),
    )
    consent = service.consent(member)
    service.accept_consent(member, consent.id)
    assessment = service.assessment(member)
    service.submit_assessment(
        member,
        assessment.assignment_id,
        {question.id: 4 for question in assessment.questions},
    )
    service.assign_counselor(admin, member.id, counselor.id)
    service.record_counselor_decision(
        counselor, member.id, CounselorDecisionStatus.APPROVED
    )
    service.record_screening_status(
        admin,
        member.id,
        ScreeningStatus.ELIGIBLE,
        f"evt-{subject}",
        f"case-{subject}",
    )
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")
    service.save_match_preferences(
        member,
        MatchPreferencesInput(
            gender=Gender.MAN if name == "Alex" else Gender.WOMAN,
            min_partner_age=25,
            max_partner_age=45,
        ),
    )
    assert service.progress(member).readiness.eligible
    return member


def _ready_matched_pair(
    service: PilotService,
    sessions: DatabaseSessionFactory,
    admin: AuthenticatedUser,
    counselor: AuthenticatedUser,
) -> tuple[AuthenticatedUser, AuthenticatedUser, uuid.UUID]:
    """Two complimentary, fully-ready members in an ACTIVE matched pair."""
    member_a = _ready_member(
        service, admin, counselor, "alex@example.com", "alex-sub", "Alex"
    )
    member_b = _ready_member(
        service, admin, counselor, "brooke@example.com", "brooke-sub", "Brooke"
    )
    with sessions.session() as session, session.begin():
        member_a_id, member_b_id = sorted((member_a.id, member_b.id), key=str)
        community_id = session.scalar(select(CommunityRecord.id))
        proposal = MatchProposalRecord(
            center_id=admin.center_id,
            community_id=community_id,
            member_a_id=member_a_id,
            member_b_id=member_b_id,
            status=ProposalStatus.ACTIVE.value,
            score=100,
            score_breakdown=[],
            counselor_a_id=counselor.id,
            counselor_a_decision="approved",
            counselor_b_id=counselor.id,
            counselor_b_decision="approved",
            activated_at=datetime.now(UTC),
        )
        session.add(proposal)
        session.flush()
        proposal_id = proposal.id
    return member_a, member_b, proposal_id


def _force_subscription_state(
    sessions: DatabaseSessionFactory,
    member_id: uuid.UUID,
    *,
    status: SubscriptionStatus,
    current_period_end: datetime | None = None,
    grace_expires_at: datetime | None = None,
) -> None:
    """Directly downgrade a member's subscription row to simulate the mere
    passage of time (no new webhook, admin action, or ``progress()`` call).
    """
    with sessions.session() as session, session.begin():
        record = session.scalar(
            select(SubscriptionRecord).where(SubscriptionRecord.member_id == member_id)
        )
        assert record is not None
        record.status = status.value
        record.current_period_end = current_period_end
        record.grace_expires_at = grace_expires_at


def test_entitlement_loss_closes_active_matched_pair(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, member_b, _ = _ready_matched_pair(service, sessions, admin, counselor)

    service.suspend_entitlement(admin, member_a.id, "payment-issue")
    # Reading progress is the correctness backstop that reconciles access.
    assert not service.progress(member_a).readiness.eligible

    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value


def test_expired_grace_blocks_messaging_directly_without_progress_call(
    pilot: Pilot,
) -> None:
    """Time-based grace expiry must block direct operations by itself.

    No webhook, admin action, or ``progress()`` call reconciles the state
    here: the subscription row is force-downgraded to simulate the grace
    deadline having quietly elapsed, and messaging is exercised directly.
    """
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, member_b, proposal_id = _ready_matched_pair(
        service, sessions, admin, counselor
    )

    now = datetime.now(UTC)
    _force_subscription_state(
        sessions,
        member_a.id,
        status=SubscriptionStatus.GRACE,
        current_period_end=now - timedelta(days=10),
        grace_expires_at=now - timedelta(hours=1),
    )

    with pytest.raises(ConflictError):
        service.send_message(member_a, proposal_id, "Are you there?")
    with pytest.raises(ConflictError):
        service.recent_messages(member_b, proposal_id)

    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value
        assert proposal.closed_reason == "entitlement_lapsed"

    # The pair is reconciled for both participants, not just the lapsed one.
    assert service.matched_pair(member_a) is None
    assert service.matched_pair(member_b) is None


def test_expired_current_period_end_blocks_journey_directly(pilot: Pilot) -> None:
    """Time-based current-period expiry must block direct journey access."""
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, member_b, proposal_id = _ready_matched_pair(
        service, sessions, admin, counselor
    )
    # Grant a webhook-driven ACTIVE subscription (rather than complimentary,
    # which never expires) so a real current-period expiry can be simulated.
    _force_subscription_state(
        sessions,
        member_b.id,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=datetime.now(UTC) + timedelta(days=30),
    )
    journey_id = service.assign_guided_journey(counselor, proposal_id)
    assert service.guided_journey(member_a, proposal_id) is not None

    _force_subscription_state(
        sessions,
        member_b.id,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(ConflictError):
        service.guided_journey(member_a, proposal_id)
    with pytest.raises(ConflictError):
        service.set_journey_task_completion(
            member_a,
            journey_id,
            uuid.uuid4(),
            completed=True,
        )
    with pytest.raises(ConflictError):
        service.submit_journey_check_in(
            member_a,
            journey_id,
            CheckInMilestone.DAY_30,
            RelationshipStatus.STEADY,
            support_requested=False,
            concern_flag=False,
            private_reflection="",
            share_with_counselor=False,
        )

    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value


def test_counselor_views_reconcile_lapsed_pairs(pilot: Pilot) -> None:
    """Counselor conversation/journey listings drop entitlement-lapsed pairs."""
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, member_b, proposal_id = _ready_matched_pair(
        service, sessions, admin, counselor
    )
    service.send_message(member_a, proposal_id, "Hello!")
    assert len(service.conversation_statuses(counselor)) == 1

    _force_subscription_state(
        sessions,
        member_a.id,
        status=SubscriptionStatus.SUSPENDED,
    )

    assert service.conversation_statuses(counselor) == []
    assert service.counselor_journeys(counselor) == []
    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value
        assert proposal.closed_reason == "entitlement_lapsed"


def test_counselor_listing_continues_after_reconciling_lapsed_pair(
    pilot: Pilot,
) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    lapsed_member, _, lapsed_proposal_id = _ready_matched_pair(
        service, sessions, admin, counselor
    )
    member_c = _ready_member(
        service, admin, counselor, "casey@example.com", "casey-sub", "Casey"
    )
    member_d = _ready_member(
        service, admin, counselor, "devon@example.com", "devon-sub", "Devon"
    )
    with sessions.session() as session, session.begin():
        member_c_id, member_d_id = sorted((member_c.id, member_d.id), key=str)
        community_id = session.scalar(select(CommunityRecord.id))
        active_proposal = MatchProposalRecord(
            center_id=admin.center_id,
            community_id=community_id,
            member_a_id=member_c_id,
            member_b_id=member_d_id,
            status=ProposalStatus.ACTIVE.value,
            score=90,
            score_breakdown=[],
            counselor_a_id=counselor.id,
            counselor_a_decision=CounselorReviewDecision.APPROVED.value,
            counselor_b_id=counselor.id,
            counselor_b_decision=CounselorReviewDecision.APPROVED.value,
            activated_at=datetime.now(UTC) - timedelta(days=1),
        )
        session.add(active_proposal)
        session.flush()
        active_proposal_id = active_proposal.id
    _force_subscription_state(
        sessions,
        lapsed_member.id,
        status=SubscriptionStatus.SUSPENDED,
    )

    statuses = service.conversation_statuses(counselor)

    assert [status.proposal_id for status in statuses] == [active_proposal_id]
    with sessions.session() as session:
        lapsed_proposal = session.get(MatchProposalRecord, lapsed_proposal_id)
        assert lapsed_proposal is not None
        assert lapsed_proposal.status == ProposalStatus.CLOSED.value
        assert lapsed_proposal.closed_reason == "entitlement_lapsed"


def test_expired_entitlement_blocks_pending_candidate_review(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, _, proposal_id = _ready_matched_pair(service, sessions, admin, counselor)
    with sessions.session() as session, session.begin():
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        proposal.status = ProposalStatus.PENDING_REVIEW.value
        proposal.counselor_a_decision = CounselorReviewDecision.PENDING.value
        proposal.counselor_b_decision = CounselorReviewDecision.PENDING.value
        proposal.activated_at = None
    _force_subscription_state(
        sessions,
        member_a.id,
        status=SubscriptionStatus.GRACE,
        grace_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert service.candidate_queue(counselor) == ()
    with pytest.raises(ConflictError):
        service.review_candidate(
            counselor,
            proposal_id,
            CounselorReviewDecision.APPROVED,
        )

    with sessions.session() as session:
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value
        assert proposal.closed_reason == "entitlement_lapsed"


def test_expired_entitlement_blocks_introduction_access(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member_a, member_b, proposal_id = _ready_matched_pair(
        service, sessions, admin, counselor
    )
    with sessions.session() as session, session.begin():
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        proposal.status = ProposalStatus.INTRODUCED.value
        proposal.member_a_response = None
        proposal.member_b_response = None
        proposal.activated_at = None
        proposal.introduced_at = datetime.now(UTC)
    _force_subscription_state(
        sessions,
        member_b.id,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert service.introduction(member_a) is None
    with pytest.raises(ConflictError):
        service.respond_to_introduction(
            member_b,
            proposal_id,
            MemberResponseDecision.ACCEPTED,
        )

    with sessions.session() as session:
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        assert proposal.status == ProposalStatus.CLOSED.value
        assert proposal.closed_reason == "entitlement_lapsed"


def test_missing_subscription_row_is_never_treated_as_entitled(pilot: Pilot) -> None:
    """A member with no subscription row at all must never pass the gate.

    Test fixtures elsewhere always grant an explicit complimentary
    entitlement for "ready" members; this proves that omission is not a
    silent, accidental way to weaken the gate; a bare ``INCOMPLETE`` status
    (the default when no row exists) is inactive, matching production.
    """
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    with sessions.session() as session:
        assert (
            session.scalar(
                select(func.count(SubscriptionRecord.id)).where(
                    SubscriptionRecord.member_id == member.id
                )
            )
            == 0
        )

    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.INCOMPLETE
    assert not status.active


# --- Stripe webhook idempotency, ordering, and state machine --------------


def test_webhook_checkout_completed_is_exactly_once(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    now = datetime.now(UTC)
    event = _checkout_event(member_id=member.id, occurred_at=now)

    assert service.process_billing_webhook_event(event)
    assert not service.process_billing_webhook_event(event)  # duplicate event ID

    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.ACTIVE
    assert status.active
    assert status.has_provider_subscription

    with sessions.session() as session:
        history_count = session.scalar(
            select(func.count(EntitlementHistoryRecord.id)).where(
                EntitlementHistoryRecord.member_id == member.id
            )
        )
        assert history_count == 1


def test_webhook_recovery_after_grace_restores_active_without_duplicate_history(
    pilot: Pilot,
) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )
    service.process_billing_webhook_event(
        _invoice_event(
            event_type=ProviderEventType.INVOICE_PAYMENT_FAILED,
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=1),
        )
    )
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.GRACE
    assert status.active  # still within the 7-day grace window
    assert status.grace_expires_at is not None

    service.process_billing_webhook_event(
        _invoice_event(
            event_type=ProviderEventType.INVOICE_PAID,
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=2),
        )
    )
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.ACTIVE
    assert status.active
    assert status.grace_expires_at is None

    with sessions.session() as session:
        history_count = session.scalar(
            select(func.count(EntitlementHistoryRecord.id)).where(
                EntitlementHistoryRecord.member_id == member.id
            )
        )
        # checkout -> active, active -> grace, grace -> active.
        assert history_count == 3


def test_webhook_grace_expiry_suspends_access_dynamically() -> None:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
        _seed_pilot_center(session)
    # A negative grace period means any payment failure is already past its
    # deadline by the time it is processed, without needing out-of-order
    # timestamps to simulate the passage of time.
    repository = SqlAlchemyPilotRepository(
        sessions,
        ReadinessEvaluator(),
        payment_gateway=FakePaymentGateway(),
        grace_period=timedelta(days=-1),
    )
    service = PilotService(repository, frozenset({"admin@example.com"}))
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )
    service.process_billing_webhook_event(
        _invoice_event(
            event_type=ProviderEventType.INVOICE_PAYMENT_FAILED,
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=1),
        )
    )
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.GRACE
    # The grace deadline has already elapsed; the correctness backstop
    # treats access as inactive without waiting for a scheduled
    # reconciliation job.
    assert not status.active
    assert not service.progress(member).readiness.eligible


def test_webhook_out_of_order_event_does_not_regress_state(pilot: Pilot) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=2),
            status="active",
        )
    )
    # A stale, out-of-order "past_due" event with an earlier timestamp must
    # not regress the already-applied newer "active" state.
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=1),
            status="past_due",
        )
    )
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.ACTIVE
    assert status.grace_expires_at is None


def test_webhook_scheduled_cancellation_retains_access_through_period_end(
    pilot: Pilot,
) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )
    future_period_end = now + timedelta(days=15)
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=1),
            status="active",
            cancel_at_period_end=True,
            period_end=future_period_end,
        )
    )
    status = service.billing_status(member)
    assert status.cancel_at_period_end
    assert status.active
    assert status.current_period_end is not None
    assert status.current_period_end.replace(tzinfo=UTC) == future_period_end

    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=2),
            status="canceled",
            period_end=future_period_end,
        )
    )
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.CANCELED
    assert not status.active


def test_webhook_for_unresolvable_customer_is_ignored_safely(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    event = _subscription_updated_event(
        provider_customer_id="cus_unknown",
        occurred_at=datetime.now(UTC),
        status="active",
    )
    assert service.process_billing_webhook_event(event)
    with sessions.session() as session:
        assert session.scalar(select(func.count(SubscriptionRecord.id))) == 0
        receipt = session.scalar(select(BillingWebhookReceiptRecord))
        assert receipt is not None
        # Acknowledged (so Stripe does not retry forever), but never
        # mis-recorded as applied: nothing actually changed.
        assert receipt.applied is False
        assert receipt.unresolved_reason == "unresolvable_member"


def test_checkout_event_preserves_newer_subscription_period_and_cancellation(
    pilot: Pilot,
) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    period_end = now + timedelta(days=30)
    service.process_billing_webhook_event(
        _checkout_event(
            member_id=member.id,
            occurred_at=now - timedelta(seconds=1),
        )
    )
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now,
            status="active",
            cancel_at_period_end=True,
            period_end=period_end,
        )
    )

    service.process_billing_webhook_event(
        _checkout_event(
            member_id=member.id,
            occurred_at=now + timedelta(seconds=1),
        )
    )

    entitlement = service.billing_status(member)
    assert entitlement.active
    assert entitlement.current_period_end is not None
    assert entitlement.current_period_end.replace(tzinfo=UTC) == period_end
    assert entitlement.cancel_at_period_end


def test_webhook_receipt_lifecycle_tracks_applied_and_reason(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)

    checkout_event = _checkout_event(member_id=member.id, occurred_at=now)
    assert service.process_billing_webhook_event(checkout_event)
    with sessions.session() as session:
        receipt = session.scalar(
            select(BillingWebhookReceiptRecord).where(
                BillingWebhookReceiptRecord.provider_event_id
                == checkout_event.provider_event_id
            )
        )
        assert receipt is not None
        assert receipt.applied is True
        assert receipt.unresolved_reason is None

    # An unrecognized event type is acknowledged but never claims to apply.
    unrecognized = BillingWebhookEvent(
        provider="stripe",
        provider_event_id="evt-unrecognized-1",
        event_type=None,
        occurred_at=now + timedelta(minutes=1),
        provider_customer_id=None,
        provider_subscription_id=None,
        member_id=None,
        provider_status=None,
        current_period_end=None,
        cancel_at_period_end=False,
        amount_minor_units=None,
        currency=None,
    )
    assert service.process_billing_webhook_event(unrecognized)
    with sessions.session() as session:
        receipt = session.scalar(
            select(BillingWebhookReceiptRecord).where(
                BillingWebhookReceiptRecord.provider_event_id == "evt-unrecognized-1"
            )
        )
        assert receipt is not None
        assert receipt.applied is False
        assert receipt.unresolved_reason == "unrecognized_event_type"

    # A stale, out-of-order subscription update is acknowledged but not
    # applied: the newer state must never regress.
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=2),
            status="active",
        )
    )
    stale_event = _subscription_updated_event(
        provider_customer_id="cus_1",
        occurred_at=now + timedelta(hours=1),
        status="past_due",
    )
    assert service.process_billing_webhook_event(stale_event)
    with sessions.session() as session:
        receipt = session.scalar(
            select(BillingWebhookReceiptRecord).where(
                BillingWebhookReceiptRecord.provider_event_id
                == stale_event.provider_event_id
            )
        )
        assert receipt is not None
        assert receipt.applied is False
        assert receipt.unresolved_reason == "out_of_order_event"

    # Duplicate delivery of an already-recorded event ID is rejected without
    # touching the original receipt's outcome.
    assert not service.process_billing_webhook_event(checkout_event)


def test_admin_webhook_failures_queue_lists_unapplied_receipts_only(
    pilot: Pilot,
) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)

    applied_event = _checkout_event(member_id=member.id, occurred_at=now)
    service.process_billing_webhook_event(applied_event)
    # A newer state is applied first, then a stale, out-of-order update for
    # the same (resolvable) customer never regresses -- an unapplied
    # failure this Center's admin can see because it resolves to this
    # Center via the member.
    service.process_billing_webhook_event(
        _subscription_updated_event(
            provider_customer_id="cus_1",
            occurred_at=now + timedelta(hours=2),
            status="active",
        )
    )
    stale_event = _subscription_updated_event(
        provider_customer_id="cus_1",
        occurred_at=now + timedelta(hours=1),
        status="past_due",
    )
    service.process_billing_webhook_event(stale_event)
    # An event whose customer never resolves to any member has no Center to
    # scope it to; it must never be exposed to any Center admin.
    unresolvable_event = _subscription_updated_event(
        provider_customer_id="cus_unknown",
        occurred_at=now,
        status="active",
    )
    service.process_billing_webhook_event(unresolvable_event)

    with pytest.raises(AuthorizationError):
        service.billing_webhook_failures(counselor)

    failures = service.billing_webhook_failures(admin)
    assert [f.provider_event_id for f in failures] == [stale_event.provider_event_id]
    assert failures[0].unresolved_reason == "out_of_order_event"
    # The successfully applied checkout event never shows up as a failure,
    # and the unresolvable-member event is never exposed to any admin.
    failure_ids = [f.provider_event_id for f in failures]
    assert applied_event.provider_event_id not in failure_ids
    assert unresolvable_event.provider_event_id not in failure_ids


def test_webhook_refund_is_recorded_without_altering_status(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )
    refund_event = BillingWebhookEvent(
        provider="stripe",
        provider_event_id="evt-refund-1",
        event_type=ProviderEventType.REFUND_ISSUED,
        occurred_at=now + timedelta(hours=1),
        provider_customer_id="cus_1",
        provider_subscription_id=None,
        member_id=None,
        provider_status=None,
        current_period_end=None,
        cancel_at_period_end=False,
        amount_minor_units=4_900,
        currency="usd",
    )
    assert service.process_billing_webhook_event(refund_event)
    status = service.billing_status(member)
    assert status.status is SubscriptionStatus.ACTIVE

    with sessions.session() as session:
        payload = session.scalar(
            select(OutboxMessageRecord.payload).where(
                OutboxMessageRecord.event_type == "billing.refund_recorded"
            )
        )
        assert payload == {"member_id": str(member.id), "amount_minor_units": 4_900}
        receipt = session.scalar(
            select(BillingWebhookReceiptRecord).where(
                BillingWebhookReceiptRecord.provider_event_id == "evt-refund-1"
            )
        )
        assert receipt is not None
        assert receipt.applied is True


# --- Counselor earnings ledger ---------------------------------------------


def test_intake_credit_is_exactly_once_and_survives_reassignment(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor_a = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-a@example.com", "counselor-a-sub"
    )
    counselor_b = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-b@example.com", "counselor-b-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.assign_counselor(admin, member.id, counselor_a.id)
    service.record_counselor_decision(
        counselor_a, member.id, CounselorDecisionStatus.APPROVED
    )

    earnings_a = service.counselor_earnings(counselor_a)
    assert earnings_a.balance_minor_units == 2_500
    assert len(earnings_a.entries) == 1

    # Reassign to a different counselor and record another decision for the
    # same member: no second credit is created, and it never transfers.
    service.assign_counselor(admin, member.id, counselor_b.id)
    service.record_counselor_decision(
        counselor_b, member.id, CounselorDecisionStatus.APPROVED
    )

    assert service.counselor_earnings(counselor_a).balance_minor_units == 2_500
    assert service.counselor_earnings(counselor_b).balance_minor_units == 0

    with sessions.session() as session:
        credit_count = session.scalar(
            select(func.count(CounselorEarningRecord.id)).where(
                CounselorEarningRecord.entry_type == "intake_credit"
            )
        )
        assert credit_count == 1


def test_pending_decision_does_not_credit_earnings(pilot: Pilot) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.assign_counselor(admin, member.id, counselor.id)
    service.record_counselor_decision(
        counselor, member.id, CounselorDecisionStatus.PENDING
    )

    assert service.counselor_earnings(counselor).balance_minor_units == 0


def test_admin_adjustment_is_append_only_and_reasoned(pilot: Pilot) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )

    with pytest.raises(ValidationError):
        service.record_earnings_adjustment(admin, counselor.id, 0, "correction")
    with pytest.raises(ValidationError):
        service.record_earnings_adjustment(admin, counselor.id, 500, "  ")

    service.record_earnings_adjustment(admin, counselor.id, -500, "overpaid-credit")
    service.record_earnings_adjustment(admin, counselor.id, 1_000, "bonus")

    earnings = service.counselor_earnings(counselor)
    assert earnings.balance_minor_units == 500
    assert len(earnings.entries) == 2

    ledger = service.ledger(admin)
    assert len(ledger) == 2

    with sessions.session() as session:
        rows = session.scalars(select(CounselorEarningRecord)).all()
        assert all(row.entry_type == "admin_adjustment" for row in rows)
        # Append-only: no update columns exist and every row keeps its
        # original amount/reason forever.
        assert {row.reason_code for row in rows} == {"overpaid-credit", "bonus"}


def test_counselor_only_sees_own_earnings(pilot: Pilot) -> None:
    service, _, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor_a = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-a@example.com", "counselor-a-sub"
    )
    counselor_b = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-b@example.com", "counselor-b-sub"
    )
    service.record_earnings_adjustment(admin, counselor_a.id, 1_000, "bonus")

    assert service.counselor_earnings(counselor_a).balance_minor_units == 1_000
    assert service.counselor_earnings(counselor_b).balance_minor_units == 0


# --- Safe audit/outbox payloads (no secrets or payment instruments) --------


def test_billing_audit_and_outbox_never_contain_secrets_or_card_data(
    pilot: Pilot,
) -> None:
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.create_checkout_session(member)
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")
    now = datetime.now(UTC)
    service.process_billing_webhook_event(
        _checkout_event(member_id=member.id, occurred_at=now)
    )

    forbidden_markers = (
        "sk_test",
        "whsec",
        "card",
        "pan",
        "cvc",
        "bank_account",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
    )
    with sessions.session() as session:
        audit_blobs = [
            str(row.safe_metadata)
            for row in session.scalars(select(AuditEventRecord)).all()
        ]
        outbox_blobs = [
            str(row.payload)
            for row in session.scalars(select(OutboxMessageRecord)).all()
        ]
    haystacks = " ".join(audit_blobs + outbox_blobs).lower()
    for marker in forbidden_markers:
        assert marker.lower() not in haystacks


def test_ledger_and_billing_queue_are_isolated_across_centers(pilot: Pilot) -> None:
    """Cross-Center IDOR: a second Center's admin never sees this Center's
    billing queue, counselor earnings ledger, or webhook failures, and vice
    versa -- even though both share the same underlying database."""
    service, sessions, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")
    service.record_earnings_adjustment(admin, counselor.id, 1_000, "bonus")

    with sessions.session() as session, session.begin():
        other_center_id = uuid.uuid4()
        session.add(
            CenterRecord(id=other_center_id, slug="other-center", name="Other Center")
        )
        session.add(
            CommunityRecord(
                id=uuid.uuid4(),
                center_id=other_center_id,
                slug="other-community",
                name="Other Community",
            )
        )
        other_admin_record = UserRecord(
            center_id=other_center_id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-admin-sub",
            email="other-admin@example.com",
            name="Other Admin",
            role=Role.ADMIN.value,
        )
        session.add(other_admin_record)
        session.flush()
        other_admin_id = other_admin_record.id

    other_admin = AuthenticatedUser(
        id=other_admin_id,
        email="other-admin@example.com",
        name="Other Admin",
        role=Role.ADMIN,
        center_id=other_center_id,
    )

    assert len(service.billing_queue(admin)) > 0
    assert len(service.billing_queue(other_admin)) == 0

    assert len(service.ledger(admin)) > 0
    assert len(service.ledger(other_admin)) == 0

    assert list(service.accounts(other_admin)) == [
        AccountRow(
            id=other_admin_id,
            email="other-admin@example.com",
            display_name="Other Admin",
            role=Role.ADMIN,
            status=AccountStatus.ACTIVE,
            disabled_reason_code=None,
            disabled_at=None,
            is_self=True,
            counselor_needs_reassignment=False,
        ),
    ]
    account_ids = {row.id for row in service.accounts(admin)}
    assert other_admin_id not in account_ids
