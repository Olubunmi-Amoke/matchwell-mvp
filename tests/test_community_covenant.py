import importlib.util
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AccountStatus, AuthenticatedUser, Role
from matchwell.domain.errors import ConflictError, ValidationError
from matchwell.domain.pilot import ProfileInput
from matchwell.domain.readiness import ReadinessEvaluator, RequirementCode
from matchwell.infrastructure.persistence.database import (
    Base,
    DatabaseSessionFactory,
    create_database_engine,
)
from matchwell.infrastructure.persistence.models import (
    AssessmentDefinitionRecord,
    AuditEventRecord,
    CenterRecord,
    CommunityCovenantAcceptanceRecord,
    CommunityCovenantDefinitionRecord,
    CommunityRecord,
    ConsentVersionRecord,
    HoldRecord,
    ReadinessDecisionRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    COMMUNITY_COVENANT_POLICY_KEY,
    SqlAlchemyPilotRepository,
)

AFFIRMATIONS = [
    {"key": "christian_identity", "label": "I identify as Christian."},
    {
        "key": "dignity_and_respect",
        "label": "I will treat every person with dignity and respect.",
    },
]


@pytest.fixture
def covenant_setup() -> tuple[
    PilotService,
    DatabaseSessionFactory,
    AuthenticatedUser,
]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    center_id, member_id = uuid.uuid4(), uuid.uuid4()
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
        session.add(
            UserRecord(
                id=member_id,
                center_id=center_id,
                oidc_issuer="https://accounts.google.com",
                oidc_subject=str(member_id),
                email="member@example.com",
                name="Member",
                role=Role.MEMBER.value,
                status=AccountStatus.ACTIVE.value,
            )
        )
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="1",
                title="Consent",
                body_markdown="Consent",
                required_acknowledgements=[],
                effective_at=datetime.now(UTC),
                is_active=True,
            )
        )
        session.add(
            AssessmentDefinitionRecord(
                id=uuid.uuid4(),
                key="readiness",
                version="1",
                title="Readiness",
                description="Readiness",
                questions=[{"id": "q1", "prompt": "Question"}],
                is_active=True,
            )
        )
        session.add(
            CommunityCovenantDefinitionRecord(
                id=uuid.uuid4(),
                policy_key=COMMUNITY_COVENANT_POLICY_KEY,
                display_version="Pilot 1.0",
                revision=1,
                effective_at=datetime.now(UTC),
                title="Faith & Community Covenant",
                body_markdown="## Shared purpose\n\nParticipation commitments.",
                required_affirmations=AFFIRMATIONS,
                is_active=True,
            )
        )
    service = PilotService(
        SqlAlchemyPilotRepository(sessions, ReadinessEvaluator()),
        frozenset(),
    )
    member = AuthenticatedUser(
        id=member_id,
        email="member@example.com",
        name="Member",
        role=Role.MEMBER,
        center_id=center_id,
    )
    return service, sessions, member


def test_exact_current_affirmations_are_required_at_application_boundary(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, _, member = covenant_setup
    covenant = service.community_covenant(member)
    required = frozenset(item.key for item in covenant.required_affirmations)

    with pytest.raises(ValidationError, match="every required"):
        service.accept_community_covenant(
            member, covenant.id, frozenset({"christian_identity"})
        )
    with pytest.raises(ValidationError, match="every required"):
        service.accept_community_covenant(
            member, covenant.id, required | {"unexpected"}
        )


def test_acceptance_is_idempotent_and_audit_contains_keys_not_labels(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, sessions, member = covenant_setup
    covenant = service.community_covenant(member)
    keys = frozenset(item.key for item in covenant.required_affirmations)

    service.accept_community_covenant(member, covenant.id, keys)
    service.accept_community_covenant(member, covenant.id, keys)

    with sessions.session() as session:
        assert (
            session.scalar(select(func.count(CommunityCovenantAcceptanceRecord.id)))
            == 1
        )
        audits = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "community_covenant.accepted"
            )
        ).all()
        assert len(audits) == 1
        assert set(audits[0].safe_metadata) == {
            "policy_key",
            "display_version",
            "revision",
            "affirmation_keys",
        }
        serialized = str(audits[0].safe_metadata)
        assert "dignity_and_respect" in serialized
        assert "treat every person" not in serialized.casefold()


