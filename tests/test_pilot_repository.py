import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from matchwell.application.pilot import PilotService
from matchwell.domain.access import OidcIdentity, Role
from matchwell.domain.errors import ConflictError, NotFoundError
from matchwell.domain.pilot import (
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
    AssessmentAssignmentRecord,
    AssessmentDefinitionRecord,
    AuditEventRecord,
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    OutboxMessageRecord,
    ReadinessDecisionRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)


@pytest.fixture
def pilot() -> tuple[PilotService, DatabaseSessionFactory]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
        center_id = uuid.uuid4()
        session.add(
            CenterRecord(
                id=center_id,
                slug="matchwell-pilot",
                name="Matchwell Center",
            )
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
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    return PilotService(repository, frozenset({"admin@example.com"})), sessions


def identity(email: str, subject: str) -> OidcIdentity:
    return OidcIdentity(
        issuer="https://accounts.google.com",
        subject=subject,
        email=email,
        email_verified=True,
        name=email.split("@")[0].title(),
    )


def test_complete_invited_member_journey_is_audited_and_eligible(
    pilot: tuple[PilotService, DatabaseSessionFactory],
) -> None:
    service, sessions = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    assert admin.role is Role.ADMIN

    service.create_invitation(
        admin,
        InvitationInput(
            email="member@example.com",
            role=Role.MEMBER,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    service.create_invitation(
        admin,
        InvitationInput(
            email="counselor@example.com",
            role=Role.COUNSELOR,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    assert len(service.invitations(admin)) == 2

    member = service.sign_in(identity("member@example.com", "member-sub"))
    counselor = service.sign_in(identity("counselor@example.com", "counselor-sub"))
    assert member is not None
    assert counselor is not None
    assert len(service.members(admin)) == 1
    assert len(service.counselors(admin)) == 1

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
    assert service.assigned_members(counselor)[0].id == member.id
    with pytest.raises(ConflictError):
        service.reassign_counselor_to_member(
            admin,
            counselor.id,
            counselor.email,
            "returning-to-member-journey",
        )
    service.record_counselor_decision(
        counselor,
        member.id,
        CounselorDecisionStatus.APPROVED,
    )
    created = service.record_screening_status(
        admin,
        member.id,
        ScreeningStatus.ELIGIBLE,
        "event-1",
        "case-1",
    )

    assert created
    assert service.progress(member).readiness.eligible

    duplicate = service.record_screening_status(
        admin,
        member.id,
        ScreeningStatus.ELIGIBLE,
        "event-1",
        "case-1",
    )
    assert not duplicate

    service.apply_hold(admin, member.id, "manual-review")
    assert not service.progress(member).readiness.eligible
    service.release_hold(admin, member.id)
    assert service.progress(member).readiness.eligible

    with sessions.session() as session, session.begin():
        original_assignment = session.get(
            AssessmentAssignmentRecord,
            assessment.assignment_id,
        )
        assert original_assignment is not None
        original_assignment.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    expired_progress = service.progress(member)
    replacement = service.assessment(member)

    assert not expired_progress.readiness.eligible
    assert replacement.assignment_id != assessment.assignment_id

    with sessions.session() as session:
        assessment_audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "assessment.completed"
            )
        )
        assert assessment_audit is not None
        assert "answers" not in assessment_audit.safe_metadata
        assignment_count = session.scalar(
            select(func.count(AssessmentAssignmentRecord.id)).where(
                AssessmentAssignmentRecord.member_id == member.id
            )
        )
        assert assignment_count == 2
        latest_decision = session.scalar(
            select(ReadinessDecisionRecord)
            .where(ReadinessDecisionRecord.member_id == member.id)
            .order_by(ReadinessDecisionRecord.evaluated_at.desc())
        )
        assert latest_decision is not None
        assert latest_decision.evidence_versions["assessment_assignment_id"] == str(
            replacement.assignment_id
        )
        outbox_count = session.scalar(select(func.count(OutboxMessageRecord.id)))
        assert outbox_count == 5


def test_uninvited_identity_is_denied(
    pilot: tuple[PilotService, DatabaseSessionFactory],
) -> None:
    service, _ = pilot

    assert service.sign_in(identity("unknown@example.com", "unknown-sub")) is None


def test_unassigned_counselor_cannot_decide_for_member(
    pilot: tuple[PilotService, DatabaseSessionFactory],
) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    service.create_invitation(
        admin,
        InvitationInput(
            email="member@example.com",
            role=Role.MEMBER,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    service.create_invitation(
        admin,
        InvitationInput(
            email="counselor@example.com",
            role=Role.COUNSELOR,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    member = service.sign_in(identity("member@example.com", "member-sub"))
    counselor = service.sign_in(identity("counselor@example.com", "counselor-sub"))
    assert member is not None
    assert counselor is not None

    with pytest.raises(NotFoundError):
        service.record_counselor_decision(
            counselor,
            member.id,
            CounselorDecisionStatus.APPROVED,
        )


def test_existing_oidc_identity_reuses_account(
    pilot: tuple[PilotService, DatabaseSessionFactory],
) -> None:
    service, _ = pilot
    first = service.sign_in(identity("admin@example.com", "admin-sub"))
    second = service.sign_in(identity("admin@example.com", "admin-sub"))

    assert first == second


def test_cross_center_member_is_not_visible_or_assignable(
    pilot: tuple[PilotService, DatabaseSessionFactory],
) -> None:
    service, sessions = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    with sessions.session() as session, session.begin():
        other_center = CenterRecord(
            id=uuid.uuid4(),
            slug="other-center",
            name="Other Center",
        )
        session.add(other_center)
        session.flush()
        other_member = UserRecord(
            center_id=other_center.id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-member",
            email="other@example.com",
            name="Other Member",
            role=Role.MEMBER.value,
        )
        session.add(other_member)
        other_counselor = UserRecord(
            center_id=other_center.id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-counselor",
            email="other-counselor@example.com",
            name="Other Counselor",
            role=Role.COUNSELOR.value,
        )
        session.add(other_counselor)
        session.flush()
        other_member_id = other_member.id
        other_counselor_id = other_counselor.id

    assert all(item.id != other_member_id for item in service.members(admin))
    with pytest.raises(NotFoundError):
        service.assign_counselor(admin, other_member_id, uuid.uuid4())
    with pytest.raises(NotFoundError):
        service.reassign_member_to_counselor(
            admin,
            other_member_id,
            "other@example.com",
            "account-role-correction",
        )
    with pytest.raises(NotFoundError):
        service.reassign_counselor_to_member(
            admin,
            other_counselor_id,
            "other-counselor@example.com",
            "account-role-correction",
        )
