import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from covenant_helpers import seed_community_covenant
from sqlalchemy import select

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, Role
from matchwell.domain.errors import ConflictError, ValidationError
from matchwell.domain.matching import (
    Gender,
    ProposalStatus,
    SuggestionInterestStatus,
)
from matchwell.domain.personality import (
    BigFiveTrait,
    PersonalityItem,
    compatibility_explanation,
    score_inventory,
)
from matchwell.domain.pilot import (
    CommunityAssignmentReasonCode,
    MatchingMode,
)
from matchwell.domain.readiness import ReadinessEvaluator
from matchwell.infrastructure.persistence.database import (
    Base,
    DatabaseSessionFactory,
    create_database_engine,
)
from matchwell.infrastructure.persistence.models import (
    AssessmentAssignmentRecord,
    AssessmentDefinitionRecord,
    AuditEventRecord,
    CenterRecord,
    CommunityCovenantAcceptanceRecord,
    CommunityRecord,
    ConsentAcceptanceRecord,
    ConsentVersionRecord,
    CounselorAssignmentRecord,
    CounselorDecisionRecord,
    MatchProposalParticipantClaimRecord,
    MatchProposalRecord,
    MemberCommunityAssignmentRecord,
    MemberMatchPreferencesRecord,
    MemberProfileRecord,
    OutboxMessageRecord,
    PersonalityInventoryDefinitionRecord,
    PersonalityInventoryResponseRecord,
    PersonalityInventoryScoreRecord,
    PilotPlanRecord,
    ScreeningCaseRecord,
    SelfPacedSuggestionInterestRecord,
    SubscriptionRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)


def _items() -> tuple[PersonalityItem, ...]:
    result: list[PersonalityItem] = []
    for trait in BigFiveTrait:
        for index in range(4):
            result.append(
                PersonalityItem(
                    id=f"{trait.value}-{index}",
                    prompt=f"{trait.value} item {index}",
                    trait=trait,
                    reverse_keyed=index >= 2,
                )
            )
    return tuple(result)


def test_personality_scoring_is_complete_deterministic_and_reverse_keyed() -> None:
    items = _items()
    answers = {item.id: (5 if not item.reverse_keyed else 1) for item in items}
    assert score_inventory(items, answers) == {trait: 5.0 for trait in BigFiveTrait}
    with pytest.raises(ValueError, match="every"):
        score_inventory(items, dict(list(answers.items())[:-1]))
    with pytest.raises(ValueError, match="1 to 5"):
        score_inventory(items, answers | {items[0].id: 6})


def test_personality_explanations_are_neutral_and_do_not_reveal_scores() -> None:
    high = {trait: 5.0 for trait in BigFiveTrait}
    low = {trait: 1.0 for trait in BigFiveTrait}
    assert "optional" in compatibility_explanation(None, high)
    explanation = compatibility_explanation(high, low)
    assert "differing tendencies" in explanation
    assert "5.0" not in explanation
    assert "1.0" not in explanation


