"""Deterministic Stripe adapter tests. No network calls or live credentials.

Event fixtures are built with ``stripe.Event.construct_from`` over plain
dict payloads shaped exactly like real Stripe webhook deliveries on the
pinned ``2025-08-27.basil`` API version (``stripe==12.5.1``). Real
``StripeObject`` instances are dict subclasses whose attribute access can
silently shadow dict methods (for example ``subscription.items`` resolves to
``dict.items``, not the API field), so MagicMock stand-ins would hide that
class of bug entirely. These fixtures reproduce it faithfully.
"""

import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
import stripe

from matchwell.domain.billing import ProviderEventType
from matchwell.domain.errors import AuthenticationError, ValidationError
from matchwell.infrastructure.billing.stripe_gateway import (
    MEMBER_METADATA_KEY,
    StripePaymentGateway,
    build_stripe_gateway,
)

_API_KEY = "sk_test_123"


def _gateway(client: MagicMock) -> StripePaymentGateway:
    gateway = StripePaymentGateway(
        secret_key=_API_KEY,
        webhook_secret="whsec_test_123",
        price_id="price_123",
        checkout_success_url="https://app.example.com/billing?checkout=success",
        checkout_cancel_url="https://app.example.com/billing?checkout=cancel",
        billing_portal_return_url="https://app.example.com/billing",
    )
    gateway._client = client
    return gateway


def test_missing_config_fails_explicitly() -> None:
    with pytest.raises(ValidationError):
        StripePaymentGateway(
            secret_key="",
            webhook_secret="whsec_test_123",
            price_id="price_123",
            checkout_success_url="https://app.example.com/success",
            checkout_cancel_url="https://app.example.com/cancel",
            billing_portal_return_url="https://app.example.com/billing",
        )
    with pytest.raises(ValidationError):
        StripePaymentGateway(
            secret_key="sk_test_123",
            webhook_secret="whsec_test_123",
            price_id="price_123",
            checkout_success_url="",
            checkout_cancel_url="https://app.example.com/cancel",
            billing_portal_return_url="https://app.example.com/billing",
        )


# --- All-or-nothing gateway construction (partial config must not crash) ---


_FULL_CONFIG: dict[str, str] = {
    "secret_key": "sk_test_123",
    "webhook_secret": "whsec_test_123",
    "price_id": "price_123",
    "checkout_success_url": "https://app.example.com/success",
    "checkout_cancel_url": "https://app.example.com/cancel",
    "billing_portal_return_url": "https://app.example.com/billing",
}


def test_build_stripe_gateway_returns_none_when_unconfigured() -> None:
    assert (
        build_stripe_gateway(
            secret_key=None,
            webhook_secret="whsec",
            price_id="price_1",
            checkout_success_url="https://app.example.com/success",
            checkout_cancel_url="https://app.example.com/cancel",
            billing_portal_return_url="https://app.example.com/billing",
        )
        is None
    )


def test_build_stripe_gateway_succeeds_with_full_configuration() -> None:
    gateway = build_stripe_gateway(**_FULL_CONFIG)
    assert isinstance(gateway, StripePaymentGateway)


@pytest.mark.parametrize("missing_key", sorted(_FULL_CONFIG))
def test_build_stripe_gateway_is_all_or_nothing(missing_key: str) -> None:
    """A single missing field of an otherwise-full config must not crash.

    Before this fix, secrets/price present but a return URL absent would
    pass an empty string through to ``StripePaymentGateway.__init__``,
    which raises ``ValidationError`` and would crash Streamlit at startup
    instead of degrading gracefully to an unconfigured billing feature.
    """
    partial = dict(_FULL_CONFIG)
    partial[missing_key] = None  # type: ignore[assignment]

    assert build_stripe_gateway(**partial) is None


def test_checkout_session_creates_customer_and_uses_configured_urls() -> None:
    client = MagicMock()
    client.customers.create.return_value = MagicMock(id="cus_new")
    client.checkout.sessions.create.return_value = MagicMock(
        id="sess_1",
        url="https://checkout.stripe.com/sess_1",
        customer="cus_new",
    )
    gateway = _gateway(client)
    member_id = uuid.uuid4()

    result = gateway.create_checkout_session(
        member_id=member_id,
        member_email="member@example.com",
        existing_provider_customer_id=None,
    )

    assert result.provider_customer_id == "cus_new"
    assert result.provider_session_id == "sess_1"
    assert result.url == "https://checkout.stripe.com/sess_1"

    create_kwargs = client.checkout.sessions.create.call_args.args[0]
    assert (
        create_kwargs["success_url"]
        == "https://app.example.com/billing?checkout=success"
    )
    assert (
        create_kwargs["cancel_url"] == "https://app.example.com/billing?checkout=cancel"
    )
    assert create_kwargs["metadata"][MEMBER_METADATA_KEY] == str(member_id)
    assert create_kwargs["customer"] == "cus_new"


