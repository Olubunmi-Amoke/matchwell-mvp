import uuid
from datetime import UTC, datetime

import pytest
from test_matching_repository import (
    Pilot,
    _bootstrap_admin_and_counselors,
    _make_reciprocal_pair,
)

from matchwell.application.pilot import PilotService
from matchwell.domain.access import Role
from matchwell.domain.alerts import (
    AlertStatus,
    evaluate_auth_failures,
    evaluate_backup_drill_age,
    evaluate_overdue_queues,
    evaluate_provider_failures,
    evaluate_safety_activity,
)
from matchwell.domain.analytics import (
    SMALL_CELL_SUPPRESSION_THRESHOLD,
    suppress_small_cell,
)
from matchwell.domain.journey import TaskScope
from matchwell.domain.matching import CounselorReviewDecision, MemberResponseDecision
from matchwell.domain.readiness import ReadinessEvaluator
from matchwell.infrastructure.persistence.database import (
    Base,
    DatabaseSessionFactory,
    create_database_engine,
)
from matchwell.infrastructure.persistence.models import (
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    JourneyTemplateRecord,
    JourneyTemplateTaskRecord,
    PilotPlanRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)


@pytest.fixture
def pilot() -> Pilot:
    """A local, self-contained seed matching test_matching_repository's
    fixture. Not imported directly: a plain-function import of a
    ``@pytest.fixture``-decorated function from another test module is not
    reliably re-registered by pytest once both modules are collected
    together in a full run, so this module defines its own copy instead."""
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
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
            PilotPlanRecord(
                id=uuid.uuid4(),
                key="matchwell-pilot",
                name="Matchwell Pilot",
                price_minor_units=4_900,
                currency="usd",
                is_active=True,
            )
        )
        from matchwell.infrastructure.persistence.models import (
            AssessmentDefinitionRecord,
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
        for sequence, (scope, due_day) in enumerate(
            (
                (TaskScope.SHARED, 7),
                (TaskScope.SHARED, 14),
                (TaskScope.INDIVIDUAL, 21),
                (TaskScope.SHARED, 30),
                (TaskScope.SHARED, 45),
                (TaskScope.INDIVIDUAL, 60),
            ),
            start=1,
        ):
            session.add(
                JourneyTemplateTaskRecord(
                    id=uuid.uuid4(),
                    template_id=template_id,
                    sequence=sequence,
                    title=f"Activity {sequence}",
                    description=f"Complete activity {sequence}.",
                    scope=scope.value,
                    due_day=due_day,
                )
            )
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    return PilotService(repository, frozenset({"admin@example.com"})), sessions


@pytest.mark.parametrize("value", [0])
def test_suppress_small_cell_never_suppresses_a_true_zero(value: int) -> None:
    assert suppress_small_cell(value) == 0


@pytest.mark.parametrize("value", [1, 2, 3, 4])
def test_suppress_small_cell_suppresses_below_threshold(value: int) -> None:
    assert value < SMALL_CELL_SUPPRESSION_THRESHOLD
    assert suppress_small_cell(value) is None


@pytest.mark.parametrize("value", [5, 6, 100])
def test_suppress_small_cell_shows_at_or_above_threshold(value: int) -> None:
    assert suppress_small_cell(value) == value


def test_auth_failures_thresholds() -> None:
    assert evaluate_auth_failures(0).status is AlertStatus.OK
    assert evaluate_auth_failures(4).status is AlertStatus.OK
    assert evaluate_auth_failures(5).status is AlertStatus.WARNING
    assert evaluate_auth_failures(19).status is AlertStatus.WARNING
    assert evaluate_auth_failures(20).status is AlertStatus.CRITICAL


def test_provider_failures_thresholds() -> None:
    assert evaluate_provider_failures(0).status is AlertStatus.OK
    assert evaluate_provider_failures(1).status is AlertStatus.WARNING
    assert evaluate_provider_failures(9).status is AlertStatus.WARNING
    assert evaluate_provider_failures(10).status is AlertStatus.CRITICAL


def test_overdue_queues_thresholds() -> None:
    assert evaluate_overdue_queues(0).status is AlertStatus.OK
    assert evaluate_overdue_queues(1).status is AlertStatus.WARNING
    assert evaluate_overdue_queues(10).status is AlertStatus.CRITICAL


def test_safety_activity_thresholds() -> None:
    assert evaluate_safety_activity(0).status is AlertStatus.OK
    assert evaluate_safety_activity(1).status is AlertStatus.WARNING
    assert evaluate_safety_activity(5).status is AlertStatus.CRITICAL


def test_backup_drill_age_never_recorded_is_critical() -> None:
    metric = evaluate_backup_drill_age(None)
    assert metric.status is AlertStatus.CRITICAL
    assert metric.value is None


def test_backup_drill_age_thresholds() -> None:
    assert evaluate_backup_drill_age(0).status is AlertStatus.OK
    assert evaluate_backup_drill_age(44).status is AlertStatus.OK
    assert evaluate_backup_drill_age(45).status is AlertStatus.WARNING
    assert evaluate_backup_drill_age(90).status is AlertStatus.CRITICAL


def test_alert_snapshot_overall_status_is_the_worst_metric() -> None:
    from matchwell.domain.alerts import AlertMetric, AlertSnapshot

    snapshot = AlertSnapshot(
        metrics=(
            AlertMetric("a", 0, AlertStatus.OK, "", ""),
            AlertMetric("b", 1, AlertStatus.WARNING, "", ""),
            AlertMetric("c", 0, AlertStatus.OK, "", ""),
        )
    )
    assert snapshot.overall_status is AlertStatus.WARNING

    critical_snapshot = AlertSnapshot(
        metrics=snapshot.metrics + (AlertMetric("d", 99, AlertStatus.CRITICAL, "", ""),)
    )
    assert critical_snapshot.overall_status is AlertStatus.CRITICAL


def test_analytics_snapshot_counts_funnel_stages_accurately(pilot: Pilot) -> None:
    service, _sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    assert service.generate_candidates(admin) == 1
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)
    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )

    snapshot = service.analytics(admin)

    assert snapshot.funnel.accounts_created == 2
    assert snapshot.funnel.profile_completed == 2
    assert snapshot.funnel.consent_accepted == 2
    assert snapshot.funnel.assessment_completed == 2
    assert snapshot.funnel.counselor_assigned == 2
    assert snapshot.funnel.screening_eligible == 2
    assert snapshot.funnel.subscription_ready == 2
    assert snapshot.funnel.community_eligible == 2
    assert snapshot.funnel.proposals_generated == 1
    # Only one of the two members has responded so far: still awaiting.
    assert snapshot.funnel.introductions_awaiting_response == 1
    assert snapshot.funnel.active_matches == 0

    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.ACCEPTED
    )
    activated = service.analytics(admin)
    assert activated.funnel.introductions_awaiting_response == 0
    assert activated.funnel.active_matches == 1


