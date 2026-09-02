from unittest.mock import MagicMock

from pytest import MonkeyPatch
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from matchwell.domain.system_health import HealthStatus
from matchwell.infrastructure.persistence import database
from matchwell.infrastructure.persistence.database import (
    DatabaseSessionFactory,
    SqlAlchemyDatabaseProbe,
    create_database_engine,
    upgrade_database,
)
from matchwell.infrastructure.persistence.models import (
    AuditEventRecord,
    OutboxMessageRecord,
)


def test_unconfigured_database_is_degraded() -> None:
    health = SqlAlchemyDatabaseProbe(None).check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "DATABASE_URL is not configured."


def test_reachable_database_is_ready() -> None:
    health = SqlAlchemyDatabaseProbe("sqlite://").check()

    assert health.status is HealthStatus.READY


def test_database_failure_is_reported_without_exception_detail(
    monkeypatch: MonkeyPatch,
) -> None:
    failing_engine = MagicMock(spec=Engine)
    failing_engine.connect.side_effect = SQLAlchemyError(
        "postgresql://user:password@private-host/matchwell"
    )
    monkeypatch.setattr(
        database,
        "create_database_engine",
        lambda _: failing_engine,
    )

    health = SqlAlchemyDatabaseProbe("postgresql://configured").check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "Database connection failed."
    assert "private-host" not in health.detail


def test_session_factory_uses_configured_engine() -> None:
    engine = create_database_engine("sqlite://")
    factory = DatabaseSessionFactory(engine)

    with factory.session() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_foundation_tables_are_registered() -> None:
    assert AuditEventRecord.__tablename__ == "audit_events"
    assert OutboxMessageRecord.__tablename__ == "outbox_messages"


def test_database_upgrade_uses_one_locked_connection(
    monkeypatch: MonkeyPatch,
) -> None:
    engine = MagicMock(spec=Engine)
    connection = engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(database, "create_database_engine", lambda _: engine)
    upgrade = MagicMock()
    monkeypatch.setattr(
        "matchwell.infrastructure.persistence.database.command.upgrade",
        upgrade,
    )

    upgrade_database("postgresql+psycopg://configured")

    assert "pg_advisory_xact_lock" in str(connection.execute.call_args.args[0])
    config = upgrade.call_args.args[0]
    assert config.attributes["connection"] is connection
