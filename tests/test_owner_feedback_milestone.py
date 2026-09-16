import importlib.util
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from covenant_helpers import seed_community_covenant
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AccountStatus, AuthenticatedUser, Role
from matchwell.domain.errors import ConflictError, NotFoundError, ValidationError
from matchwell.domain.matching import CandidateEvidence, Gender, MatchScorer
from matchwell.domain.pilot import (
    ConsentAcknowledgement,
    DenominationCode,
    IntroductorySessionReasonCode,
    IntroductorySessionStatus,
    ProfileInput,
    normalize_denomination,
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
    CenterRecord,
    CommunityRecord,
    ConsentAcceptanceRecord,
    ConsentVersionRecord,
    CounselorAssignmentRecord,
    IntroductorySessionBenefitRecord,
    OutboxMessageRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)


@pytest.fixture
def milestone() -> tuple[
    PilotService,
    DatabaseSessionFactory,
    AuthenticatedUser,
    AuthenticatedUser,
    AuthenticatedUser,
]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    center_id = uuid.uuid4()
    admin_id, counselor_id, member_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with sessions.session() as session, session.begin():
        session.add(CenterRecord(id=center_id, slug="pilot", name="Pilot"))
        session.add(
            CommunityRecord(
                id=uuid.uuid4(),
                center_id=center_id,
                slug="community",
                name="Community",
            )
        )
        for user_id, email, role in (
            (admin_id, "admin@example.com", Role.ADMIN),
            (counselor_id, "counselor@example.com", Role.COUNSELOR),
            (member_id, "member@example.com", Role.MEMBER),
        ):
            session.add(
                UserRecord(
                    id=user_id,
                    center_id=center_id,
                    oidc_issuer="https://accounts.google.com",
                    oidc_subject=str(user_id),
                    email=email,
                    name=email.split("@")[0].title(),
                    role=role.value,
                    status=AccountStatus.ACTIVE.value,
                )
            )
        session.add(
            CounselorAssignmentRecord(
                id=uuid.uuid4(),
                center_id=center_id,
                member_id=member_id,
                counselor_id=counselor_id,
                assigned_by_id=admin_id,
                assigned_at=datetime.now(UTC),
            )
        )
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="2.0-draft",
                title="DRAFT pilot consent",
                body_markdown="Pending legal review.",
                required_acknowledgements=[
                    {"key": "voluntary", "label": "Participation is voluntary."},
                    {"key": "risk", "label": "I acknowledge risk."},
                ],
                effective_at=datetime.now(UTC),
                is_active=True,
            )
        )
        seed_community_covenant(session)
        session.add(
            AssessmentDefinitionRecord(
                id=uuid.uuid4(),
                key="readiness",
                version="1",
                title="Readiness",
                description="Readiness",
                questions=[{"id": "q", "prompt": "Question"}],
                is_active=True,
            )
        )
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    service = PilotService(repository, frozenset())
    actors = tuple(
        AuthenticatedUser(
            id=user_id,
            email=email,
            name=email.split("@")[0].title(),
            role=role,
            center_id=center_id,
        )
        for user_id, email, role in (
            (admin_id, "admin@example.com", Role.ADMIN),
            (counselor_id, "counselor@example.com", Role.COUNSELOR),
            (member_id, "member@example.com", Role.MEMBER),
        )
    )
    return service, sessions, actors[0], actors[1], actors[2]


def test_denomination_normalization_and_other_scoring_rules() -> None:
    assert normalize_denomination(" Roman Catholic ") == (
        DenominationCode.CATHOLIC,
        None,
    )
    assert normalize_denomination("Church of the Nazarene") == (
        DenominationCode.OTHER,
        "Church of the Nazarene",
    )
    scorer = MatchScorer()

    def candidate(code: DenominationCode) -> CandidateEvidence:
        return CandidateEvidence(
            member_id=uuid.uuid4(),
            gender=Gender.MAN,
            age=30,
            min_partner_age=18,
            max_partner_age=99,
            city="",
            state="",
            denomination_code=code,
            relationship_intent="",
        )

    for excluded in (
        DenominationCode.OTHER,
        DenominationCode.PREFER_NOT_TO_SAY,
    ):
        contribution = scorer.score(candidate(excluded), candidate(excluded))
        assert contribution.contributions[1].points == 0
    assert (
        scorer.score(
            candidate(DenominationCode.BAPTIST),
            candidate(DenominationCode.BAPTIST),
        )
        .contributions[1]
        .points
        == scorer.DENOMINATION_WEIGHT
    )


