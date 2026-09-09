import pytest

from matchwell.domain.readiness import (
    ReadinessEvaluator,
    ReadinessEvidence,
    ReadinessStage,
    RequirementCode,
)


def complete_evidence(**overrides: bool) -> ReadinessEvidence:
    values = {
        "adult_and_faith_complete": True,
        "consent_complete": True,
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
            screening_eligible=False,
        )
    )

    assert result.unmet_requirements == (
        RequirementCode.CONSENT,
        RequirementCode.SCREENING,
    )
    assert result.explanations == (
        "Accept the current participation consent",
        "Receive an eligible screening status",
    )
    assert result.stage is ReadinessStage.ONBOARDING


def test_hold_overrides_complete_evidence() -> None:
    result = ReadinessEvaluator().evaluate(complete_evidence(active_hold=True))

    assert not result.eligible
    assert result.unmet_requirements == (RequirementCode.NO_ACTIVE_HOLD,)
    assert result.stage is ReadinessStage.HELD


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
