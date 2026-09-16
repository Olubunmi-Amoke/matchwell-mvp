import uuid

import pytest

from matchwell.domain.access import AccountStatus
from matchwell.domain.pilot import (
    CounselorDecisionStatus,
    MatchingMode,
    OperationsMember,
    ScreeningStatus,
)
from matchwell.domain.readiness import (
    TOTAL_ORDINARY_REQUIREMENTS,
    ReadinessEvaluator,
    ReadinessEvidence,
    ReadinessStage,
    RequirementCode,
)


def complete_evidence(**overrides: bool) -> ReadinessEvidence:
    values = {
        "adult_and_faith_complete": True,
        "consent_complete": True,
        "community_covenant_complete": True,
        "profile_complete": True,
        "assessment_complete": True,
        "counselor_approved": True,
        "screening_eligible": True,
        "subscription_active": True,
        "active_hold": False,
    }
    values.update(overrides)
    return ReadinessEvidence(**values)


def test_complete_evidence_is_eligible() -> None:
    result = ReadinessEvaluator().evaluate(complete_evidence())

    assert result.eligible
    assert result.unmet_requirements == ()
    assert result.stage is ReadinessStage.COMMUNITY_ELIGIBLE


def test_unmet_requirements_are_ordered_and_explainable() -> None:
    result = ReadinessEvaluator().evaluate(
        complete_evidence(
            consent_complete=False,
            community_covenant_complete=False,
            screening_eligible=False,
        )
    )

    assert result.unmet_requirements == (
        RequirementCode.CONSENT,
        RequirementCode.COMMUNITY_COVENANT,
        RequirementCode.SCREENING,
    )
    assert result.explanations == (
        "Accept the current participation consent",
        "Affirm the current faith and community covenant",
        "Receive an eligible screening status",
    )
    assert result.stage is ReadinessStage.ONBOARDING


def test_hold_overrides_complete_evidence() -> None:
    result = ReadinessEvaluator().evaluate(complete_evidence(active_hold=True))

    assert not result.eligible
    assert result.unmet_requirements == (RequirementCode.NO_ACTIVE_HOLD,)
    assert result.stage is ReadinessStage.HELD
    assert result.completed_ordinary_requirement_count == 9
    assert result.completed_ordinary_requirement_count == TOTAL_ORDINARY_REQUIREMENTS


def test_hold_does_not_distort_empty_ordinary_progress() -> None:
    result = ReadinessEvaluator().evaluate(
        complete_evidence(
            adult_and_faith_complete=False,
            consent_complete=False,
            community_covenant_complete=False,
            profile_complete=False,
            assessment_complete=False,
            counselor_approved=False,
            screening_eligible=False,
            subscription_active=False,
            active_hold=True,
        )
    )

    assert result.stage is ReadinessStage.HELD
    assert result.completed_ordinary_requirement_count == 0
    assert (
        0 <= result.completed_ordinary_requirement_count <= TOTAL_ORDINARY_REQUIREMENTS
    )


def test_operations_member_uses_ordinary_progress_without_hold_penalty() -> None:
    readiness = ReadinessEvaluator().evaluate(complete_evidence(active_hold=True))
    member = OperationsMember(
        id=uuid.uuid4(),
        email="member@example.com",
        display_name="Member",
        center_id=uuid.uuid4(),
        counselor_id=None,
        counselor_status=CounselorDecisionStatus.APPROVED,
        screening_status=ScreeningStatus.ELIGIBLE,
        hold_active=True,
        readiness=readiness,
        account_status=AccountStatus.ACTIVE,
        matching_mode=MatchingMode.COUNSELOR_BASED,
    )

    assert member.readiness_completed_count == 9


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"assessment_complete": False}, ReadinessStage.ASSESSMENT),
        ({"counselor_approved": False}, ReadinessStage.COUNSELOR_INTAKE),
        ({"screening_eligible": False}, ReadinessStage.SCREENING),
        ({"subscription_active": False}, ReadinessStage.BILLING),
    ],
)
def test_stage_tracks_first_unmet_requirement(
    overrides: dict[str, bool],
    expected: ReadinessStage,
) -> None:
    result = ReadinessEvaluator().evaluate(complete_evidence(**overrides))

    assert result.stage is expected


def test_unsubscribed_member_is_ineligible_for_community_unlock() -> None:
    result = ReadinessEvaluator().evaluate(complete_evidence(subscription_active=False))

    assert not result.eligible
    assert result.unmet_requirements == (RequirementCode.SUBSCRIPTION,)
    assert result.explanations == ("Activate the Matchwell Pilot subscription",)
    assert result.stage is ReadinessStage.BILLING
