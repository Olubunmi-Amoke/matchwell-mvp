"""Alert-ready aggregate metrics and thresholds for the Center-scoped
administrator operations dashboard.

No hosted monitoring provider is added in this pilot. This module defines
the same thresholds an external monitor would use, evaluated on demand
against real, DB-queryable signals, and rendered on the admin dashboard.
See docs/runbooks/monitoring-and-alerts.md for the operator-facing
description of each metric, threshold, and recommended action.
"""

from dataclasses import dataclass
from enum import StrEnum


class AlertStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AlertMetric:
    name: str
    value: int | None
    status: AlertStatus
    threshold_description: str
    recommended_action: str


@dataclass(frozen=True, slots=True)
class AlertSnapshot:
    metrics: tuple[AlertMetric, ...]

    @property
    def overall_status(self) -> AlertStatus:
        statuses = {metric.status for metric in self.metrics}
        if AlertStatus.CRITICAL in statuses:
            return AlertStatus.CRITICAL
        if AlertStatus.WARNING in statuses:
            return AlertStatus.WARNING
        if AlertStatus.UNKNOWN in statuses:
            return AlertStatus.UNKNOWN
        return AlertStatus.OK


def _status_for(value: int, warning_at: int, critical_at: int) -> AlertStatus:
    if value >= critical_at:
        return AlertStatus.CRITICAL
    if value >= warning_at:
        return AlertStatus.WARNING
    return AlertStatus.OK


def evaluate_auth_failures(denied_sign_in_count_24h: int) -> AlertMetric:
    return AlertMetric(
        name="Denied sign-ins (24h)",
        value=denied_sign_in_count_24h,
        status=_status_for(denied_sign_in_count_24h, warning_at=5, critical_at=20),
        threshold_description="Warn at 5, critical at 20 in a rolling 24 hours.",
        recommended_action=(
            "Review the account access queue for repeated disabled-account "
            "sign-in attempts; consider whether a targeted account needs "
            "further investigation."
        ),
    )


def evaluate_provider_failures(unapplied_count: int) -> AlertMetric:
    return AlertMetric(
        name="Unapplied provider events",
        value=unapplied_count,
        status=_status_for(unapplied_count, warning_at=1, critical_at=10),
        threshold_description="Warn at 1, critical at 10 unapplied receipts.",
        recommended_action=(
            "Open Billing / Screening failures and follow "
            "docs/runbooks/provider-failure-recovery.md to triage and "
            "recover each unapplied event."
        ),
    )


def evaluate_overdue_queues(overdue_count: int) -> AlertMetric:
    return AlertMetric(
        name="Overdue operational queue items",
        value=overdue_count,
        status=_status_for(overdue_count, warning_at=1, critical_at=10),
        threshold_description="Warn at 1, critical at 10 overdue check-ins.",
        recommended_action=(
            "Review Guided journeys for overdue check-ins and follow up "
            "with the assigned counselor."
        ),
    )


def evaluate_safety_activity(recent_safety_event_count: int) -> AlertMetric:
    return AlertMetric(
        name="Recent safety activity (7d)",
        value=recent_safety_event_count,
        status=_status_for(recent_safety_event_count, warning_at=1, critical_at=5),
        threshold_description=(
            "Warn at 1, critical at 5 blocks/reports/holds in a rolling 7 days."
        ),
        recommended_action=(
            "Review Member readiness holds and the safety queue; this "
            "metric is never small-cell suppressed here because it is "
            "shown only in aggregate as a single count, not tied to a "
            "specific member."
        ),
    )


def evaluate_backup_drill_age(age_days: int | None) -> AlertMetric:
    if age_days is None:
        return AlertMetric(
            name="Backup drill age",
            value=None,
            status=AlertStatus.CRITICAL,
            threshold_description="No drill has ever been recorded.",
            recommended_action=(
                "Run a restore drill following "
                "docs/runbooks/backup-and-restore.md and record it in "
                "backup_drill_runs."
            ),
        )
    return AlertMetric(
        name="Backup drill age",
        value=age_days,
        status=_status_for(age_days, warning_at=45, critical_at=90),
        threshold_description="Warn at 45 days, critical at 90 days since the last drill.",
        recommended_action=(
            "Run a fresh restore drill following "
            "docs/runbooks/backup-and-restore.md and record it in "
            "backup_drill_runs."
        ),
    )
