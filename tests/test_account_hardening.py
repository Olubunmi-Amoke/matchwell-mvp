"""Account revocation, admin allow-list reconciliation, and fail-closed
sign-in tests for the Pilot Hardening milestone."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from matchwell.application.pilot import PilotService
from matchwell.domain.access import (
    AccountDisableReasonCode,
    AccountReactivateReasonCode,
    AccountStatus,
    AuthenticatedUser,
    OidcIdentity,
    Role,
)
from matchwell.domain.errors import (
    AccountDisabledError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from matchwell.domain.pilot import (
    InvitationInput,
    ScreeningProviderEvent,
    ScreeningReasonCode,
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
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    PilotPlanRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)

Pilot = tuple[PilotService, DatabaseSessionFactory]


def _make_pilot(admin_emails: frozenset[str]) -> Pilot:
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
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    return PilotService(repository, admin_emails), sessions


@pytest.fixture
def pilot() -> Pilot:
    return _make_pilot(frozenset({"admin@example.com"}))


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


def test_disabled_account_is_denied_before_returning_an_actor(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    service.disable_account(admin, member.id, AccountDisableReasonCode.SAFETY_CONCERN)

    with pytest.raises(AccountDisabledError):
        service.sign_in(identity("member@example.com", "member-sub"))


def test_reactivated_account_can_sign_in_again(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )

    service.disable_account(admin, member.id, AccountDisableReasonCode.SAFETY_CONCERN)
    with pytest.raises(AccountDisabledError):
        service.sign_in(identity("member@example.com", "member-sub"))

    service.reactivate_account(
        admin, member.id, AccountReactivateReasonCode.SAFETY_CONCERN_RESOLVED
    )
    reactivated = service.sign_in(identity("member@example.com", "member-sub"))
    assert reactivated is not None
    assert reactivated.id == member.id


def test_administrator_cannot_disable_their_own_account(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None

    with pytest.raises(ValidationError):
        service.disable_account(
            admin, admin.id, AccountDisableReasonCode.OTHER_OPERATIONAL
        )


def test_last_active_administrator_cannot_be_disabled() -> None:
    # A second administrator is bootstrapped purely via the allow-list, same
    # as the first: no invitation is required for an admin allow-list email.
    service, _ = _make_pilot(frozenset({"admin@example.com", "admin2@example.com"}))
    admin_one = service.sign_in(identity("admin@example.com", "admin-one-sub"))
    admin_two = service.sign_in(identity("admin2@example.com", "admin-two-sub"))
    assert admin_one is not None and admin_two is not None

    # With two active admins, disabling one is fine.
    service.disable_account(
        admin_two, admin_one.id, AccountDisableReasonCode.OTHER_OPERATIONAL
    )

    # Now only admin_two remains active in this Center. Model a distinct
    # admin actor (e.g. a stale/forged session) attempting to disable them
    # -- self-disable is a separate, already-enforced guard, so this
    # isolates the "last active administrator" count guard on its own.
    another_admin_actor = AuthenticatedUser(
        id=uuid.uuid4(),
        email="ghost-admin@example.com",
        name="Ghost Admin",
        role=Role.ADMIN,
        center_id=admin_two.center_id,
    )
    with pytest.raises(ConflictError):
        service.disable_account(
            another_admin_actor,
            admin_two.id,
            AccountDisableReasonCode.OTHER_OPERATIONAL,
        )


def test_stale_stored_admin_does_not_satisfy_last_admin_guard() -> None:
    service, _ = _make_pilot(frozenset({"admin@example.com", "admin2@example.com"}))
    stale_admin = service.sign_in(identity("admin@example.com", "admin-one-sub"))
    current_admin = service.sign_in(identity("admin2@example.com", "admin-two-sub"))
    assert stale_admin is not None and current_admin is not None

    service_after_removal = PilotService(
        service._repository,
        frozenset({"admin2@example.com"}),
    )
    forged_actor = AuthenticatedUser(
        id=uuid.uuid4(),
        email="ghost-admin@example.com",
        name="Ghost Admin",
        role=Role.ADMIN,
        center_id=current_admin.center_id,
    )

    with pytest.raises(ConflictError):
        service_after_removal.disable_account(
            forged_actor,
            current_admin.id,
            AccountDisableReasonCode.OTHER_OPERATIONAL,
        )


def test_admin_allowlist_removal_revokes_access_on_next_sign_in() -> None:
    service, sessions = _make_pilot(frozenset({"admin@example.com"}))
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    assert admin.role is Role.ADMIN

    # The allow-list no longer includes this email: a fresh service/session
    # simulates the next deploy/restart reading updated configuration.
    service_without_admin, _ = (
        PilotService(service._repository, frozenset()),
        None,
    )
    demoted = service_without_admin.sign_in(identity("admin@example.com", "admin-sub"))
    assert demoted is not None
    assert demoted.role is Role.MEMBER

    with sessions.session() as session:
        record = session.scalar(select(UserRecord).where(UserRecord.id == admin.id))
        assert record is not None
        assert record.role == Role.MEMBER.value


def test_admin_allowlist_removal_restores_invited_counselor_role() -> None:
    service, sessions = _make_pilot(
        frozenset({"admin@example.com", "counselor@example.com"})
    )
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    service.create_invitation(
        admin,
        InvitationInput(
            email="counselor@example.com",
            role=Role.COUNSELOR,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    elevated_counselor = service.sign_in(
        identity("counselor@example.com", "counselor-sub")
    )
    assert elevated_counselor is not None
    assert elevated_counselor.role is Role.ADMIN

    service_after_removal = PilotService(
        service._repository,
        frozenset({"admin@example.com"}),
    )
    restored = service_after_removal.sign_in(
        identity("counselor@example.com", "counselor-sub")
    )
    assert restored is not None
    assert restored.role is Role.COUNSELOR

    with sessions.session() as session:
        record = session.scalar(
            select(UserRecord).where(UserRecord.id == elevated_counselor.id)
        )
        assert record is not None
        assert record.role == Role.COUNSELOR.value


def test_admin_allowlist_re_addition_restores_access_but_not_reactivation() -> None:
    service, sessions = _make_pilot(frozenset({"admin@example.com"}))
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None

    service_without_admin = PilotService(
        service._repository,
        frozenset(),
    )
    demoted = service_without_admin.sign_in(identity("admin@example.com", "admin-sub"))
    assert demoted is not None
    assert demoted.role is Role.MEMBER

    # Re-adding the email restores admin access again.
    service_with_admin = PilotService(
        service._repository,
        frozenset({"admin@example.com"}),
    )
    restored = service_with_admin.sign_in(identity("admin@example.com", "admin-sub"))
    assert restored is not None
    assert restored.role is Role.ADMIN

    # But if the account had instead been explicitly disabled, re-adding the
    # allow-list email must never silently reactivate it.
    with sessions.session() as session, session.begin():
        record = session.scalar(select(UserRecord).where(UserRecord.id == admin.id))
        assert record is not None
        record.status = AccountStatus.DISABLED.value
        record.disabled_reason_code = AccountDisableReasonCode.SAFETY_CONCERN.value

    with pytest.raises(AccountDisabledError):
        service_with_admin.sign_in(identity("admin@example.com", "admin-sub"))


def test_disable_account_requires_target_in_actors_center(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None

    with pytest.raises(NotFoundError):
        service.disable_account(
            admin, uuid.uuid4(), AccountDisableReasonCode.SAFETY_CONCERN
        )


def test_disabling_a_member_closes_open_proposals(pilot: Pilot) -> None:
    from datetime import date

    from matchwell.domain.matching import Gender, MatchPreferencesInput
    from matchwell.domain.pilot import CounselorDecisionStatus, ProfileInput

    service, sessions = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.save_profile(
        member,
        ProfileInput(
            display_name="Member One",
            birth_date=date(1990, 1, 1),
            faith_affirmed=True,
            relationship_intent="A healthy, committed Christian marriage.",
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
        admin, member.id, ScreeningStatus.ELIGIBLE, "event-1", "case-1"
    )
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")
    service.save_match_preferences(
        member,
        MatchPreferencesInput(
            gender=Gender.MAN, min_partner_age=25, max_partner_age=40
        ),
    )
    assert service.progress(member).readiness.eligible

    service.disable_account(admin, member.id, AccountDisableReasonCode.SAFETY_CONCERN)

    # The disabled member's readiness/open-proposal reconciliation ran; the
    # account can no longer sign in at all regardless of prior state.
    with pytest.raises(AccountDisabledError):
        service.sign_in(identity("member@example.com", "member-sub"))


def test_disabled_counselor_flags_members_needing_reassignment(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor@example.com", "counselor-sub"
    )
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.assign_counselor(admin, member.id, counselor.id)

    before = service.members(admin)[0]
    assert not before.counselor_needs_reassignment

    service.disable_account(
        admin, counselor.id, AccountDisableReasonCode.INACTIVE_ACCOUNT
    )

    after = service.members(admin)[0]
    assert after.counselor_needs_reassignment
    # The historical assignment is preserved, not silently cleared.
    assert after.counselor_id == counselor.id
    # The disabled counselor can no longer sign in at all.
    with pytest.raises(AccountDisabledError):
        service.sign_in(identity("counselor@example.com", "counselor-sub"))


def test_accounts_queue_reflects_status_and_self(pilot: Pilot) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    service.disable_account(admin, member.id, AccountDisableReasonCode.MEMBER_REQUESTED)

    rows = {row.id: row for row in service.accounts(admin)}
    assert rows[admin.id].is_self
    assert rows[member.id].status is AccountStatus.DISABLED
    assert rows[member.id].disabled_reason_code == "member_requested"
    assert not rows[member.id].is_self


def test_process_screening_provider_event_is_idempotent_and_scoped(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    member = _invite_and_sign_in(
        service, admin, Role.MEMBER, "member@example.com", "member-sub"
    )
    # Seed a screening case with a known provider reference so the provider
    # event can resolve to this member, mirroring how a real intake would
    # first create a case before a provider callback arrives.
    service.record_screening_status(
        admin, member.id, ScreeningStatus.PENDING, "seed-event", "case-42"
    )

    event = ScreeningProviderEvent(
        provider="synthetic-screening",
        provider_event_id="evt-1",
        provider_reference="case-42",
        status=ScreeningStatus.ELIGIBLE,
        reason_code=None,
        occurred_at=datetime.now(UTC),
    )
    assert service.process_screening_provider_event(event)
    assert service.progress(member).screening_status is ScreeningStatus.ELIGIBLE

    # Replaying the same provider event ID is acknowledged, not reprocessed.
    assert not service.process_screening_provider_event(event)

    # A malformed (unrecognized) event type is recorded unapplied.
    malformed = ScreeningProviderEvent(
        provider="synthetic-screening",
        provider_event_id="evt-malformed",
        provider_reference="case-42",
        status=None,
        reason_code=None,
        occurred_at=datetime.now(UTC),
    )
    assert service.process_screening_provider_event(malformed)

    # An event referencing an unknown case never resolves to a member and
    # is recorded unapplied without exposing free text.
    unresolved = ScreeningProviderEvent(
        provider="synthetic-screening",
        provider_event_id="evt-unresolved",
        provider_reference="case-does-not-exist",
        status=ScreeningStatus.INELIGIBLE,
        reason_code=ScreeningReasonCode.MANUAL_REVIEW_REQUIRED,
        occurred_at=datetime.now(UTC),
    )
    assert service.process_screening_provider_event(unresolved)

    failures = service.screening_failures(admin)
    failure_ids = {f.provider_event_id: f for f in failures}
    # Successfully applied events never appear as failures.
    assert "evt-1" not in failure_ids
    # Neither the malformed nor the unresolved-reference receipt ever
    # resolves to a member, so -- exactly like billing's unresolvable
    # webhook receipts -- neither has a Center to scope it to and neither
    # is exposed to any Center admin here. This is the documented,
    # safe-by-default operational consequence: those receipts still exist
    # (verifiable directly in the database) but require direct engineering
    # investigation rather than showing up in this Center-scoped queue.
    assert "evt-malformed" not in failure_ids
    assert "evt-unresolved" not in failure_ids

    # ... but the receipts are never silently dropped; they remain queryable
    # directly for engineering triage of the documented gap above.
    from matchwell.infrastructure.persistence.models import ScreeningEventReceiptRecord

    with sessions.session() as session:
        malformed_receipt = session.scalar(
            select(ScreeningEventReceiptRecord).where(
                ScreeningEventReceiptRecord.provider_event_id == "evt-malformed"
            )
        )
        unresolved_receipt = session.scalar(
            select(ScreeningEventReceiptRecord).where(
                ScreeningEventReceiptRecord.provider_event_id == "evt-unresolved"
            )
        )
        assert malformed_receipt is not None and not malformed_receipt.applied
        assert malformed_receipt.center_id is None
        assert unresolved_receipt is not None and not unresolved_receipt.applied
        assert unresolved_receipt.unresolved_reason == "manual_review_required"