@pytest.fixture
def priority3() -> tuple[
    PilotService,
    DatabaseSessionFactory,
    AuthenticatedUser,
    AuthenticatedUser,
    AuthenticatedUser,
    uuid.UUID,
]:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    now = datetime.now(UTC)
    center_id = uuid.uuid4()
    counselor_community_id = uuid.uuid4()
    self_paced_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    counselor_id = uuid.uuid4()
    member_a_id = uuid.uuid4()
    member_b_id = uuid.uuid4()
    consent_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    personality_id = uuid.uuid4()
    items = _items()
    with sessions.session() as session, session.begin():
        session.add(CenterRecord(id=center_id, slug="matchwell-pilot", name="Center"))
        session.add_all(
            (
                CommunityRecord(
                    id=counselor_community_id,
                    center_id=center_id,
                    slug="intentional-relationships",
                    name="Intentional Relationships Community",
                    matching_mode=MatchingMode.COUNSELOR_BASED.value,
                ),
                CommunityRecord(
                    id=self_paced_id,
                    center_id=center_id,
                    slug="self-paced-pilot",
                    name="Self-Paced Pilot",
                    matching_mode=MatchingMode.SELF_PACED.value,
                ),
            )
        )
        users = (
            UserRecord(
                id=admin_id,
                center_id=center_id,
                oidc_issuer="issuer",
                oidc_subject="admin",
                email="admin@example.com",
                name="Admin",
                role=Role.ADMIN.value,
            ),
            UserRecord(
                id=counselor_id,
                center_id=center_id,
                oidc_issuer="issuer",
                oidc_subject="counselor",
                email="counselor@example.com",
                name="Counselor",
                role=Role.COUNSELOR.value,
            ),
            UserRecord(
                id=member_a_id,
                center_id=center_id,
                oidc_issuer="issuer",
                oidc_subject="a",
                email="a@example.com",
                name="Alex",
                role=Role.MEMBER.value,
            ),
            UserRecord(
                id=member_b_id,
                center_id=center_id,
                oidc_issuer="issuer",
                oidc_subject="b",
                email="b@example.com",
                name="Blair",
                role=Role.MEMBER.value,
            ),
        )
        session.add_all(users)
        session.flush()
        session.add(
            ConsentVersionRecord(
                id=consent_id,
                policy_key="pilot",
                version="1",
                title="Consent",
                body_markdown="Consent",
                effective_at=now,
                is_active=True,
            )
        )
        covenant_id = seed_community_covenant(session)
        session.add(
            AssessmentDefinitionRecord(
                id=assessment_id,
                key="readiness",
                version="1",
                title="Readiness",
                description="Readiness",
                questions=[{"id": "q", "prompt": "Q"}],
                is_active=True,
            )
        )
        session.add(
            PersonalityInventoryDefinitionRecord(
                id=personality_id,
                key="ipip-big-five-20",
                version="1.0",
                title="Optional Personality Inventory",
                description="Non-diagnostic.",
                items=[
                    {
                        "id": item.id,
                        "prompt": item.prompt,
                        "trait": item.trait.value,
                        "reverse_keyed": item.reverse_keyed,
                    }
                    for item in items
                ],
                provenance="IPIP public domain, https://ipip.ori.org/",
                is_active=True,
            )
        )
        session.add(
            PilotPlanRecord(
                id=plan_id,
                key="pilot",
                name="Pilot",
                price_minor_units=4900,
                currency="usd",
                is_active=True,
            )
        )
        for index, (member_id, display_name, birth_year, gender) in enumerate(
            (
                (member_a_id, "Alex", 1990, Gender.MAN),
                (member_b_id, "Blair", 1992, Gender.WOMAN),
            )
        ):
            assignment_id = uuid.uuid4()
            counselor_assignment_id = uuid.uuid4()
            session.add(
                MemberProfileRecord(
                    user_id=member_id,
                    display_name=display_name,
                    birth_date=date(birth_year, 4, 3),
                    faith_affirmed=True,
                    relationship_intent="committed Christian marriage",
                    denomination_code="baptist",
                    city="Nashville",
                    state="Tennessee",
                    completed_at=now,
                )
            )
            session.add(
                MemberMatchPreferencesRecord(
                    user_id=member_id,
                    gender=gender.value,
                    min_partner_age=25,
                    max_partner_age=50,
                    completed_at=now,
                )
            )
            session.add(
                ConsentAcceptanceRecord(
                    user_id=member_id,
                    consent_version_id=consent_id,
                    accepted_at=now,
                )
            )
            session.add(
                CommunityCovenantAcceptanceRecord(
                    user_id=member_id,
                    covenant_definition_id=covenant_id,
                    accepted_affirmation_keys=[
                        "christian_identity",
                        "community_conduct",
                    ],
                    accepted_at=now,
                )
            )
            session.add(
                AssessmentAssignmentRecord(
                    id=assignment_id,
                    member_id=member_id,
                    definition_id=assessment_id,
                    answers={"q": 4},
                    assigned_at=now,
                    completed_at=now,
                    expires_at=now + timedelta(days=90),
                )
            )
            session.add(
                CounselorAssignmentRecord(
                    id=counselor_assignment_id,
                    center_id=center_id,
                    member_id=member_id,
                    counselor_id=counselor_id,
                    assigned_by_id=admin_id,
                    assigned_at=now,
                )
            )
            session.add(
                CounselorDecisionRecord(
                    assignment_id=counselor_assignment_id,
                    status="approved",
                    decided_by_id=counselor_id,
                    decided_at=now,
                )
            )
            session.add(
                ScreeningCaseRecord(
                    member_id=member_id,
                    provider="test",
                    provider_reference=f"screen-{index}",
                    status="eligible",
                    requested_at=now,
                    updated_at=now,
                )
            )
            session.add(
                SubscriptionRecord(
                    member_id=member_id,
                    center_id=center_id,
                    plan_id=plan_id,
                    status="active",
                    provider="test",
                    current_period_end=now + timedelta(days=30),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                MemberCommunityAssignmentRecord(
                    center_id=center_id,
                    member_id=member_id,
                    community_id=self_paced_id,
                    assigned_by_id=admin_id,
                    reason_code="pilot_placement",
                    assigned_at=now,
                )
            )
    service = PilotService(
        SqlAlchemyPilotRepository(sessions, ReadinessEvaluator()),
        frozenset({"admin@example.com"}),
    )
    return (
        service,
        sessions,
        AuthenticatedUser(
            admin_id, "admin@example.com", "Admin", Role.ADMIN, center_id
        ),
        AuthenticatedUser(member_a_id, "a@example.com", "Alex", Role.MEMBER, center_id),
        AuthenticatedUser(
            member_b_id, "b@example.com", "Blair", Role.MEMBER, center_id
        ),
        counselor_community_id,
    )


def test_inventory_retake_is_versioned_private_and_does_not_change_rank(
    priority3: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
        uuid.UUID,
    ],
) -> None:
    service, sessions, _admin, member_a, _member_b, _community_id = priority3
    before = service.self_paced_suggestions(member_a)
    inventory = service.personality_inventory(member_a)
    answers = {item.id: 5 for item in inventory.items}
    service.submit_personality_inventory(member_a, inventory.assignment_id, answers)
    service.submit_personality_inventory(
        member_a,
        inventory.assignment_id,
        {item.id: 1 for item in inventory.items},
    )
    after = service.self_paced_suggestions(member_a)
    assert service.personality_status(member_a).completed
    assert after[0].score == before[0].score
    assert after[0].member_id == before[0].member_id
    with sessions.session() as session:
        response = session.scalar(select(PersonalityInventoryResponseRecord))
        score = session.scalar(select(PersonalityInventoryScoreRecord))
        audits = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "personality_inventory.completed"
            )
        ).all()
        outbox = session.scalars(select(OutboxMessageRecord)).all()
        assert response is not None and response.answers
        assert score is not None and score.scores
        assert audits and all("scores" not in item.safe_metadata for item in audits)
        assert all("personality" not in str(item.payload).lower() for item in outbox)


