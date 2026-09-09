"""Provider-neutral billing, entitlement, and counselor earnings domain types.

Billing owns commercial subscription state. Every type here is deliberately
free of Stripe-specific shapes so readiness, matching, messaging, and guided
journey code never depends on a payment provider's object model.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

PILOT_PLAN_KEY = "matchwell-pilot"
PILOT_PLAN_NAME = "Matchwell Pilot"
PILOT_PLAN_PRICE_MINOR_UNITS = 4_900
PILOT_PLAN_CURRENCY = "usd"
PILOT_INTAKE_CREDIT_MINOR_UNITS = 2_500

# A stable, non-human identity used to attribute provider-callback-driven
# audit events and entitlement transitions, matching the "background jobs
# and provider callbacks use dedicated identities" authorization principle.
BILLING_SYSTEM_ACTOR_ID = uuid.UUID(int=0)


class SubscriptionStatus(StrEnum):
    """Normalized, provider-neutral subscription state."""

    INCOMPLETE = "incomplete"
    ACTIVE = "active"
    GRACE = "grace"
    SUSPENDED = "suspended"
    CANCELED = "canceled"
    COMPLIMENTARY = "complimentary"


class BillingEventSource(StrEnum):
    STRIPE_WEBHOOK = "stripe_webhook"
    ADMIN_COMPLIMENTARY = "admin_complimentary"
    ADMIN_CORRECTION = "admin_correction"
    MIGRATION_BACKFILL = "migration_backfill"


class ProviderEventType(StrEnum):
    CHECKOUT_COMPLETED = "checkout.completed"
    SUBSCRIPTION_UPDATED = "subscription.updated"
    SUBSCRIPTION_DELETED = "subscription.deleted"
    INVOICE_PAID = "invoice.paid"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    REFUND_ISSUED = "refund.issued"


class EarningEntryType(StrEnum):
    INTAKE_CREDIT = "intake_credit"
    ADMIN_ADJUSTMENT = "admin_adjustment"


@dataclass(frozen=True, slots=True)
class BillingWebhookEvent:
    """A verified, normalized Stripe event ready for idempotent processing."""

    provider: str
    provider_event_id: str
    event_type: ProviderEventType | None
    occurred_at: datetime
    provider_customer_id: str | None
    provider_subscription_id: str | None
    member_id: uuid.UUID | None
    provider_status: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    amount_minor_units: int | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class EntitlementView:
    """A member-safe, provider-neutral view of the current entitlement."""

    plan_name: str
    price_minor_units: int
    currency: str
    status: SubscriptionStatus
    active: bool
    current_period_end: datetime | None
    cancel_at_period_end: bool
    grace_expires_at: datetime | None
    has_provider_subscription: bool
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CheckoutSessionView:
    url: str
    provider_session_id: str
    provider_customer_id: str


@dataclass(frozen=True, slots=True)
class BillingPortalSessionView:
    url: str


@dataclass(frozen=True, slots=True)
class AdminBillingRow:
    member_id: uuid.UUID
    display_name: str
    email: str
    status: SubscriptionStatus
    current_period_end: datetime | None
    cancel_at_period_end: bool
    grace_expires_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WebhookFailureView:
    """A safe, admin-facing view of a webhook receipt that never applied.

    Never carries the raw payload, signature, or any provider secret; only
    the identifiers and a safe reason code needed to triage and reprocess.
    """

    id: uuid.UUID
    provider: str
    provider_event_id: str
    event_type: str
    unresolved_reason: str | None
    received_at: datetime


@dataclass(frozen=True, slots=True)
class LedgerEntryView:
    id: uuid.UUID
    counselor_id: uuid.UUID
    counselor_name: str
    member_id: uuid.UUID | None
    entry_type: EarningEntryType
    amount_minor_units: int
    currency: str
    reason_code: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CounselorEarningsView:
    entries: tuple[LedgerEntryView, ...]

    @property
    def balance_minor_units(self) -> int:
        return sum(entry.amount_minor_units for entry in self.entries)