def test_profile_requires_other_text_at_application_boundary(
    milestone: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
    ],
) -> None:
    service, _, _, _, member = milestone
    with pytest.raises(ValidationError):
        service.save_profile(
            member,
            ProfileInput(
                display_name="Member",
                birth_date=date(1990, 1, 1),
                faith_affirmed=True,
                relationship_intent="Marriage",
                city="Nashville",
                state="Tennessee",
                denomination_code=DenominationCode.OTHER,
            ),
        )


def test_consent_requires_exact_current_acknowledgements_and_audits_keys_only(
    milestone: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
    ],
) -> None:
    service, sessions, _, _, member = milestone
    consent = service.consent(member)
    assert consent.required_acknowledgements == (
        ConsentAcknowledgement("voluntary", "Participation is voluntary."),
        ConsentAcknowledgement("risk", "I acknowledge risk."),
    )
    with pytest.raises(ValidationError):
        service.accept_consent(member, consent.id, frozenset({"voluntary"}))
    with pytest.raises(ValidationError):
        service.accept_consent(
            member, consent.id, frozenset({"voluntary", "risk", "unexpected"})
        )
    service.accept_consent(member, consent.id, frozenset({"risk", "voluntary"}))
    with sessions.session() as session:
        acceptance = session.scalar(select(ConsentAcceptanceRecord))
        assert acceptance is not None
        assert acceptance.accepted_acknowledgement_keys == ["risk", "voluntary"]
        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "consent.accepted"
            )
        )
        assert audit is not None
        assert set(audit.safe_metadata) == {
            "policy_key",
            "version",
            "acknowledgement_keys",
        }
        assert "label" not in str(audit.safe_metadata).casefold()


def test_new_active_consent_requires_reconsent_without_changing_history(
    milestone: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
    ],
) -> None:
    service, sessions, _, _, member = milestone
    old = service.consent(member)
    service.accept_consent(member, old.id, frozenset({"voluntary", "risk"}))
    with sessions.session() as session, session.begin():
        old_record = session.get(ConsentVersionRecord, old.id)
        assert old_record is not None
        old_record.is_active = False
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="3.0-draft",
                title="New DRAFT",
                body_markdown="Still pending legal review.",
                required_acknowledgements=[
                    {"key": "new_key", "label": "I acknowledge the new draft."}
                ],
                effective_at=datetime.now(UTC) + timedelta(seconds=1),
                is_active=True,
            )
        )
    current = service.consent(member)
    assert current.id != old.id
    assert not current.accepted
    with pytest.raises(ValidationError):
        service.accept_consent(member, old.id, frozenset({"new_key"}))
    service.accept_consent(member, current.id, frozenset({"new_key"}))
    with sessions.session() as session:
        assert session.scalar(select(func.count(ConsentAcceptanceRecord.id))) == 2


def test_introductory_session_lifecycle_is_single_audited_and_reschedulable(
    milestone: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
    ],
) -> None:
    service, sessions, admin, counselor, member = milestone
    benefit = service.introductory_session(member)
    assert benefit.status is IntroductorySessionStatus.AVAILABLE
    assert service.introductory_session(member).id == benefit.id
    scheduled = service.schedule_introductory_session(
        admin,
        member.id,
        counselor.id,
        datetime.now(UTC) + timedelta(days=1),
    )
    assert scheduled.status is IntroductorySessionStatus.SCHEDULED
    cancelled = service.cancel_introductory_session(
        admin,
        member.id,
        IntroductorySessionReasonCode.MEMBER_REQUESTED,
    )
    assert cancelled.status is IntroductorySessionStatus.CANCELLED
    service.schedule_introductory_session(
        admin,
        member.id,
        counselor.id,
        datetime.now(UTC) + timedelta(days=2),
    )
    completed = service.complete_introductory_session(counselor, member.id)
    assert completed.status is IntroductorySessionStatus.COMPLETED
    assert completed.completed_at is not None
    with pytest.raises(ConflictError):
        service.schedule_introductory_session(
            admin,
            member.id,
            counselor.id,
            datetime.now(UTC) + timedelta(days=3),
        )
    with sessions.session() as session:
        assert (
            session.scalar(select(func.count(IntroductorySessionBenefitRecord.id))) == 1
        )
        audits = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "introductory_session.transitioned"
            )
        ).all()
        assert len(audits) == 4
        assert all("scheduled_at" not in item.safe_metadata for item in audits)
        events = session.scalars(
            select(OutboxMessageRecord).where(
                OutboxMessageRecord.event_type == "introductory_session.status_changed"
            )
        ).all()
        assert len(events) == 5
        assert all("counselor_id" not in item.payload for item in events)


