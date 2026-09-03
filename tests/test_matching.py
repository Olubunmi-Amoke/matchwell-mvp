import uuid

from matchwell.domain.matching import (
    CandidateEvidence,
    Gender,
    MatchScorer,
)


def candidate(**overrides: object) -> CandidateEvidence:
    values: dict[str, object] = {
        "member_id": uuid.uuid4(),
        "gender": Gender.MAN,
        "age": 30,
        "min_partner_age": 25,
        "max_partner_age": 35,
        "city": "Nashville",
        "state": "Tennessee",
        "denomination": "Baptist",
        "relationship_intent": "Seeking a committed Christian marriage",
    }
    values.update(overrides)
    return CandidateEvidence(**values)  # type: ignore[arg-type]


def test_same_gender_pair_is_never_reciprocally_compatible() -> None:
    scorer = MatchScorer()
    a = candidate(gender=Gender.MAN)
    b = candidate(gender=Gender.MAN)

    assert not scorer.is_reciprocally_compatible(a, b)


def test_man_woman_pair_within_mutual_age_range_is_compatible() -> None:
    scorer = MatchScorer()
    a = candidate(gender=Gender.MAN, age=30, min_partner_age=25, max_partner_age=35)
    b = candidate(gender=Gender.WOMAN, age=28, min_partner_age=28, max_partner_age=40)

    assert scorer.is_reciprocally_compatible(a, b)


def test_asymmetric_age_window_breaks_reciprocal_compatibility() -> None:
    scorer = MatchScorer()
    # a only wants a partner aged 25-30, but b is 45; b's own preferences would
    # accept a, so this exercises the reciprocal (both-directions) check.
    a = candidate(gender=Gender.MAN, age=28, min_partner_age=25, max_partner_age=30)
    b = candidate(gender=Gender.WOMAN, age=45, min_partner_age=25, max_partner_age=50)

    assert not scorer.is_reciprocally_compatible(a, b)


def test_scoring_is_deterministic_and_explainable() -> None:
    scorer = MatchScorer()
    a = candidate(city="Nashville", state="Tennessee", denomination="Baptist")
    b = candidate(city="Nashville", state="Tennessee", denomination="Baptist")

    first = scorer.score(a, b)
    second = scorer.score(a, b)

    assert first.total == second.total
    assert len(first.explanations) == 4
    labels = {item.label for item in first.contributions}
    assert labels == {
        "Location",
        "Denomination",
        "Age preference",
        "Relationship intent",
    }


def test_scoring_never_reads_assessment_or_screening_fields() -> None:
    # CandidateEvidence structurally excludes assessment answers, screening
    # details, counselor notes, exact birth dates, emails, and contact details.
    fields = set(CandidateEvidence.__dataclass_fields__)
    assert fields == {
        "member_id",
        "gender",
        "age",
        "min_partner_age",
        "max_partner_age",
        "city",
        "state",
        "denomination",
        "relationship_intent",
    }


def test_matching_city_scores_higher_than_matching_state_only() -> None:
    scorer = MatchScorer()
    base = candidate(city="Nashville", state="Tennessee")
    same_city = candidate(city="Nashville", state="Tennessee")
    same_state_only = candidate(city="Knoxville", state="Tennessee")
    no_overlap = candidate(city="Denver", state="Colorado")

    same_city_score = scorer.score(base, same_city).total
    same_state_score = scorer.score(base, same_state_only).total
    no_overlap_score = scorer.score(base, no_overlap).total

    assert same_city_score > same_state_score > no_overlap_score


def test_relationship_intent_overlap_increases_score() -> None:
    scorer = MatchScorer()
    base = candidate(relationship_intent="Seeking a committed Christian marriage")
    aligned = candidate(relationship_intent="Committed Christian marriage is my goal")
    unaligned = candidate(relationship_intent="Casual dating only for now")

    aligned_score = scorer.score(base, aligned).total
    unaligned_score = scorer.score(base, unaligned).total

    assert aligned_score > unaligned_score