def test_checkout_session_reuses_existing_customer() -> None:
    client = MagicMock()
    client.checkout.sessions.create.return_value = MagicMock(
        id="sess_2",
        url="https://checkout.stripe.com/sess_2",
        customer="cus_existing",
    )
    gateway = _gateway(client)

    result = gateway.create_checkout_session(
        member_id=uuid.uuid4(),
        member_email="member@example.com",
        existing_provider_customer_id="cus_existing",
    )

    client.customers.create.assert_not_called()
    assert result.provider_customer_id == "cus_existing"


def test_checkout_session_requires_a_url_from_stripe() -> None:
    client = MagicMock()
    client.checkout.sessions.create.return_value = MagicMock(
        id="sess_3", url=None, customer="cus_existing"
    )
    gateway = _gateway(client)

    with pytest.raises(ValidationError):
        gateway.create_checkout_session(
            member_id=uuid.uuid4(),
            member_email="member@example.com",
            existing_provider_customer_id="cus_existing",
        )


def test_billing_portal_session_uses_configured_return_url() -> None:
    client = MagicMock()
    client.billing_portal.sessions.create.return_value = MagicMock(
        url="https://billing.stripe.com/portal/1"
    )
    gateway = _gateway(client)

    result = gateway.create_billing_portal_session(provider_customer_id="cus_1")

    assert result.url == "https://billing.stripe.com/portal/1"
    kwargs = client.billing_portal.sessions.create.call_args.args[0]
    assert kwargs["return_url"] == "https://app.example.com/billing"
    assert kwargs["customer"] == "cus_1"


# --- Realistic event fixtures ----------------------------------------------
#
# Built from plain dict payloads via ``stripe.Event.construct_from``, which
# recursively converts them into the same ``StripeObject``/subclass
# instances (``stripe.Subscription``, ``stripe.Invoice``, ...) that
# ``stripe.Webhook.construct_event`` would return for a real delivery.


def _event_payload(
    event_type: str,
    data_object: dict[str, Any],
    created: int | None = None,
) -> dict[str, Any]:
    return {
        "id": f"evt_{uuid.uuid4()}",
        "object": "event",
        "api_version": "2025-08-27.basil",
        "type": event_type,
        "created": created or int(time.time()),
        "data": {"object": data_object},
    }


def _fake_event(
    event_type: str,
    data_object: dict[str, Any],
    created: int | None = None,
) -> Any:
    return stripe.Event.construct_from(
        _event_payload(event_type, data_object, created), _API_KEY
    )


def test_verify_and_parse_webhook_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_signature_error(*args: object, **kwargs: object) -> None:
        raise stripe.SignatureVerificationError(  # type: ignore[no-untyped-call]
            "bad signature", "sig_header"
        )

    monkeypatch.setattr(stripe.Webhook, "construct_event", raise_signature_error)
    gateway = _gateway(MagicMock())

    sensitive_payload = b'{"secret_marker": "should-never-leak-into-errors"}'
    with pytest.raises(AuthenticationError) as excinfo:
        gateway.verify_and_parse_webhook(
            payload=sensitive_payload,
            signature_header="bad-sig-should-never-leak-either",
        )
    # The raw request body, signature header, and webhook secret must never
    # be echoed back in a safe, user-facing error message.
    assert "should-never-leak" not in str(excinfo.value)
    assert "whsec_test_123" not in str(excinfo.value)


def test_verify_and_parse_webhook_rejects_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_value_error(*args: object, **kwargs: object) -> None:
        raise ValueError("invalid payload")

    monkeypatch.setattr(stripe.Webhook, "construct_event", raise_value_error)
    gateway = _gateway(MagicMock())

    with pytest.raises(ValidationError):
        gateway.verify_and_parse_webhook(payload=b"not json", signature_header="sig")