def test_self_paced_projection_interest_and_reciprocal_activation(
    priority3: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
        uuid.UUID,
    ],
) -> None:
    service, sessions, _admin, member_a, member_b, _community_id = priority3
    suggestion = service.self_paced_suggestions(member_a)[0]
    assert suggestion.display_name == "Blair"
    assert suggestion.age_band.endswith("–34")
    assert "1992" not in repr(suggestion)
    assert (
        service.set_suggestion_interest(
            member_a, member_b.id, SuggestionInterestStatus.INTERESTED
        )
        is None
    )
    # One-sided interest does not reveal Alex through any exceptional path:
    # Alex was already a normally eligible suggestion for Blair.
    reciprocal_suggestion = service.self_paced_suggestions(member_b)[0]
    assert reciprocal_suggestion.member_id == member_a.id
    assert reciprocal_suggestion.incoming_interest
    proposal_id = service.set_suggestion_interest(
        member_b, member_a.id, SuggestionInterestStatus.INTERESTED
    )
    assert proposal_id is not None
    assert (
        service.set_suggestion_interest(
            member_b, member_a.id, SuggestionInterestStatus.INTERESTED
        )
        == proposal_id
    )
    with sessions.session() as session:
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        assert proposal.status == ProposalStatus.ACTIVE.value
        assert proposal.member_a_response == "accepted"
        assert proposal.member_b_response == "accepted"
        assert session.scalar(
            select(MatchProposalParticipantClaimRecord).where(
                MatchProposalParticipantClaimRecord.proposal_id == proposal_id
            )
        )
        assert (
            len(session.scalars(select(SelfPacedSuggestionInterestRecord)).all()) == 2
        )