def test_concurrent_exact_acceptance_retries_create_one_record(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'covenant.db'}")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    center_id, member_id, covenant_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
        session.add(
            UserRecord(
                id=member_id,
                center_id=center_id,
                oidc_issuer="issuer",
                oidc_subject=str(member_id),
                email="member@example.com",
                name="Member",
                role=Role.MEMBER.value,
                status=AccountStatus.ACTIVE.value,
            )
        )
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="1",
                title="Consent",
                body_markdown="Consent",
                required_acknowledgements=[],
                effective_at=datetime.now(UTC),
                is_active=True,
            )
        )
        session.add(
            AssessmentDefinitionRecord(
                id=uuid.uuid4(),
                key="readiness",
                version="1",
                title="Readiness",
                description="Readiness",
                questions=[{"id": "q1", "prompt": "Question"}],
                is_active=True,
            )
        )
        session.add(
            CommunityCovenantDefinitionRecord(
                id=covenant_id,
                policy_key=COMMUNITY_COVENANT_POLICY_KEY,
                display_version="Pilot 1.0",
                revision=1,
                effective_at=datetime.now(UTC),
                title="Faith & Community Covenant",
                body_markdown="Participation commitments.",
                required_affirmations=AFFIRMATIONS,
                is_active=True,
            )
        )
    service = PilotService(
        SqlAlchemyPilotRepository(sessions, ReadinessEvaluator()),
        frozenset(),
    )
    member = AuthenticatedUser(
        id=member_id,
        email="member@example.com",
        name="Member",
        role=Role.MEMBER,
        center_id=center_id,
    )
    keys = frozenset(item["key"] for item in AFFIRMATIONS)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.accept_community_covenant,
                member,
                covenant_id,
                keys,
            )
            for _ in range(2)
        ]
        for future in futures:
            future.result()

    with sessions.session() as session:
        assert (
            session.scalar(select(func.count(CommunityCovenantAcceptanceRecord.id)))
            == 1
        )
        assert (
            session.scalar(
                select(func.count(AuditEventRecord.id)).where(
                    AuditEventRecord.action == "community_covenant.accepted"
                )
            )
            == 1
        )