def test_verify_and_parse_webhook_normalizes_checkout_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member_id = uuid.uuid4()
    session_object = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "customer": "cus_1",
        "subscription": "sub_1",
        "client_reference_id": str(member_id),
        "metadata": {},
    }
    event = _fake_event("checkout.session.completed", session_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.event_type is ProviderEventType.CHECKOUT_COMPLETED
    assert result.member_id == member_id
    assert result.provider_customer_id == "cus_1"
    assert result.provider_subscription_id == "sub_1"
    assert result.occurred_at.tzinfo is UTC


def test_verify_and_parse_webhook_normalizes_subscription_updated_basil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Basil moved ``current_period_end`` onto each subscription item."""
    period_end_ts = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    subscription_object = {
        "id": "sub_1",
        "object": "subscription",
        "customer": "cus_1",
        "status": "past_due",
        "cancel_at_period_end": True,
        "metadata": {},
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_1",
                    "object": "subscription_item",
                    "current_period_start": period_end_ts - 30 * 86400,
                    "current_period_end": period_end_ts,
                }
            ],
        },
    }
    event = _fake_event("customer.subscription.updated", subscription_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.event_type is ProviderEventType.SUBSCRIPTION_UPDATED
    assert result.provider_status == "past_due"
    assert result.cancel_at_period_end is True
    assert result.current_period_end == datetime.fromtimestamp(period_end_ts, tz=UTC)


def test_verify_and_parse_webhook_subscription_period_end_uses_max_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple subscription items: the furthest period end wins."""
    earlier_ts = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    later_ts = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())
    subscription_object = {
        "id": "sub_multi",
        "object": "subscription",
        "customer": "cus_1",
        "status": "active",
        "cancel_at_period_end": False,
        "metadata": {},
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_1",
                    "object": "subscription_item",
                    "current_period_end": earlier_ts,
                },
                {
                    "id": "si_2",
                    "object": "subscription_item",
                    "current_period_end": later_ts,
                },
            ],
        },
    }
    event = _fake_event("customer.subscription.updated", subscription_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.current_period_end == datetime.fromtimestamp(later_ts, tz=UTC)


def test_verify_and_parse_webhook_subscription_period_end_pre_basil_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-Basil-shaped subscription with no items still normalizes."""
    period_end_ts = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    subscription_object = {
        "id": "sub_legacy",
        "object": "subscription",
        "customer": "cus_1",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": period_end_ts,
        "metadata": {},
    }
    event = _fake_event("customer.subscription.updated", subscription_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.current_period_end == datetime.fromtimestamp(period_end_ts, tz=UTC)


def test_verify_and_parse_webhook_invoice_paid_extends_entitlement_basil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Basil-shaped invoice.paid: subscription id and period from the line.

    ``invoice.subscription`` is gone in favor of
    ``invoice.parent.subscription_details.subscription``, and the paid
    period must come from the line item, never the invoice-level
    ``period_end`` (which describes when the invoice was generated, not the
    period being paid for, and would immediately expire an active member).
    """
    generated_at_ts = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    paid_through_ts = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    invoice_object = {
        "id": "in_1",
        "object": "invoice",
        "customer": "cus_1",
        "amount_paid": 4_900,
        "amount_due": 4_900,
        "currency": "usd",
        # A retrospective, misleading top-level value that must be ignored.
        "period_end": generated_at_ts,
        "parent": {
            "type": "subscription_details",
            "subscription_details": {
                "subscription": "sub_1",
                "metadata": {},
            },
        },
        "lines": {
            "object": "list",
            "data": [
                {
                    "id": "il_1",
                    "object": "line_item",
                    "period": {
                        "start": generated_at_ts,
                        "end": paid_through_ts,
                    },
                }
            ],
        },
        "metadata": {},
    }
    event = _fake_event("invoice.paid", invoice_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.event_type is ProviderEventType.INVOICE_PAID
    assert result.provider_subscription_id == "sub_1"
    assert result.current_period_end == datetime.fromtimestamp(paid_through_ts, tz=UTC)
    assert result.current_period_end != datetime.fromtimestamp(generated_at_ts, tz=UTC)
    assert result.amount_minor_units == 4_900
    assert result.currency == "usd"


def test_verify_and_parse_webhook_invoice_subscription_id_pre_basil_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-Basil invoice with a top-level ``subscription`` still resolves."""
    paid_through_ts = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    invoice_object = {
        "id": "in_2",
        "object": "invoice",
        "customer": "cus_1",
        "amount_paid": 4_900,
        "currency": "usd",
        "subscription": "sub_legacy",
        "lines": {
            "object": "list",
            "data": [
                {
                    "id": "il_1",
                    "object": "line_item",
                    "period": {"start": 0, "end": paid_through_ts},
                }
            ],
        },
        "metadata": {},
    }
    event = _fake_event("invoice.paid", invoice_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.provider_subscription_id == "sub_legacy"
    assert result.current_period_end == datetime.fromtimestamp(paid_through_ts, tz=UTC)


def test_verify_and_parse_webhook_invoice_paid_without_lines_has_no_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No line items: there is deliberately no invoice-level fallback."""
    invoice_object = {
        "id": "in_3",
        "object": "invoice",
        "customer": "cus_1",
        "amount_paid": 4_900,
        "currency": "usd",
        "period_end": int(time.time()),
        "parent": {
            "type": "subscription_details",
            "subscription_details": {"subscription": "sub_1", "metadata": {}},
        },
        "lines": {"object": "list", "data": []},
        "metadata": {},
    }
    event = _fake_event("invoice.paid", invoice_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.current_period_end is None


def test_verify_and_parse_webhook_invoice_payment_failed_basil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_object = {
        "id": "in_4",
        "object": "invoice",
        "customer": "cus_1",
        "amount_due": 4_900,
        "currency": "usd",
        "parent": {
            "type": "subscription_details",
            "subscription_details": {"subscription": "sub_1", "metadata": {}},
        },
        "lines": {"object": "list", "data": []},
        "metadata": {},
    }
    event = _fake_event("invoice.payment_failed", invoice_object)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.event_type is ProviderEventType.INVOICE_PAYMENT_FAILED
    assert result.provider_subscription_id == "sub_1"
    assert result.amount_minor_units == 4_900


def test_verify_and_parse_webhook_ignores_unrecognized_event_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _fake_event("payment_intent.created", {"id": "pi_1", "metadata": {}})
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)
    gateway = _gateway(MagicMock())

    result = gateway.verify_and_parse_webhook(payload=b"{}", signature_header="sig")

    assert result.event_type is None
