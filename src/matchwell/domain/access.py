import re
import uuid
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    MEMBER = "member"
    COUNSELOR = "counselor"
    ADMIN = "admin"


class AccountStatus(StrEnum):
    """Explicit account state checked on every sign-in before returning an
    actor. Disabled accounts never resolve to an ``AuthenticatedUser``."""

    ACTIVE = "active"
    DISABLED = "disabled"


class AccountDisableReasonCode(StrEnum):
    """Safe, constrained reason codes for disabling an account.

    Never free text: only these operationally meaningful codes may enter
    audit metadata or outbox payloads.
    """

    SAFETY_CONCERN = "safety_concern"
    POLICY_VIOLATION = "policy_violation"
    MEMBER_REQUESTED = "member_requested"
    DUPLICATE_ACCOUNT = "duplicate_account"
    INACTIVE_ACCOUNT = "inactive_account"
    OTHER_OPERATIONAL = "other_operational"


class AccountReactivateReasonCode(StrEnum):
    """Safe, constrained reason codes for reactivating an account."""

    SAFETY_CONCERN_RESOLVED = "safety_concern_resolved"
    MEMBER_REQUESTED = "member_requested"
    ADMIN_ALLOWLIST_RESTORED = "admin_allowlist_restored"
    ENTERED_IN_ERROR = "entered_in_error"
    OTHER_OPERATIONAL = "other_operational"


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    issuer: str
    subject: str
    email: str
    email_verified: bool
    name: str


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: uuid.UUID
    email: str
    name: str
    role: Role
    center_id: uuid.UUID


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    return email
