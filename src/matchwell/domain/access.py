import re
import uuid
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    MEMBER = "member"
    COUNSELOR = "counselor"
    ADMIN = "admin"


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
