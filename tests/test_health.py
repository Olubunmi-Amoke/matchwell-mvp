from dataclasses import dataclass
from unittest.mock import MagicMock

from pytest import MonkeyPatch
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from matchwell.application.health import SystemHealthService
from matchwell.domain.system_health import ComponentHealth, HealthStatus
from matchwell.infrastructure.persistence import database
from matchwell.infrastructure.persistence.database import (
    MigrationStatusProbe,
    SqlAlchemyDatabaseProbe,
)


@dataclass
class StubDatabaseProbe:
    result: ComponentHealth

    def check(self) -> ComponentHealth:
        return self.result


def test_system_is_ready_when_database_is_ready() -> None:
    service = SystemHealthService(
        StubDatabaseProbe(
            ComponentHealth(
                name="PostgreSQL",
                status=HealthStatus.READY,
                detail="Healthy.",
            )
        )
    )

    assert service.check().status is HealthStatus.READY


def test_system_is_degraded_when_database_is_degraded() -> None:
    service = SystemHealthService(
        StubDatabaseProbe(
            ComponentHealth(
                name="PostgreSQL",
                status=HealthStatus.DEGRADED,
                detail="Unavailable.",
            )
        )
    )

    assert service.check().status is HealthStatus.DEGRADED


def test_system_is_ready_only_when_every_component_including_migrations_is_ready() -> (
    None
):
    service = SystemHealthService(
        StubDatabaseProbe(
            ComponentHealth(
                name="PostgreSQL", status=HealthStatus.READY, detail="Healthy."
            )
        ),
        StubDatabaseProbe(
            ComponentHealth(
                name="Migrations",
                status=HealthStatus.DEGRADED,
                detail="A pending migration has not been applied.",
            )
        ),
    )

    result = service.check()
    assert result.status is HealthStatus.DEGRADED
    assert [component.name for component in result.components] == [
        "PostgreSQL",
        "Migrations",
    ]


def test_real_health_probes_never_leak_connection_string(
    monkeypatch: MonkeyPatch,
) -> None:
    failing_engine = MagicMock(spec=Engine)
    failing_engine.connect.side_effect = SQLAlchemyError("******private-host/matchwell")
    monkeypatch.setattr(database, "create_database_engine", lambda _: failing_engine)
    service = SystemHealthService(
        SqlAlchemyDatabaseProbe("postgresql://configured"),
        MigrationStatusProbe("postgresql://configured"),
    )

    for component in service.check().components:
        assert "://" not in component.detail
        assert "password" not in component.detail.lower()
        assert "private-host" not in component.detail
