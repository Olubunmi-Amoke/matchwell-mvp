import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BigFiveTrait(StrEnum):
    OPENNESS_INTELLECT = "openness_intellect"
    CONSCIENTIOUSNESS = "conscientiousness"
    EXTRAVERSION = "extraversion"
    AGREEABLENESS = "agreeableness"
    EMOTIONAL_STABILITY = "emotional_stability"


@dataclass(frozen=True, slots=True)
class PersonalityItem:
    id: str
    prompt: str
    trait: BigFiveTrait
    reverse_keyed: bool


@dataclass(frozen=True, slots=True)
class PersonalityInventoryView:
    assignment_id: uuid.UUID
    version: str
    title: str
    description: str
    items: tuple[PersonalityItem, ...]
    completed_at: datetime | None

    @property
    def completed(self) -> bool:
        return self.completed_at is not None


@dataclass(frozen=True, slots=True)
class PersonalityStatus:
    version: str
    completed_at: datetime | None

    @property
    def completed(self) -> bool:
        return self.completed_at is not None


PersonalityAnswers = dict[str, int]
PersonalityScores = dict[BigFiveTrait, float]


def score_inventory(
    items: tuple[PersonalityItem, ...],
    answers: PersonalityAnswers,
) -> PersonalityScores:
    """Score a complete 1–5 inventory deterministically, reversing keyed items."""
    expected = {item.id for item in items}
    if set(answers) != expected:
        raise ValueError("Answer every personality inventory item.")
    if any(value < 1 or value > 5 for value in answers.values()):
        raise ValueError("Personality inventory answers must be from 1 to 5.")
    grouped: dict[BigFiveTrait, list[int]] = {trait: [] for trait in BigFiveTrait}
    for item in items:
        value = answers[item.id]
        grouped[item.trait].append(6 - value if item.reverse_keyed else value)
    if any(len(values) != 4 for values in grouped.values()):
        raise ValueError("The personality inventory must contain four items per trait.")
    return {
        trait: round(sum(values) / len(values), 2) for trait, values in grouped.items()
    }


def compatibility_explanation(
    first: PersonalityScores | None,
    second: PersonalityScores | None,
) -> str:
    """Return a neutral explanation without revealing either member's scores."""
    if first is None or second is None:
        return "Personality reflections are optional; compatibility is not assessed."
    average_gap = sum(abs(first[trait] - second[trait]) for trait in BigFiveTrait) / 5
    if average_gap <= 0.75:
        return "Your personality reflections show several broadly similar tendencies."
    if average_gap <= 1.5:
        return (
            "Your personality reflections show a mix of similarities and differences."
        )
    return "Your personality reflections show differing tendencies that may invite conversation."
