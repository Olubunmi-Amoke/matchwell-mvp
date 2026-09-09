"""Structured JSON operational logging with correlation IDs and redaction.

Every log record emitted through :func:`log_event` is a single JSON object
on one line, suitable for ingestion by any log aggregator without further
parsing. This module is deliberately conservative about what it will ever
emit:

* Only an enumerated :class:`OperationalEvent` name, a level, a correlation
  ID, and explicit keyword "safe fields" may be logged -- never a raw
  exception message, request body, or arbitrary object.
* Every field name is checked against :data:`SENSITIVE_KEY_DENYLIST` (a
  case-insensitive substring match) before being emitted; a match raises
  immediately rather than silently emitting the value, so a mistake at a
  call site fails loudly in tests instead of leaking quietly in
  production.
* Only JSON-primitive field values are accepted (``str``, ``int``,
  ``float``, ``bool``, ``None``, ``uuid.UUID``, ``datetime``); anything
  else raises. This keeps this module from ever needing to know how to
  "redact" an arbitrary nested object.

This module never logs message bodies, reflections, assessment answers,
counselor notes, screening details, tokens, cookies, secrets, raw webhook
bodies, emails/names, or unnecessary identifiers. Call sites are
responsible for choosing safe field names and values; this module is
responsible for refusing anything that looks unsafe by name.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

# Case-insensitive substrings that must never appear in a logged field name.
# Deliberately broad: a false positive (a safe field renamed to avoid this
# list) is a much smaller cost than a false negative.
SENSITIVE_KEY_DENYLIST: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "signature",
    "database_url",
    "message",
    "reflection",
    "note",
    "answer",
    "screening_report",
    "assessment_answer",
    "email",
    "name",
    "body",
    "payload",
    "card",
    "bank",
    "ssn",
)

# Field names that are exempt from the denylist because they are safe,
# structural identifiers whose names happen to contain a denylisted
# substring (e.g. "event_name" contains "name"; "event_id" is fine).
_SAFE_FIELD_OVERRIDES: frozenset[str] = frozenset(
    {
        "event_name",
        "event_type",
        "table_name",
        "provider_event_id",
    }
)

_JSON_PRIMITIVES = (str, int, float, bool, type(None))


class OperationalEvent(StrEnum):
    """Enumerated, safe event names. Prefer adding a new member here over
    inventing an ad hoc string at a call site."""

    AUTH_SIGN_IN_REJECTED = "auth.sign_in_rejected"
    AUTH_SIGN_IN_DENIED_DISABLED = "auth.sign_in_denied_disabled"
    ACCOUNT_DISABLED = "account.disabled"
    ACCOUNT_REACTIVATED = "account.reactivated"
    ADMIN_ACCESS_REVOKED = "identity.admin_access_revoked"
    ADMIN_ACCESS_GRANTED = "identity.admin_access_granted"
    BILLING_WEBHOOK_UNAPPLIED = "provider.billing_webhook_unapplied"
    SCREENING_EVENT_UNAPPLIED = "provider.screening_event_unapplied"
    DATABASE_UNAVAILABLE = "health.database_unavailable"
    MIGRATION_FAILED = "health.migration_failed"
    ADMIN_QUEUE_OVERDUE = "operations.queue_overdue"


class SensitiveFieldError(ValueError):
    """Raised when a call site tries to log a field that looks sensitive."""


def _check_field(key: str, value: Any) -> None:
    if key in _SAFE_FIELD_OVERRIDES:
        return
    lowered = key.lower()
    for marker in SENSITIVE_KEY_DENYLIST:
        if marker in lowered:
            raise SensitiveFieldError(
                f"Refusing to log field '{key}': name matches denylisted "
                f"marker '{marker}'."
            )
    if isinstance(value, uuid.UUID | datetime):
        return
    if not isinstance(value, _JSON_PRIMITIVES):
        raise SensitiveFieldError(
            f"Refusing to log field '{key}': only JSON-primitive values, "
            "UUIDs, and datetimes are accepted."
        )


class JsonFormatter(logging.Formatter):
    """Renders each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        fields = getattr(record, "safe_fields", None)
        if fields:
            document["fields"] = fields
        return json.dumps(document, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger's handlers.

    Idempotent: safe to call more than once (e.g. once per Streamlit
    rerun) without stacking duplicate handlers.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]


def log_event(
    logger: logging.Logger,
    event: OperationalEvent,
    *,
    level: int = logging.INFO,
    correlation_id: uuid.UUID | str | None = None,
    **safe_fields: Any,
) -> uuid.UUID | str:
    """Emit one structured, redaction-checked JSON log record.

    Returns the correlation ID used (generated if not supplied) so callers
    can thread it through a matching audit event if useful.
    """
    for key, value in safe_fields.items():
        _check_field(key, value)
    resolved_correlation_id = correlation_id or uuid.uuid4()
    logger.log(
        level,
        event.value,
        extra={
            "event": event.value,
            "correlation_id": str(resolved_correlation_id),
            "safe_fields": {k: str(v) for k, v in safe_fields.items()},
        },
    )
    return resolved_correlation_id
