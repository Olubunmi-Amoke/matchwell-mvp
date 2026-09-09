import json
import logging
import uuid

import pytest

from matchwell.infrastructure.observability.logging import (
    JsonFormatter,
    OperationalEvent,
    SensitiveFieldError,
    log_event,
)


def _make_logger(name: str) -> tuple[logging.Logger, logging.Handler, list[str]]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    records: list[str] = []

    class _CollectingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = _CollectingHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger, handler, records


def test_log_event_emits_single_line_json_with_correlation_id() -> None:
    logger, _, records = _make_logger("test.observability.basic")
    correlation_id = log_event(
        logger,
        OperationalEvent.ACCOUNT_DISABLED,
        subject_role="member",
        reason_code="safety_concern",
    )
    assert len(records) == 1
    document = json.loads(records[0])
    assert document["event"] == "account.disabled"
    assert document["correlation_id"] == str(correlation_id)
    assert document["fields"] == {
        "subject_role": "member",
        "reason_code": "safety_concern",
    }
    assert document["level"] == "INFO"


def test_log_event_generates_correlation_id_when_not_supplied() -> None:
    logger, _, records = _make_logger("test.observability.correlation")
    log_event(logger, OperationalEvent.AUTH_SIGN_IN_REJECTED)
    document = json.loads(records[0])
    # Must be a valid UUID string.
    uuid.UUID(document["correlation_id"])


def test_log_event_accepts_a_caller_supplied_correlation_id() -> None:
    logger, _, records = _make_logger("test.observability.explicit-correlation")
    given = uuid.uuid4()
    returned = log_event(
        logger,
        OperationalEvent.MIGRATION_FAILED,
        correlation_id=given,
    )
    assert returned == given
    document = json.loads(records[0])
    assert document["correlation_id"] == str(given)


@pytest.mark.parametrize(
    "field_name",
    [
        "password",
        "secret_key",
        "auth_token",
        "session_cookie",
        "message_body",
        "reflection_text",
        "counselor_note",
        "assessment_answer_1",
        "member_email",
        "display_name",
        "raw_payload",
    ],
)
def test_log_event_refuses_sensitive_field_names(field_name: str) -> None:
    logger, _, _records = _make_logger(f"test.observability.denylist.{field_name}")
    fields: dict[str, str] = {field_name: "x"}
    with pytest.raises(SensitiveFieldError):
        log_event(logger, OperationalEvent.ACCOUNT_DISABLED, **fields)  # type: ignore[arg-type]


def test_log_event_refuses_non_primitive_field_values() -> None:
    logger, _, _records = _make_logger("test.observability.non-primitive")
    with pytest.raises(SensitiveFieldError):
        log_event(
            logger,
            OperationalEvent.ACCOUNT_DISABLED,
            details={"nested": "object"},
        )


def test_log_event_accepts_uuid_and_safe_override_field_names() -> None:
    logger, _, records = _make_logger("test.observability.overrides")
    subject_id = uuid.uuid4()
    log_event(
        logger,
        OperationalEvent.BILLING_WEBHOOK_UNAPPLIED,
        subject_id=subject_id,
        event_name="checkout.completed",
        provider_event_id="evt_123",
    )
    document = json.loads(records[0])
    assert document["fields"]["subject_id"] == str(subject_id)
    assert document["fields"]["event_name"] == "checkout.completed"
    assert document["fields"]["provider_event_id"] == "evt_123"
