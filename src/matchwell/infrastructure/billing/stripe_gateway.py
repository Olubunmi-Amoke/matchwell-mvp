"""Stripe test-mode adapter behind the ``PaymentGateway`` port.

Every Stripe-specific object stays inside this module. Callers only see the
provider-neutral domain types in :mod:`matchwell.domain.billing`, and no
Stripe secret, API key, or raw webhook body is ever logged or persisted.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import stripe

from matchwell.domain.billing import (
    BillingPortalSessionView,
    BillingWebhookEvent,
    CheckoutSessionView,
    ProviderEventType,
)
from matchwell.domain.errors import AuthenticationError, ValidationError

MEMBER_METADATA_KEY = "matchwell_member_id"

_EVENT_TYPE_MAP: dict[str, ProviderEventType] = {
    "checkout.session.completed": ProviderEventType.CHECKOUT_COMPLETED,
    "customer.subscription.updated": ProviderEventType.SUBSCRIPTION_UPDATED,
    "customer.subscription.deleted": ProviderEventType.SUBSCRIPTION_DELETED,
    "invoice.paid": ProviderEventType.INVOICE_PAID,
    "invoice.payment_failed": ProviderEventType.INVOICE_PAYMENT_FAILED,
    "charge.refunded": ProviderEventType.REFUND_ISSUED,
}


class StripePaymentGateway:
    """Test-mode Stripe Checkout, Billing Portal, and webhook adapter."""

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        price_id: str,
        checkout_success_url: str,
        checkout_cancel_url: str,
        billing_portal_return_url: str,
    ) -> None:
        if not secret_key.strip():
            raise ValidationError("STRIPE_SECRET_KEY is not configured.")
        if not webhook_secret.strip():
            raise ValidationError("STRIPE_WEBHOOK_SECRET is not configured.")
        if not price_id.strip():
            raise ValidationError("STRIPE_PILOT_PRICE_ID is not configured.")
        if not checkout_success_url.strip() or not checkout_cancel_url.strip():
            raise ValidationError("Checkout return URLs are not configured.")
        if not billing_portal_return_url.strip():
            raise ValidationError("The billing portal return URL is not configured.")
        self._client = stripe.StripeClient(secret_key)
        self._webhook_secret = webhook_secret
        self._price_id = price_id
        self._checkout_success_url = checkout_success_url
        self._checkout_cancel_url = checkout_cancel_url
        self._billing_portal_return_url = billing_portal_return_url

    def create_checkout_session(
        self,
        *,
        member_id: uuid.UUID,
        member_email: str,
        existing_provider_customer_id: str | None,
    ) -> CheckoutSessionView:
        customer_id = existing_provider_customer_id
        if customer_id is None:
            customer = self._client.customers.create(
                cast(
                    Any,
                    {
                        "email": member_email,
                        "metadata": {MEMBER_METADATA_KEY: str(member_id)},
                    },
                )
            )
            customer_id = customer.id
        session = self._client.checkout.sessions.create(
            cast(
                Any,
                {
                    "mode": "subscription",
                    "customer": customer_id,
                    "line_items": [{"price": self._price_id, "quantity": 1}],
                    "success_url": self._checkout_success_url,
                    "cancel_url": self._checkout_cancel_url,
                    "client_reference_id": str(member_id),
                    "metadata": {MEMBER_METADATA_KEY: str(member_id)},
                    "subscription_data": {
                        "metadata": {MEMBER_METADATA_KEY: str(member_id)},
                    },
                },
            )
        )
        if not session.url:
            raise ValidationError("Stripe did not return a checkout URL.")
        return CheckoutSessionView(
            url=session.url,
            provider_session_id=session.id,
            provider_customer_id=customer_id,
        )

    def create_billing_portal_session(
        self,
        *,
        provider_customer_id: str,
    ) -> BillingPortalSessionView:
        session = self._client.billing_portal.sessions.create(
            cast(
                Any,
                {
                    "customer": provider_customer_id,
                    "return_url": self._billing_portal_return_url,
                },
            )
        )
        return BillingPortalSessionView(url=session.url)

    def verify_and_parse_webhook(
        self,
        *,
        payload: bytes,
        signature_header: str,
    ) -> BillingWebhookEvent:
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature_header,
                self._webhook_secret,
            )  # type: ignore[no-untyped-call]
        except stripe.SignatureVerificationError as error:
            raise AuthenticationError(
                "The Stripe webhook signature is invalid."
            ) from error
        except (ValueError, stripe.StripeError) as error:
            raise ValidationError(
                "The Stripe webhook payload could not be parsed."
            ) from error
        return _normalize_event(event)


def _normalize_event(event: Any) -> BillingWebhookEvent:
    event_type = _EVENT_TYPE_MAP.get(event.type)
    occurred_at = datetime.fromtimestamp(event.created, tz=UTC)
    data_object = event.data.object

    provider_customer_id = _field(data_object, "customer")
    provider_subscription_id: str | None = None
    provider_status: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end = False
    amount_minor_units: int | None = None
    currency: str | None = None
    member_id = _extract_member_id(data_object)

    if event_type is ProviderEventType.CHECKOUT_COMPLETED:
        provider_subscription_id = _field(data_object, "subscription")
        if member_id is None:
            member_id = _parse_uuid(_field(data_object, "client_reference_id"))
    elif event_type in (
        ProviderEventType.SUBSCRIPTION_UPDATED,
        ProviderEventType.SUBSCRIPTION_DELETED,
    ):
        provider_subscription_id = _field(data_object, "id")
        provider_status = _field(data_object, "status")
        cancel_at_period_end = bool(_field(data_object, "cancel_at_period_end", False))
        current_period_end = _subscription_period_end(data_object)
    elif event_type in (
        ProviderEventType.INVOICE_PAID,
        ProviderEventType.INVOICE_PAYMENT_FAILED,
    ):
        provider_subscription_id = _invoice_subscription_id(data_object)
        # The invoice-level ``period_end`` describes when the invoice was
        # generated, which is retrospective and would immediately expire an
        # otherwise-current entitlement. The line items describe what was
        # actually paid through, so entitlement is always extended from
        # there instead.
        current_period_end = _invoice_line_period_end(data_object)
        amount_minor_units = _field(data_object, "amount_paid") or _field(
            data_object, "amount_due"
        )
        currency = _field(data_object, "currency")
    elif event_type is ProviderEventType.REFUND_ISSUED:
        amount_minor_units = _field(data_object, "amount_refunded")
        currency = _field(data_object, "currency")

    return BillingWebhookEvent(
        provider="stripe",
        provider_event_id=event.id,
        event_type=event_type,
        occurred_at=occurred_at,
        provider_customer_id=provider_customer_id,
        provider_subscription_id=provider_subscription_id,
        member_id=member_id,
        provider_status=provider_status,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
        amount_minor_units=amount_minor_units,
        currency=currency,
    )


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read a Stripe payload field without tripping on dict method names.

    A ``StripeObject`` (and any realistic dict-shaped fixture) is a ``dict``
    subclass, so an attribute lookup such as ``subscription.items`` silently
    resolves to the built-in ``dict.items`` bound method instead of the API
    field of the same name. Mapping access is tried first to avoid that trap;
    plain objects (for example legacy test doubles) fall back to
    ``getattr``.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _subscription_period_end(subscription: Any) -> datetime | None:
    """The subscription's paid-through date, Basil-normalized.

    As of the 2025-03-31.basil API version, ``current_period_end`` was
    removed from the top-level ``Subscription`` object: each subscription
    item now tracks its own billing period, since a single subscription can
    mix items with different cadences. The furthest item period end is used
    conservatively so a member is never cut off before every line they are
    paying for has actually lapsed.
    """
    items = _field(_field(subscription, "items") or {}, "data") or []
    period_ends = [
        raw
        for raw in (_field(item, "current_period_end") for item in items)
        if raw is not None
    ]
    if period_ends:
        return _parse_timestamp(max(period_ends))
    # Pre-Basil API versions still return this directly on the subscription.
    return _parse_timestamp(_field(subscription, "current_period_end"))


def _invoice_subscription_id(invoice: Any) -> str | None:
    """The subscription an invoice belongs to, Basil-normalized.

    As of Basil, ``invoice.subscription`` was removed in favor of
    ``invoice.parent.subscription_details.subscription``, gated on
    ``invoice.parent.type == "subscription_details"``.
    """
    parent = _field(invoice, "parent")
    if parent is not None and _field(parent, "type") == "subscription_details":
        details = _field(parent, "subscription_details") or {}
        subscription = _field(details, "subscription")
        if subscription is not None:
            return (
                subscription
                if isinstance(subscription, str)
                else _field(subscription, "id")
            )
    # Pre-Basil API versions still expose this directly on the invoice.
    legacy = _field(invoice, "subscription")
    if legacy is None:
        return None
    return legacy if isinstance(legacy, str) else _field(legacy, "id")


def _invoice_line_period_end(invoice: Any) -> datetime | None:
    """The paid-through date evidenced by an invoice, from its line items.

    The invoice-level ``period_end`` describes when the invoice itself was
    generated (retrospective, and often equal to the moment the *previous*
    period ended), not the period the payment actually covers. The line
    items' billing periods are the correct, forward-looking source of truth,
    so entitlement is only ever extended from there. There is deliberately
    no invoice-level fallback.
    """
    lines = _field(_field(invoice, "lines") or {}, "data") or []
    period_ends = []
    for line in lines:
        period = _field(line, "period") or {}
        raw = _field(period, "end")
        if raw is not None:
            period_ends.append(raw)
    if not period_ends:
        return None
    return _parse_timestamp(max(period_ends))


def _extract_member_id(data_object: Any) -> uuid.UUID | None:
    metadata = _field(data_object, "metadata") or {}
    return _parse_uuid(_field(metadata, MEMBER_METADATA_KEY))


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _parse_timestamp(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def build_stripe_gateway(
    *,
    secret_key: str | None,
    webhook_secret: str | None,
    price_id: str | None,
    checkout_success_url: str | None,
    checkout_cancel_url: str | None,
    billing_portal_return_url: str | None,
) -> StripePaymentGateway | None:
    """Build the adapter from settings, or ``None`` when billing isn't configured.

    Returning ``None`` (rather than raising) lets the Streamlit app and the
    webhook service keep working in environments where billing secrets are
    intentionally absent, such as most automated tests. Construction is
    deliberately all-or-nothing: secrets, the price, and every return URL are
    each required by ``StripePaymentGateway.__init__``, so a *partial*
    configuration (for example secrets set but a return URL forgotten) must
    also return ``None`` here rather than passing empty-string placeholders
    that would raise ``ValidationError`` and crash the caller at startup.
    """
    if (
        not secret_key
        or not webhook_secret
        or not price_id
        or not checkout_success_url
        or not checkout_cancel_url
        or not billing_portal_return_url
    ):
        return None
    return StripePaymentGateway(
        secret_key=secret_key,
        webhook_secret=webhook_secret,
        price_id=price_id,
        checkout_success_url=checkout_success_url,
        checkout_cancel_url=checkout_cancel_url,
        billing_portal_return_url=billing_portal_return_url,
    )
