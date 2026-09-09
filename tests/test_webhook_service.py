"""Health and Stripe webhook endpoint tests for the companion FastAPI service.

No live Stripe credentials or network calls are used; the payment gateway is
a deterministic fake, matching the repository and service test conventions.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from matchwell.application.pilot import PilotService
from matchwell.domain.billing import BillingWebhookEvent, ProviderEventType
from matchwell.domain.errors import AuthenticationError, ValidationError
from matchwell.domain.system_health import ComponentHealth, HealthStatus, SystemHealth
from matchwell.webhook_api.app import create_app


class _StubHealthService:
    def __init__(self, status: HealthStatus) -> None:
        self._status = status

    def check(self) -> SystemHealth:
        return SystemHealth(
            components=(
                ComponentHealth(
                    name="PostgreSQL",
                    status=self._status,
                    detail="ok" if self._status is HealthStatus.READY else "down",
                ),
            )
        )


class _StubGateway:
    def __init__(self, event: BillingWebhookEvent | None = None) -> None:
        self.event = event
        self.received_payload: bytes | None = None
        self.received_signature: str | None = None

    def verify_and_parse_webhook(
        self, *, payload: bytes, signature_header: str
    ) -> BillingWebhookEvent:
        self.received_payload = payload
        self.received_signature = signature_header
        if self.event is None:
            raise AuthenticationError("The Stripe webhook signature is invalid.")
        return self.event


def _checkout_event(member_id: uuid.UUID) -> BillingWebhookEvent:
    return BillingWebhookEvent(
        provider="stripe",
        provider_event_id="evt_1",
        event_type=ProviderEventType.CHECKOUT_COMPLETED,
        occurred_at=datetime.now(UTC),
        provider_customer_id="cus_1",
        provider_subscription_id="sub_1",
        member_id=member_id,
        provider_status=None,
        current_period_end=datetime.now(UTC),
        cancel_at_period_end=False,
        amount_minor_units=None,
        currency=None,
    )


def _client(
    *,
    gateway: _StubGateway,
    pilot_service: PilotService,
    health_status: HealthStatus = HealthStatus.READY,
) -> TestClient:
    app = create_app(
        pilot_service=pilot_service,
        payment_gateway=gateway,  # type: ignore[arg-type]
        health_service=_StubHealthService(health_status),  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_health_endpoint_reports_ready() -> None:
    client = _client(gateway=_StubGateway(), pilot_service=MagicMock())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_health_endpoint_reports_degraded_with_503() -> None:
    client = _client(
        gateway=_StubGateway(),
        pilot_service=MagicMock(),
        health_status=HealthStatus.DEGRADED,
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_webhook_rejects_missing_signature_header() -> None:
    client = _client(gateway=_StubGateway(), pilot_service=MagicMock())

    response = client.post("/webhooks/stripe", content=b"{}")

    assert response.status_code == 400


def test_webhook_rejects_invalid_signature_without_retry_status() -> None:
    client = _client(gateway=_StubGateway(event=None), pilot_service=MagicMock())

    response = client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "bad"},
    )

    assert response.status_code == 400


def test_webhook_processes_valid_signed_event() -> None:
    member_id = uuid.uuid4()
    gateway = _StubGateway(event=_checkout_event(member_id))
    pilot_service = MagicMock()
    pilot_service.process_billing_webhook_event.return_value = True
    client = _client(gateway=gateway, pilot_service=pilot_service)

    response = client.post(
        "/webhooks/stripe",
        content=b'{"id": "evt_1"}',
        headers={"stripe-signature": "t=1,v1=abc"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    pilot_service.process_billing_webhook_event.assert_called_once()
    assert gateway.received_payload == b'{"id": "evt_1"}'
    assert gateway.received_signature == "t=1,v1=abc"


def test_webhook_returns_422_when_domain_rejects_event() -> None:
    gateway = _StubGateway(event=_checkout_event(uuid.uuid4()))
    pilot_service = MagicMock()
    pilot_service.process_billing_webhook_event.side_effect = ValidationError(
        "unresolvable member"
    )
    client = _client(gateway=gateway, pilot_service=pilot_service)

    response = client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=abc"},
    )

    assert response.status_code == 422


def test_webhook_returns_503_on_database_failure_so_stripe_retries() -> None:
    from sqlalchemy.exc import SQLAlchemyError

    gateway = _StubGateway(event=_checkout_event(uuid.uuid4()))
    pilot_service = MagicMock()
    pilot_service.process_billing_webhook_event.side_effect = SQLAlchemyError(
        "connection lost"
    )
    client = _client(gateway=gateway, pilot_service=pilot_service)

    response = client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=abc"},
    )

    assert response.status_code == 503


def test_entrypoint_fails_explicitly_without_stripe_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PILOT_PRICE_ID", raising=False)
    from matchwell.infrastructure.settings import get_settings

    get_settings.cache_clear()
    sys.modules.pop("app.webhook_service", None)

    with pytest.raises(RuntimeError, match="Stripe billing settings"):
        importlib.import_module("app.webhook_service")

    sys.modules.pop("app.webhook_service", None)
    get_settings.cache_clear()
