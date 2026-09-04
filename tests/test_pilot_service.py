import uuid
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
from matchwell.domain.matching import (
    CounselorReviewDecision,
    Gender,
    MatchPreferencesInput,
    SafetyCategory,
)
from matchwell.domain.pilot import ProfileInput


def service() -> PilotService:
    repository = cast(PilotRepository, MagicMock())
    return PilotService(repository, frozenset())


def actor(role: Role) -> AuthenticatedUser:
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


def test_admin_workspace_rejects_member() -> None:
    with pytest.raises(AuthorizationError):
        service().generate_candidates(actor(Role.MEMBER))


@pytest.mark.parametrize("role", [Role.MEMBER, Role.COUNSELOR])
def test_only_admin_can_view_candidate_diagnostics(role: Role) -> None:
    with pytest.raises(AuthorizationError):
        service().candidate_generation_diagnostics(actor(role))


@pytest.mark.parametrize("role", [Role.MEMBER, Role.COUNSELOR])
def test_only_admin_can_reassign_member_role(role: Role) -> None:
    with pytest.raises(AuthorizationError):
        service().reassign_member_to_counselor(
            actor(role),
            uuid.uuid4(),
            "member@example.com",
            "counselor-onboarding",
        )


@pytest.mark.parametrize("role", [Role.MEMBER, Role.COUNSELOR])
def test_only_admin_can_reassign_counselor_role(role: Role) -> None:
    with pytest.raises(AuthorizationError):
        service().reassign_counselor_to_member(
            actor(role),
            uuid.uuid4(),
            "counselor@example.com",
            "returning-to-member-journey",
        )


def test_admin_role_reassignment_is_forwarded_with_normalized_input() -> None:
    repository = MagicMock()
    pilot_service = PilotService(cast(PilotRepository, repository), frozenset())
    admin = actor(Role.ADMIN)
    member_id = uuid.uuid4()

    pilot_service.reassign_member_to_counselor(
        admin,
        member_id,
        " MEMBER@EXAMPLE.COM ",
        " counselor-onboarding ",
    )

    repository.reassign_member_to_counselor.assert_called_once_with(
        admin,
        member_id,
        "member@example.com",
        "counselor-onboarding",
    )


def test_admin_counselor_reassignment_is_forwarded_with_normalized_input() -> None:
    repository = MagicMock()
    pilot_service = PilotService(cast(PilotRepository, repository), frozenset())
    admin = actor(Role.ADMIN)
    counselor_id = uuid.uuid4()

    pilot_service.reassign_counselor_to_member(
        admin,
        counselor_id,
        " COUNSELOR@EXAMPLE.COM ",
        " returning-to-member-journey ",
    )

    repository.reassign_counselor_to_member.assert_called_once_with(
        admin,
        counselor_id,
        "counselor@example.com",
        "returning-to-member-journey",
    )


@pytest.mark.parametrize(
    ("confirmation_email", "reason_code"),
    [
        ("", "counselor-onboarding"),
        ("member@example.com", "free-form explanation"),
    ],
)
def test_invalid_role_reassignment_confirmation_is_rejected(
    confirmation_email: str,
    reason_code: str,
) -> None:
    with pytest.raises(ValidationError):
        service().reassign_member_to_counselor(
            actor(Role.ADMIN),
            uuid.uuid4(),
            confirmation_email,
            reason_code,
        )


@pytest.mark.parametrize(
    ("confirmation_email", "reason_code"),
    [
        ("", "returning-to-member-journey"),
        ("counselor@example.com", "free-form explanation"),
    ],
)
def test_invalid_counselor_reassignment_confirmation_is_rejected(
    confirmation_email: str,
    reason_code: str,
) -> None:
    with pytest.raises(ValidationError):
        service().reassign_counselor_to_member(
            actor(Role.ADMIN),
            uuid.uuid4(),
            confirmation_email,
            reason_code,
        )


def test_counselor_workspace_rejects_admin() -> None:
    with pytest.raises(AuthorizationError):
        service().candidate_queue(actor(Role.ADMIN))


def test_member_workspace_rejects_counselor_for_matching_preferences() -> None:
    with pytest.raises(AuthorizationError):
        service().save_match_preferences(
            actor(Role.COUNSELOR),
            MatchPreferencesInput(
                gender=Gender.MAN,
                min_partner_age=25,
                max_partner_age=35,
            ),
        )


def test_partner_age_below_minimum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().save_match_preferences(
            actor(Role.MEMBER),
            MatchPreferencesInput(
                gender=Gender.MAN,
                min_partner_age=10,
                max_partner_age=35,
            ),
        )


def test_partner_age_above_maximum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().save_match_preferences(
            actor(Role.MEMBER),
            MatchPreferencesInput(
                gender=Gender.MAN,
                min_partner_age=25,
                max_partner_age=150,
            ),
        )


def test_min_partner_age_over_max_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().save_match_preferences(
            actor(Role.MEMBER),
            MatchPreferencesInput(
                gender=Gender.MAN,
                min_partner_age=40,
                max_partner_age=30,
            ),
        )


def test_pending_counselor_review_decision_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().review_candidate(
            actor(Role.COUNSELOR),
            uuid.uuid4(),
            CounselorReviewDecision.PENDING,
        )


def test_member_cannot_block_self() -> None:
    with pytest.raises(ValidationError):
        member = actor(Role.MEMBER)
        service().block_member(
            member,
            member.id,
            SafetyCategory.HARASSMENT,
            None,
        )


def test_member_cannot_report_self() -> None:
    with pytest.raises(ValidationError):
        member = actor(Role.MEMBER)
        service().report_member(
            member,
            member.id,
            SafetyCategory.HARASSMENT,
            "Concerning behavior during our conversation.",
        )


def test_report_requires_minimum_context() -> None:
    with pytest.raises(ValidationError):
        service().report_member(
            actor(Role.MEMBER),
            uuid.uuid4(),
            SafetyCategory.HARASSMENT,
            "too short",
        )


def test_report_context_over_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        service().report_member(
            actor(Role.MEMBER),
            uuid.uuid4(),
            SafetyCategory.HARASSMENT,
            "x" * 501,
        )
