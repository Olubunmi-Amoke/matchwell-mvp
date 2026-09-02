from typing import Protocol

from matchwell.domain.system_health import ComponentHealth, SystemHealth


class DatabaseProbe(Protocol):
    def check(self) -> ComponentHealth: ...


class SystemHealthService:
    def __init__(self, database_probe: DatabaseProbe) -> None:
        self._database_probe = database_probe

    def check(self) -> SystemHealth:
        return SystemHealth(components=(self._database_probe.check(),))