def test_analytics_snapshot_applies_small_cell_suppression_to_safety_counts(
    pilot: Pilot,
) -> None:
    service, _sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    assert service.generate_candidates(admin) == 1
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)
    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )
    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.ACCEPTED
    )

    from matchwell.domain.matching import SafetyCategory

    service.report_member(
        member_a,
        member_b.id,
        SafetyCategory.HARASSMENT,
        "Concerning behavior during our conversation together.",
    )

    below_threshold = service.analytics(admin)
    assert below_threshold.safety.reports is None  # 1 report: suppressed

    for _ in range(SMALL_CELL_SUPPRESSION_THRESHOLD - 1):
        service.apply_hold(admin, member_a.id, "manual-review")
        service.release_hold(admin, member_a.id)
    service.apply_hold(admin, member_a.id, "manual-review")

    at_threshold = service.analytics(admin)
    assert at_threshold.safety.active_holds is None  # 1 active hold: suppressed
    assert at_threshold.safety.reports is None


def test_alert_snapshot_reflects_provider_failures(pilot: Pilot) -> None:
    from datetime import UTC, datetime

    from matchwell.domain.billing import BillingWebhookEvent, ProviderEventType

    service, _sessions = pilot
    admin = service.sign_in(
        __import__("test_matching_repository").identity(
            "admin@example.com", "admin-sub"
        )
    )
    assert admin is not None

    event = BillingWebhookEvent(
        provider="stripe",
        provider_event_id="evt-unresolvable-1",
        event_type=ProviderEventType.SUBSCRIPTION_UPDATED,
        occurred_at=datetime.now(UTC),
        provider_customer_id="cus_never_seen",
        provider_subscription_id="sub_1",
        member_id=None,
        provider_status="active",
        current_period_end=None,
        cancel_at_period_end=False,
        amount_minor_units=None,
        currency=None,
    )
    service.process_billing_webhook_event(event)

    alerts = service.alerts(admin)
    provider_metric = next(
        metric
        for metric in alerts.metrics
        if metric.name == "Unapplied provider events"
    )
    # This receipt never resolved to a member/Center, so it is not counted
    # here either (documented, safe-by-default consequence -- see
    # docs/runbooks/provider-failure-recovery.md).
    assert provider_metric.value == 0

    backup_metric = next(
        metric for metric in alerts.metrics if metric.name == "Backup drill age"
    )
    assert backup_metric.status is AlertStatus.CRITICAL
    assert backup_metric.value is None


def test_alerts_and_analytics_require_admin_role(pilot: Pilot) -> None:
    service, _sessions = pilot
    admin, counselor_a, _counselor_b = _bootstrap_admin_and_counselors(service)
    from matchwell.domain.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        service.analytics(counselor_a)
    with pytest.raises(AuthorizationError):
        service.alerts(counselor_a)
    assert admin.role is Role.ADMIN
