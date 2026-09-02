from dataclasses import dataclass
from enum import StrEnum


class RequirementCode(StrEnum):
    ADULT_AND_FAITH = "adult_and_faith"
    CONSENT = "consent"
    PROFILE = "profile"
    ASSESSMENT = "assessment"
    COUNSELOR = "counselor"
    SCREENING = "screening"
    NO_ACTIVE_HOLD = "no_active_hold"


class ReadinessStage(StrEnum):
    ONBOARDING = "onboarding"
    ASSESSMENT = "assessment"
    COUNSELOR_INTAKE = "counselor_intake"
    SCREENING = "screening"
    HELD = "held"
    COMMUNITY_ELIGIBLE = "community_eligible"


REQUIREMENT_LABELS: dict[RequirementCode, str] = {
    RequirementCode.ADULT_AND_FAITH: "Confirm adult eligibility and Christian faith",
    RequirementCode.CONSENT: "Accept the current participation consent",
    RequirementCode.PROFILE: "Complete the readiness profile",
    RequirementCode.ASSESSMENT: "Complete the readiness assessment",
    RequirementCode.COUNSELOR: "Receive counselor intake approval",
    RequirementCode.SCREENING: "Receive an eligible screening status",
    RequirementCode.NO_ACTIVE_HOLD: "Resolve the active account hold",
}


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    adult_and_faith_complete: bool
    consent_complete: bool
    profile_complete: bool
    assessment_complete: bool
    counselor_approved: bool
    screening_eligible: bool
    active_hold: bool


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    eligible: bool
    unmet_requirements: tuple[RequirementCode, ...]

    @property
    def explanations(self) -> tuple[str, ...]:
        return tuple(REQUIREMENT_LABELS[code] for code in self.unmet_requirements)

    @property
    def stage(self) -> ReadinessStage:
        if RequirementCode.NO_ACTIVE_HOLD in self.unmet_requirements:
            return ReadinessStage.HELD
        if any(
            requirement in self.unmet_requirements
            for requirement in (
                RequirementCode.ADULT_AND_FAITH,
                RequirementCode.CONSENT,
                RequirementCode.PROFILE,
            )
        ):
            return ReadinessStage.ONBOARDING
        if RequirementCode.ASSESSMENT in self.unmet_requirements:
            return ReadinessStage.ASSESSMENT
        if RequirementCode.COUNSELOR in self.unmet_requirements:
            return ReadinessStage.COUNSELOR_INTAKE
        if RequirementCode.SCREENING in self.unmet_requirements:
            return ReadinessStage.SCREENING
        return ReadinessStage.COMMUNITY_ELIGIBLE


class ReadinessEvaluator:
    _ordered_checks = (
        (RequirementCode.ADULT_AND_FAITH, "adult_and_faith_complete"),
        (RequirementCode.CONSENT, "consent_complete"),
        (RequirementCode.PROFILE, "profile_complete"),
        (RequirementCode.ASSESSMENT, "assessment_complete"),
        (RequirementCode.COUNSELOR, "counselor_approved"),
        (RequirementCode.SCREENING, "screening_eligible"),
    )

    def evaluate(self, evidence: ReadinessEvidence) -> ReadinessResult:
        unmet = [
            code
            for code, attribute in self._ordered_checks
            if not getattr(evidence, attribute)
        ]
        if evidence.active_hold:
            unmet.append(RequirementCode.NO_ACTIVE_HOLD)
        return ReadinessResult(
            eligible=not unmet,
            unmet_requirements=tuple(unmet),
        )