def test_mutated_affirmation_keys_invalidate_existing_acceptance(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, sessions, member = covenant_setup
    covenant = service.community_covenant(member)
    original_keys = frozenset(item.key for item in covenant.required_affirmations)
    service.accept_community_covenant(member, covenant.id, original_keys)

    with sessions.session() as session, session.begin():
        definition = session.get(CommunityCovenantDefinitionRecord, covenant.id)
        assert definition is not None
        definition.required_affirmations = [
            *AFFIRMATIONS,
            {"key": "new_commitment", "label": "I affirm a new commitment."},
        ]

    current = service.community_covenant(member)
    assert not current.accepted
    assert (
        RequirementCode.COMMUNITY_COVENANT
        in service.progress(member).readiness.unmet_requirements
    )
    with pytest.raises(ConflictError, match="changed after acceptance"):
        service.accept_community_covenant(
            member,
            current.id,
            frozenset(item.key for item in current.required_affirmations),
        )


def test_new_active_revision_revokes_readiness_and_requires_reaffirmation(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, sessions, member = covenant_setup
    old = service.community_covenant(member)
    old_keys = frozenset(item.key for item in old.required_affirmations)
    service.accept_community_covenant(member, old.id, old_keys)
    service.save_profile(
        member,
        ProfileInput(
            display_name="Member",
            birth_date=date(1990, 1, 1),
            faith_affirmed=True,
            relationship_intent="Committed Christian marriage",
            city="Nashville",
            state="Tennessee",
        ),
    )
    consent = service.consent(member)
    service.accept_consent(member, consent.id)

    new_id = uuid.uuid4()
    with sessions.session() as session, session.begin():
        old_record = session.get(CommunityCovenantDefinitionRecord, old.id)
        assert old_record is not None
        old_record.is_active = False
        session.add(
            CommunityCovenantDefinitionRecord(
                id=new_id,
                policy_key=COMMUNITY_COVENANT_POLICY_KEY,
                display_version="Pilot 1.1",
                revision=2,
                effective_at=datetime.now(UTC) + timedelta(seconds=1),
                title="Faith & Community Covenant",
                body_markdown="Updated participation commitments.",
                required_affirmations=[
                    *AFFIRMATIONS,
                    {"key": "truthful", "label": "I will participate truthfully."},
                ],
                is_active=True,
            )
        )

    current = service.community_covenant(member)
    assert current.id == new_id
    assert not current.accepted
    with pytest.raises(ValidationError, match="no longer current"):
        service.accept_community_covenant(member, old.id, old_keys)
    progress = service.progress(member)
    assert RequirementCode.COMMUNITY_COVENANT in progress.readiness.unmet_requirements
    assert progress.readiness.unmet_requirements[:3] == (
        RequirementCode.COMMUNITY_COVENANT,
        RequirementCode.ASSESSMENT,
        RequirementCode.COUNSELOR,
    )

    new_keys = frozenset(item.key for item in current.required_affirmations)
    service.accept_community_covenant(member, current.id, new_keys)
    restored = service.progress(member)
    assert (
        RequirementCode.COMMUNITY_COVENANT not in restored.readiness.unmet_requirements
    )
    assert restored.readiness.stage.value == "assessment"
    with sessions.session() as session:
        assert (
            session.scalar(select(func.count(CommunityCovenantAcceptanceRecord.id)))
            == 2
        )
        latest = session.scalar(
            select(ReadinessDecisionRecord)
            .where(ReadinessDecisionRecord.member_id == member.id)
            .order_by(ReadinessDecisionRecord.evaluated_at.desc())
        )
        assert latest is not None
        assert latest.evidence_versions["community_covenant_definition_id"] == str(
            current.id
        )
        assert latest.evidence_versions["community_covenant_acceptance_id"] != "none"


def test_global_definition_applies_across_centers_and_holds_still_win(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, sessions, member = covenant_setup
    covenant = service.community_covenant(member)
    other_center_id, other_member_id = uuid.uuid4(), uuid.uuid4()
    with sessions.session() as session, session.begin():
        session.add(CenterRecord(id=other_center_id, slug="other", name="Other"))
        session.add(
            CommunityRecord(
                id=uuid.uuid4(),
                center_id=other_center_id,
                slug="other-community",
                name="Other Community",
            )
        )
        session.add(
            UserRecord(
                id=other_member_id,
                center_id=other_center_id,
                oidc_issuer="https://accounts.google.com",
                oidc_subject=str(other_member_id),
                email="other-member@example.com",
                name="Other Member",
                role=Role.MEMBER.value,
                status=AccountStatus.ACTIVE.value,
            )
        )
    other_member = AuthenticatedUser(
        id=other_member_id,
        email="other-member@example.com",
        name="Other Member",
        role=Role.MEMBER,
        center_id=other_center_id,
    )
    assert service.community_covenant(other_member).id == covenant.id
    service.accept_community_covenant(
        member,
        covenant.id,
        frozenset(item.key for item in covenant.required_affirmations),
    )
    with sessions.session() as session, session.begin():
        session.add(
            HoldRecord(
                id=uuid.uuid4(),
                member_id=member.id,
                center_context_id=member.center_id,
                hold_type="administrative",
                reason_code="review",
                applied_by_id=member.id,
                applied_at=datetime.now(UTC),
            )
        )
    progress = service.progress(member)
    assert progress.readiness.unmet_requirements[-1] is RequirementCode.NO_ACTIVE_HOLD
    assert progress.readiness.stage.value == "held"


def test_repository_rejects_multiple_active_global_definitions(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    _, sessions, _ = covenant_setup
    with sessions.session() as session, session.begin():
        session.add(
            CommunityCovenantDefinitionRecord(
                id=uuid.uuid4(),
                policy_key=COMMUNITY_COVENANT_POLICY_KEY,
                display_version="Invalid",
                revision=2,
                effective_at=datetime.now(UTC),
                title="Invalid",
                body_markdown="Invalid",
                required_affirmations=AFFIRMATIONS,
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_repository_validation_rejects_ambiguous_active_definition(
    covenant_setup: tuple[PilotService, DatabaseSessionFactory, AuthenticatedUser],
) -> None:
    service, sessions, member = covenant_setup
    with sessions.session() as session, session.begin():
        session.execute(text("DROP INDEX uq_community_covenant_one_active"))
        session.add(
            CommunityCovenantDefinitionRecord(
                id=uuid.uuid4(),
                policy_key=COMMUNITY_COVENANT_POLICY_KEY,
                display_version="Invalid",
                revision=2,
                effective_at=datetime.now(UTC),
                title="Invalid",
                body_markdown="Invalid",
                required_affirmations=AFFIRMATIONS,
                is_active=True,
            )
        )
    with pytest.raises(ConflictError, match="configuration is invalid"):
        service.community_covenant(member)


def _migration() -> object:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260916_0011_community_covenant.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0011_sqlite_upgrade_and_downgrade() -> None:
    engine = create_database_engine("sqlite://")
    with engine.begin() as connection:
        member_id, other_member_id, center_id, community_id, decision_id = (
            uuid.uuid4().hex for _ in range(5)
        )
        proposal_id, interest_id = uuid.uuid4().hex, uuid.uuid4().hex
        for statement in (
            "CREATE TABLE users (id CHAR(32) PRIMARY KEY, center_id CHAR(32))",
            "CREATE TABLE audit_events (id CHAR(32) PRIMARY KEY, actor_id VARCHAR, "
            "action VARCHAR, subject_id VARCHAR, center_id CHAR(32), "
            "correlation_id CHAR(32), safe_metadata JSON, occurred_at DATETIME)",
            "CREATE TABLE outbox_messages (id CHAR(32) PRIMARY KEY, "
            "event_type VARCHAR, payload JSON, occurred_at DATETIME, "
            "published_at DATETIME, attempts INTEGER, last_error_code VARCHAR)",
            "CREATE TABLE readiness_decisions (id CHAR(32) PRIMARY KEY, "
            "member_id CHAR(32), community_id CHAR(32), eligible BOOLEAN, "
            "unmet_requirements JSON, evidence_versions JSON, "
            "configuration_version VARCHAR, evaluated_at DATETIME)",
            "CREATE TABLE match_proposals (id CHAR(32) PRIMARY KEY, "
            "center_id CHAR(32), member_a_id CHAR(32), member_b_id CHAR(32), "
            "status VARCHAR, closed_at DATETIME, closed_reason VARCHAR)",
            "CREATE TABLE match_proposal_participant_claims ("
            "proposal_id CHAR(32), member_id CHAR(32))",
            "CREATE TABLE self_paced_suggestion_interests ("
            "id CHAR(32) PRIMARY KEY, member_id CHAR(32), "
            "candidate_member_id CHAR(32), status VARCHAR, updated_at DATETIME)",
        ):
            connection.execute(text(statement))
        connection.execute(
            text(
                "INSERT INTO users VALUES (:member, :center), (:other_member, :center)"
            ),
            {
                "member": member_id,
                "other_member": other_member_id,
                "center": center_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO readiness_decisions VALUES "
                "(:id, :member, :community, true, '[]', '{}', 'pilot-v1', "
                "'2026-09-15 12:00:00'), "
                "(:other_id, :other_member, :community, true, '[]', '{}', "
                "'pilot-v1', '2026-09-15 12:00:00')"
            ),
            {
                "id": decision_id,
                "member": member_id,
                "other_id": uuid.uuid4().hex,
                "other_member": other_member_id,
                "community": community_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO match_proposals VALUES "
                "(:id, :center, :member, :other_member, 'introduced', "
                "NULL, NULL)"
            ),
            {
                "id": proposal_id,
                "center": center_id,
                "member": member_id,
                "other_member": other_member_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO match_proposal_participant_claims VALUES "
                "(:proposal, :member), (:proposal, :other_member)"
            ),
            {
                "proposal": proposal_id,
                "member": member_id,
                "other_member": other_member_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO self_paced_suggestion_interests VALUES "
                "(:id, :member, :other_member, 'interested', "
                "'2026-09-15 12:00:00')"
            ),
            {
                "id": interest_id,
                "member": member_id,
                "other_member": other_member_id,
            },
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            _migration().upgrade()  # type: ignore[attr-defined]
        tables = inspect(connection).get_table_names()
        assert "community_covenant_definitions" in tables
        assert "community_covenant_acceptances" in tables
        seeded = connection.execute(
            text(
                "SELECT policy_key, display_version, revision, is_active "
                "FROM community_covenant_definitions"
            )
        ).one()
        assert seeded == (COMMUNITY_COVENANT_POLICY_KEY, "Pilot 1.0", 1, 1)
        revoked = connection.execute(
            text(
                "SELECT eligible, unmet_requirements, configuration_version "
                "FROM readiness_decisions ORDER BY evaluated_at DESC LIMIT 1"
            )
        ).one()
        assert revoked == (
            0,
            '["community_covenant"]',
            "pilot-v2-community-covenant",
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM outbox_messages "
                    "WHERE event_type = 'readiness.eligibility_changed'"
                )
            ).scalar_one()
            == 2
        )
        assert connection.execute(
            text(
                "SELECT status, closed_reason FROM match_proposals WHERE id = :proposal"
            ),
            {"proposal": proposal_id},
        ).one() == ("closed", "readiness_lost")
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM match_proposal_participant_claims")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT status FROM self_paced_suggestion_interests "
                    "WHERE id = :interest"
                ),
                {"interest": interest_id},
            ).scalar_one()
            == "withdrawn"
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events WHERE action = 'matching.closed'"
                )
            ).scalar_one()
            == 1
        )
        with Operations.context(context):
            _migration().downgrade()  # type: ignore[attr-defined]
        assert (
            "community_covenant_definitions"
            not in inspect(connection).get_table_names()
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM readiness_decisions")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one()
            == 3
        )


