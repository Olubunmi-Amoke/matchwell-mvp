from dataclasses import dataclass

from matchwell.application.health import SystemHealthService
from matchwell.domain.system_health import ComponentHealth, HealthStatus


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
