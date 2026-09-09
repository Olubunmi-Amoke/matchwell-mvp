from pathlib import Path
from unittest.mock import MagicMock

from alembic.util.exc import CommandError
from pytest import MonkeyPatch
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from matchwell.domain.system_health import HealthStatus
from matchwell.infrastructure.persistence import database
from matchwell.infrastructure.persistence.database import (
    DatabaseSessionFactory,
    MigrationStatusProbe,
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


def test_migration_status_is_degraded_when_unconfigured() -> None:
    health = MigrationStatusProbe(None).check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "DATABASE_URL is not configured."


def test_migration_status_is_degraded_when_schema_not_migrated(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'unmigrated.db'}"
    create_database_engine(database_url)  # creates the (empty) file

    health = MigrationStatusProbe(database_url).check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "Schema has not been migrated yet."


def test_migration_status_is_ready_when_alembic_version_matches_head(
    tmp_path: Path,
) -> None:
    from alembic.script import ScriptDirectory

    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    engine = create_database_engine(database_url)
    script_directory = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    head_revision = script_directory.get_current_head()
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:version)"),
            {"version": head_revision},
        )

    health = MigrationStatusProbe(database_url).check()

    assert health.status is HealthStatus.READY
    assert health.detail == "Schema is on the latest migration."


def test_migration_status_is_degraded_when_pending_migration(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'stale.db'}"
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('20260101_0000')"),
        )

    health = MigrationStatusProbe(database_url).check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "A pending migration has not been applied."


def test_migration_status_failure_never_leaks_exception_detail(
    monkeypatch: MonkeyPatch,
) -> None:
    failing_engine = MagicMock(spec=Engine)
    failing_engine.connect.side_effect = SQLAlchemyError("******private-host/matchwell")
    monkeypatch.setattr(
        database,
        "create_database_engine",
        lambda _: failing_engine,
    )

    health = MigrationStatusProbe("postgresql://configured").check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "Migration status could not be determined."
    assert "private-host" not in health.detail


def test_migration_metadata_failure_is_degraded(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database,
        "ScriptDirectory",
        MagicMock(side_effect=CommandError("missing migration scripts")),
    )

    health = MigrationStatusProbe("sqlite://").check()

    assert health.status is HealthStatus.DEGRADED
    assert health.detail == "Migration status could not be determined."
    assert "missing migration scripts" not in health.detail