def test_0011_renders_postgresql_upgrade_and_downgrade_sql() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration = _migration()
        migration.upgrade()  # type: ignore[attr-defined]
        migration.downgrade()  # type: ignore[attr-defined]
    sql = output.getvalue()
    assert "CREATE TABLE community_covenant_definitions" in sql
    assert "WHERE is_active" in sql
    assert "CREATE TRIGGER community_covenant_definitions_append_only" in sql
    assert "community covenant definitions are append-only" in sql
    assert "OLD.required_affirmations IS DISTINCT FROM NEW.required_affirmations" in sql
    assert "OR NOT OLD.is_active" in sql
    assert "OR NEW.is_active" in sql
    assert "status IN ('pending_review', 'introduced', 'active')" in sql
    assert "UPDATE match_proposals SET status = 'closed'" in sql
    assert "DELETE FROM audit_events" not in sql
    assert "DROP TABLE community_covenant_definitions" in sql
    assert _migration().down_revision == "20260916_0010"  # type: ignore[attr-defined]


def test_domain_models_and_member_ui_have_no_disallowed_attitude_fields() -> None:
    root = Path(__file__).parents[1]
    paths = [
        root / "src" / "matchwell" / "domain",
        root / "src" / "matchwell" / "infrastructure" / "persistence" / "models.py",
        root / "src" / "matchwell" / "presentation" / "member.py",
    ]
    forbidden = ("lgbt_friendly", "sexual_orientation_attitude", "lgbt_attitude")
    for path in paths:
        files = path.rglob("*.py") if path.is_dir() else (path,)
        for file in files:
            contents = file.read_text(encoding="utf-8").casefold()
            assert all(term not in contents for term in forbidden)
