from typing import Protocol

from matchwell.domain.system_health import ComponentHealth, SystemHealth


class DatabaseProbe(Protocol):
    def check(self) -> ComponentHealth: ...


class SystemHealthService:
    def __init__(
        self,
        database_probe: DatabaseProbe,
        migration_probe: DatabaseProbe | None = None,
    ) -> None:
        self._database_probe = database_probe
        self._migration_probe = migration_probe

    def check(self) -> SystemHealth:
        components = [self._database_probe.check()]
        if self._migration_probe is not None:
            components.append(self._migration_probe.check())
        return SystemHealth(components=tuple(components))