def test_introductory_session_enforces_center_assignment_and_database_uniqueness(
    milestone: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
    ],
) -> None:
    service, sessions, admin, counselor, member = milestone
    other_admin = AuthenticatedUser(
        id=uuid.uuid4(),
        email="other@example.com",
        name="Other",
        role=Role.ADMIN,
        center_id=uuid.uuid4(),
    )
    with pytest.raises(NotFoundError):
        service.member_introductory_session(other_admin, member.id)
    with pytest.raises(NotFoundError):
        service.schedule_introductory_session(
            other_admin,
            member.id,
            counselor.id,
            datetime.now(UTC) + timedelta(days=1),
        )
    service.introductory_session(member)
    with sessions.session() as session, session.begin():
        session.add(
            IntroductorySessionBenefitRecord(
                id=uuid.uuid4(),
                center_id=member.center_id,
                member_id=member.id,
                status=IntroductorySessionStatus.AVAILABLE.value,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_0008_migrates_legacy_sqlite_profile_and_acceptance_without_data_loss() -> None:
    engine = create_database_engine("sqlite://")
    center_id, user_id, consent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE centers (id CHAR(32) PRIMARY KEY)",
            "CREATE TABLE users (id CHAR(32) PRIMARY KEY, center_id CHAR(32))",
            "CREATE TABLE consent_versions ("
            "id CHAR(32) PRIMARY KEY, policy_key VARCHAR(100), version VARCHAR(50), "
            "title VARCHAR(200), body_markdown TEXT, effective_at DATETIME, "
            "is_active BOOLEAN)",
            "CREATE TABLE consent_acceptances ("
            "id CHAR(32) PRIMARY KEY, user_id CHAR(32), consent_version_id CHAR(32), "
            "accepted_at DATETIME)",
            "CREATE TABLE member_profiles ("
            "user_id CHAR(32) PRIMARY KEY, display_name VARCHAR(100), birth_date DATE, "
            "faith_affirmed BOOLEAN, relationship_intent VARCHAR(300), "
            "denomination VARCHAR(100), city VARCHAR(100), state VARCHAR(100), "
            "completed_at DATETIME)",
        ):
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO centers VALUES (:id)"), {"id": center_id.hex}
        )
        connection.execute(
            text("INSERT INTO users VALUES (:id, :center)"),
            {"id": user_id.hex, "center": center_id.hex},
        )
        connection.execute(
            text(
                "INSERT INTO consent_versions VALUES "
                "(:id, 'pilot-participation', '1', 'Old', 'Old body', "
                "'2026-01-01', true)"
            ),
            {"id": consent_id.hex},
        )
        connection.execute(
            text(
                "INSERT INTO consent_acceptances VALUES "
                "(:id, :user, :consent, '2026-01-02')"
            ),
            {
                "id": uuid.uuid4().hex,
                "user": user_id.hex,
                "consent": consent_id.hex,
            },
        )
        connection.execute(
            text(
                "INSERT INTO member_profiles VALUES "
                "(:user, 'Member', '1990-01-01', true, 'Marriage', "
                "'Church of the Nazarene', 'Nashville', 'Tennessee', '2026-01-02')"
            ),
            {"user": user_id.hex},
        )
        context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260916_0008_owner_feedback_milestone.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0008", migration_path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(context):
            migration.upgrade()
        profile = connection.execute(
            text("SELECT denomination_code, denomination_other FROM member_profiles")
        ).one()
        acceptance = connection.execute(
            text("SELECT accepted_acknowledgement_keys FROM consent_acceptances")
        ).scalar_one()
        assert profile == ("other", "Church of the Nazarene")
        assert acceptance == "[]"
        assert "introductory_session_benefits" in inspect(connection).get_table_names()
