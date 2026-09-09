"""Entrypoint for the companion FastAPI Stripe webhook service.

Run independently from the Streamlit app, for example:

    uv run uvicorn app.webhook_service:app --host 0.0.0.0 --port 8000

It shares the billing application layer and PostgreSQL database with the
Streamlit app but has no member, counselor, or administrator UI, and it is
deployed separately because Streamlit Community Cloud cannot receive
arbitrary verified POST requests.
"""

from datetime import timedelta

from fastapi import FastAPI

from matchwell.application.health import SystemHealthService
from matchwell.application.pilot import PilotService
from matchwell.domain.matching import MatchScorer
from matchwell.domain.readiness import ReadinessEvaluator
from matchwell.infrastructure.billing.stripe_gateway import build_stripe_gateway
from matchwell.infrastructure.persistence.database import (
    DatabaseSessionFactory,
    SqlAlchemyDatabaseProbe,
    create_database_engine,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)
from matchwell.infrastructure.settings import get_settings
from matchwell.webhook_api.app import create_app


def build_app() -> FastAPI:
    settings = get_settings()
    database_url = settings.reveal_database_url()
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not configured.")

    payment_gateway = build_stripe_gateway(
        secret_key=settings.reveal_stripe_secret_key(),
        webhook_secret=settings.reveal_stripe_webhook_secret(),
        price_id=settings.stripe_pilot_price_id,
        checkout_success_url=settings.checkout_success_url,
        checkout_cancel_url=settings.checkout_cancel_url,
        billing_portal_return_url=settings.billing_portal_return_url,
    )
    if payment_gateway is None:
        raise RuntimeError(
            "Stripe billing settings are not fully configured: "
            "STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, and "
            "STRIPE_PILOT_PRICE_ID are all required."
        )

    engine = create_database_engine(database_url)
    repository = SqlAlchemyPilotRepository(
        DatabaseSessionFactory(engine),
        ReadinessEvaluator(),
        MatchScorer(),
        payment_gateway=payment_gateway,
        grace_period=timedelta(days=settings.billing_grace_period_days),
    )
    pilot_service = PilotService(repository, settings.normalized_admin_emails())
    health_service = SystemHealthService(SqlAlchemyDatabaseProbe(database_url))
    return create_app(
        pilot_service=pilot_service,
        payment_gateway=payment_gateway,
        health_service=health_service,
    )


app = build_app()
