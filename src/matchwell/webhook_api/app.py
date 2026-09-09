"""Companion FastAPI webhook service.

Streamlit Community Cloud cannot receive arbitrary verified POST requests, so
Stripe webhooks are handled by this narrowly scoped, independently deployable
service. It shares the billing application layer and PostgreSQL database with
the Streamlit app but exposes no member, counselor, or administrator UI.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from matchwell.application.health import SystemHealthService
from matchwell.application.pilot import PilotService
from matchwell.domain.errors import AuthenticationError, MatchwellError, ValidationError
from matchwell.domain.system_health import HealthStatus
from matchwell.infrastructure.billing.stripe_gateway import StripePaymentGateway
from matchwell.infrastructure.observability.logging import (
    OperationalEvent,
    configure_json_logging,
    log_event,
)

configure_json_logging()
logger = logging.getLogger(__name__)


def create_app(
    *,
    pilot_service: PilotService,
    payment_gateway: StripePaymentGateway,
    health_service: SystemHealthService,
) -> FastAPI:
    app = FastAPI(
        title="Matchwell Billing Webhooks",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    def health() -> JSONResponse:
        result = health_service.check()
        status_code = 200 if result.status is HealthStatus.READY else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": result.status.value,
                "components": [
                    {
                        "name": component.name,
                        "status": component.status.value,
                        "detail": component.detail,
                    }
                    for component in result.components
                ],
            },
        )

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request) -> dict[str, bool]:
        payload = await request.body()
        signature = request.headers.get("stripe-signature")
        if not signature:
            raise HTTPException(
                status_code=400,
                detail="Missing Stripe-Signature header.",
            )
        try:
            event = payment_gateway.verify_and_parse_webhook(
                payload=payload,
                signature_header=signature,
            )
        except AuthenticationError as error:
            # Invalid signature: reject without retry. Never log the body.
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ValidationError as error:
            # Malformed payload: reject without retry.
            raise HTTPException(status_code=400, detail=str(error)) from error

        try:
            await run_in_threadpool(
                pilot_service.process_billing_webhook_event,
                event,
            )
        except MatchwellError as error:
            log_event(
                logger,
                OperationalEvent.BILLING_WEBHOOK_UNAPPLIED,
                error_type=type(error).__name__,
            )
            raise HTTPException(
                status_code=422,
                detail="The webhook event could not be processed.",
            ) from error
        except SQLAlchemyError as error:
            log_event(
                logger,
                OperationalEvent.BILLING_WEBHOOK_UNAPPLIED,
                error_type=type(error).__name__,
                transient=True,
            )
            # 503 signals Stripe to retry; the event ID is not yet marked
            # applied so a retry safely re-attempts the same transition.
            raise HTTPException(
                status_code=503,
                detail="Temporary processing failure; retry later.",
            ) from error
        return {"received": True}

    return app
