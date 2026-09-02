from dataclasses import dataclass
from enum import StrEnum


class HealthStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    status: HealthStatus
    detail: str


@dataclass(frozen=True, slots=True)
class SystemHealth:
    components: tuple[ComponentHealth, ...]

    @property
    def status(self) -> HealthStatus:
        if all(component.status is HealthStatus.READY for component in self.components):
            return HealthStatus.READY
        return HealthStatus.DEGRADED
