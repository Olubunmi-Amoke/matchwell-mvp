"""Privacy-safe pilot analytics: aggregate funnel/conversion, safety, and
provider-failure counts only.

No raw member export, free text, message/reflection/screening/assessment
content is ever included here -- only Center-scoped integer counts and
derived rates. Safety and provider-failure counts apply small-cell
suppression: a nonzero count below ``SMALL_CELL_SUPPRESSION_THRESHOLD`` is
suppressed (returned as ``None``) rather than displayed, so a handful of
safety events for a small pilot Center can never be used to infer anything
about a specific member. General funnel counts are not small-cell
suppressed: they describe pilot-wide progress through ordinary,
non-sensitive stages and are safe to show exactly.
"""

from dataclasses import dataclass

SMALL_CELL_SUPPRESSION_THRESHOLD = 5


def suppress_small_cell(value: int) -> int | None:
    """Suppress a nonzero count below the threshold.

    A true zero is always shown as ``0`` (there is nothing to protect); a
    count at or above the threshold is shown exactly. Only the ambiguous
    "somewhere between 1 and threshold-1" band is suppressed, returned as
    ``None`` -- callers must render that as an explicit "suppressed" label,
    never as a blank or as zero.
    """
    if 0 < value < SMALL_CELL_SUPPRESSION_THRESHOLD:
        return None
    return value


@dataclass(frozen=True, slots=True)
class FunnelSnapshot:
    """Center-scoped counts from invitation through active guided journey.

    Every count here is an ordinary operational/progress count, not a
    safety or provider-failure signal, so none of these are small-cell
    suppressed.
    """

    invitations_sent: int
    accounts_created: int
    profile_completed: int
    consent_accepted: int
    assessment_completed: int
    counselor_assigned: int
    screening_eligible: int
    subscription_ready: int
    community_eligible: int
    proposals_generated: int
    introductions_awaiting_response: int
    active_matches: int
    guided_journeys_started: int
    checkins_submitted: int

    @staticmethod
    def conversion_rate(numerator: int, denominator: int) -> float | None:
        if denominator == 0:
            return None
        return round(numerator / denominator, 4)


@dataclass(frozen=True, slots=True)
class SafetySnapshot:
    """Small-cell-suppressed aggregate safety signal counts."""

    blocks: int | None
    reports: int | None
    active_holds: int | None


@dataclass(frozen=True, slots=True)
class ProviderFailureSnapshot:
    """Small-cell-suppressed aggregate unapplied-provider-event counts."""

    billing_webhook_failures: int | None
    screening_failures: int | None


@dataclass(frozen=True, slots=True)
class PilotAnalyticsSnapshot:
    funnel: FunnelSnapshot
    safety: SafetySnapshot
    provider_failures: ProviderFailureSnapshot