def test_community_reassignment_rejects_active_and_cleans_pending(
    priority3: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
        uuid.UUID,
    ],
) -> None:
    service, sessions, admin, member_a, member_b, counselor_community_id = priority3
    service.set_suggestion_interest(
        member_a, member_b.id, SuggestionInterestStatus.INTERESTED
    )
    proposal_id = service.set_suggestion_interest(
        member_b, member_a.id, SuggestionInterestStatus.INTERESTED
    )
    assert proposal_id is not None
    with pytest.raises(ConflictError, match="introduced or active"):
        service.assign_community(
            admin,
            member_a.id,
            counselor_community_id,
            CommunityAssignmentReasonCode.OPERATIONS_CORRECTION,
        )
    with pytest.raises(ValidationError):
        service.assign_community(
            admin,
            member_a.id,
            counselor_community_id,
            cast(CommunityAssignmentReasonCode, "private text"),
        )


def test_community_reassignment_closes_pending_and_withdraws_interest(
    priority3: tuple[
        PilotService,
        DatabaseSessionFactory,
        AuthenticatedUser,
        AuthenticatedUser,
        AuthenticatedUser,
        uuid.UUID,
    ],
) -> None:
    service, sessions, admin, member_a, member_b, counselor_community_id = priority3
    now = datetime.now(UTC)
    pair = sorted((member_a.id, member_b.id), key=str)
    with sessions.session() as session, session.begin():
        assignment = session.scalar(
            select(MemberCommunityAssignmentRecord).where(
                MemberCommunityAssignmentRecord.member_id == member_a.id,
                MemberCommunityAssignmentRecord.ended_at.is_(None),
            )
        )
        assert assignment is not None
        proposal = MatchProposalRecord(
            center_id=member_a.center_id,
            community_id=assignment.community_id,
            member_a_id=pair[0],
            member_b_id=pair[1],
            status=ProposalStatus.PENDING_REVIEW.value,
            score=50,
            score_breakdown=[],
            counselor_a_decision="pending",
            counselor_b_decision="pending",
            created_at=now,
        )
        session.add(proposal)
        session.flush()
        session.add_all(
            (
                MatchProposalParticipantClaimRecord(
                    proposal_id=proposal.id, member_id=pair[0]
                ),
                MatchProposalParticipantClaimRecord(
                    proposal_id=proposal.id, member_id=pair[1]
                ),
                SelfPacedSuggestionInterestRecord(
                    center_id=member_a.center_id,
                    community_id=assignment.community_id,
                    member_id=member_a.id,
                    candidate_member_id=member_b.id,
                    status=SuggestionInterestStatus.INTERESTED.value,
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
        proposal_id = proposal.id
    service.assign_community(
        admin,
        member_a.id,
        counselor_community_id,
        CommunityAssignmentReasonCode.OPERATIONS_CORRECTION,
    )
    with sessions.session() as session:
        closed_proposal = session.get(MatchProposalRecord, proposal_id)
        assert closed_proposal is not None
        assert closed_proposal.status == ProposalStatus.CLOSED.value
        assert closed_proposal.closed_reason == "community_reassigned"
        assert (
            session.scalar(
                select(MatchProposalParticipantClaimRecord).where(
                    MatchProposalParticipantClaimRecord.proposal_id == proposal_id
                )
            )
            is None
        )
        interest = session.scalar(select(SelfPacedSuggestionInterestRecord))
        assert interest is not None
        assert interest.status == SuggestionInterestStatus.WITHDRAWN.value
