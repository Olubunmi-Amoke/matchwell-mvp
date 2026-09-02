from datetime import date
from typing import cast
from unittest.mock import MagicMock

import pytest

from matchwell.application.pilot import PilotRepository, PilotService
from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from matchwell.domain.pilot import ProfileInput


def service() -> PilotService:
    repository = cast(PilotRepository, MagicMock())
    return PilotService(repository, frozenset())


def actor(role: Role) -> AuthenticatedUser:
    import uuid

    return AuthenticatedUser(
        id=uuid.uuid4(),
        email=f"{role.value}@example.com",
        name=role.value.title(),
        role=role,
        center_id=uuid.uuid4(),
    )


def test_unverified_google_email_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().sign_in(
            OidcIdentity(
                issuer="https://accounts.google.com",
                subject="subject",
                email="member@example.com",
                email_verified=False,
                name="Member",
            )
        )


def test_non_google_identity_is_rejected() -> None:
    with pytest.raises(AuthenticationError):
        service().sign_in(
            OidcIdentity(
                issuer="https://untrusted.example.com",
                subject="subject",
                email="member@example.com",
                email_verified=True,
                name="Member",
            )
        )


def test_member_workspace_rejects_admin() -> None:
    with pytest.raises(AuthorizationError):
        service().profile(actor(Role.ADMIN))


def test_underage_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().save_profile(
            actor(Role.MEMBER),
            ProfileInput(
                display_name="Member",
                birth_date=date.today().replace(year=date.today().year - 17),
                faith_affirmed=True,
                relationship_intent="Committed relationship",
                denomination="",
                city="Nashville",
                state="Tennessee",
            ),
        )


def test_faith_affirmation_is_required() -> None:
    with pytest.raises(ValidationError):
        service().save_profile(
            actor(Role.MEMBER),
            ProfileInput(
                display_name="Member",
                birth_date=date(1990, 1, 1),
                faith_affirmed=False,
                relationship_intent="Committed relationship",
                denomination="",
                city="Nashville",
                state="Tennessee",
            ),
        )
