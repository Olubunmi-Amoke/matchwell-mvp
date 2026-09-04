import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from matchwell.domain.matching import (
    CounselorReviewDecision,
    Gender,
    MatchPreferencesInput,
    MemberResponseDecision,
    ProposalStatus,
    SafetyCategory,
)
from matchwell.domain.pilot import (
    CounselorDecisionStatus,
    InvitationInput,
    ProfileInput,
    ScreeningStatus,
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
    CommunityRecord,
    ConsentVersionRecord,
    CounselorAssignmentRecord,
    HoldRecord,
    MatchProposalRecord,
    MemberBlockRecord,
    MemberMatchPreferencesRecord,
    MemberProfileRecord,
    OutboxMessageRecord,
    ScreeningCaseRecord,
    UserRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)

Pilot = tuple[PilotService, DatabaseSessionFactory]


@pytest.fixture
def pilot() -> Pilot:
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = DatabaseSessionFactory(engine)
    with sessions.session() as session, session.begin():
        center_id = uuid.uuid4()
        session.add(
            CenterRecord(id=center_id, slug="matchwell-pilot", name="Matchwell Center")
        )
        session.add(
            CommunityRecord(
                id=uuid.uuid4(),
                center_id=center_id,
                slug="intentional-relationships",
                name="Intentional Relationships Community",
            )
        )
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="1.0",
                title="Pilot consent",
                body_markdown="Safe pilot consent.",
                effective_at=datetime.now(UTC),
                is_active=True,
            )
        )
        session.add(
            AssessmentDefinitionRecord(
                id=uuid.uuid4(),
                key="relationship-readiness",
                version="1.0",
                title="Readiness reflection",
                description="Reflect honestly.",
                questions=[
                    {"id": "communication", "prompt": "I communicate clearly."},
                    {"id": "faith", "prompt": "Faith guides my relationships."},
                ],
                is_active=True,
            )
        )
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    return PilotService(repository, frozenset({"admin@example.com"})), sessions


def identity(email: str, subject: str) -> OidcIdentity:
    return OidcIdentity(
        issuer="https://accounts.google.com",
        subject=subject,
        email=email,
        email_verified=True,
        name=email.split("@")[0].title(),
    )


def _invite_and_sign_in(
    service: PilotService,
    admin: AuthenticatedUser,
    role: Role,
    email: str,
    subject: str,
) -> AuthenticatedUser:
    service.create_invitation(
        admin,
        InvitationInput(
            email=email,
            role=role,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    actor = service.sign_in(identity(email, subject))
    assert actor is not None
    return actor


def _make_ready_member(
    service: PilotService,
    admin: AuthenticatedUser,
    counselor: AuthenticatedUser,
    *,
    email: str,
    subject: str,
    display_name: str,
    birth_year: int,
    city: str,
    state: str,
    denomination: str,
    intent: str,
    gender: Gender,
    min_partner_age: int,
    max_partner_age: int,
) -> AuthenticatedUser:
    member = _invite_and_sign_in(service, admin, Role.MEMBER, email, subject)
    service.save_profile(
        member,
        ProfileInput(
            display_name=display_name,
            birth_date=date(birth_year, 1, 1),
            faith_affirmed=True,
            relationship_intent=intent,
            denomination=denomination,
            city=city,
            state=state,
        ),
    )
    consent = service.consent(member)
    service.accept_consent(member, consent.id)
    assessment = service.assessment(member)
    service.submit_assessment(
        member,
        assessment.assignment_id,
        {question.id: 4 for question in assessment.questions},
    )
    service.assign_counselor(admin, member.id, counselor.id)
    service.record_counselor_decision(
        counselor, member.id, CounselorDecisionStatus.APPROVED
    )
    created = service.record_screening_status(
        admin,
        member.id,
        ScreeningStatus.ELIGIBLE,
        f"event-{subject}",
        f"case-{subject}",
    )
    assert created
    service.save_match_preferences(
        member,
        MatchPreferencesInput(
            gender=gender,
            min_partner_age=min_partner_age,
            max_partner_age=max_partner_age,
        ),
    )
    assert service.progress(member).readiness.eligible
    return member


def _bootstrap_admin_and_counselors(
    service: PilotService,
) -> tuple[AuthenticatedUser, AuthenticatedUser, AuthenticatedUser]:
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor_a = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-a@example.com", "counselor-a-sub"
    )
    counselor_b = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "counselor-b@example.com", "counselor-b-sub"
    )
    return admin, counselor_a, counselor_b


def _make_reciprocal_pair(
    service: PilotService,
    admin: AuthenticatedUser,
    counselor_a: AuthenticatedUser,
    counselor_b: AuthenticatedUser,
) -> tuple[AuthenticatedUser, AuthenticatedUser]:
    member_a = _make_ready_member(
        service,
        admin,
        counselor_a,
        email="alex@example.com",
        subject="alex-sub",
        display_name="Alex",
        birth_year=1990,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Seeking a committed Christian marriage",
        gender=Gender.MAN,
        min_partner_age=25,
        max_partner_age=40,
    )
    member_b = _make_ready_member(
        service,
        admin,
        counselor_b,
        email="brooke@example.com",
        subject="brooke-sub",
        display_name="Brooke",
        birth_year=1992,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Committed Christian marriage is my goal",
        gender=Gender.WOMAN,
        min_partner_age=28,
        max_partner_age=45,
    )
    return member_a, member_b


def test_full_two_member_matching_journey_activates_workspace(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)

    created = service.generate_candidates(admin)
    assert created == 1

    queue_a = service.candidate_queue(counselor_a)
    assert len(queue_a) == 1
    proposal_id = queue_a[0].proposal_id
    assert queue_a[0].explanations

    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    assert service.introduction(member_a) is None
    assert service.introduction(member_b) is None

    queue_b = service.candidate_queue(counselor_b)
    assert len(queue_b) == 1
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)

    introduction_a = service.introduction(member_a)
    introduction_b = service.introduction(member_b)
    assert introduction_a is not None
    assert introduction_b is not None
    assert introduction_a.partner_display_name == "Brooke"
    assert introduction_b.partner_display_name == "Alex"
    assert introduction_a.my_response is None

    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )
    mid_response = service.introduction(member_a)
    assert mid_response is not None
    assert mid_response.my_response is MemberResponseDecision.ACCEPTED
    assert not hasattr(mid_response, "partner_response")
    assert service.matched_pair(member_a) is None

    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.ACCEPTED
    )
    matched_a = service.matched_pair(member_a)
    matched_b = service.matched_pair(member_b)
    assert matched_a is not None
    assert matched_b is not None
    assert matched_a.partner_display_name == "Brooke"
    assert matched_b.partner_display_name == "Alex"

    with sessions.session() as session:
        outbox_types = {
            row[0] for row in session.execute(select(OutboxMessageRecord.event_type))
        }
        assert "matching.introduced" in outbox_types
        assert "matching.activated" in outbox_types
        activated_audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "matching.activated"
            )
        )
        assert activated_audit is not None


def test_candidate_diagnostics_explain_ready_pair_and_open_proposal(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)

    before = service.candidate_generation_diagnostics(admin)

    assert before.total_members == 2
    assert before.ready_members == 2
    assert before.evaluated_pairs == 1
    assert before.eligible_pairs == 1
    assert before.pairs[0].eligible
    assert before.pairs[0].reasons == ()
    with sessions.session() as session:
        assert session.scalar(select(func.count(MatchProposalRecord.id))) == 0
        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "matching.diagnostics_accessed"
            )
        )
        assert audit is not None
        assert audit.safe_metadata == {
            "total_members": 2,
            "ready_members": 2,
            "evaluated_pairs": 1,
            "eligible_pairs": 1,
        }

    assert service.generate_candidates(admin) == 1
    after = service.candidate_generation_diagnostics(admin)

    assert after.ready_members == 0
    assert after.evaluated_pairs == 0
    assert {item.member_id: item.reasons for item in after.members} == {
        member_a.id: (
            "An existing candidate, introduction, or matched pair is still open.",
        ),
        member_b.id: (
            "An existing candidate, introduction, or matched pair is still open.",
        ),
    }


def test_candidate_diagnostics_do_not_close_an_ineligible_members_proposal(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, _ = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    assert service.generate_candidates(admin) == 1
    with sessions.session() as session, session.begin():
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        proposal_id = proposal.id
        session.add(
            HoldRecord(
                member_id=member_a.id,
                center_context_id=admin.center_id,
                hold_type="administrative",
                reason_code="manual-review",
                applied_by_id=admin.id,
                applied_at=datetime.now(UTC),
            )
        )

    diagnostics = service.candidate_generation_diagnostics(admin)

    assert any(
        "hold" in reason.casefold()
        for item in diagnostics.members
        if item.member_id == member_a.id
        for reason in item.reasons
    )
    with sessions.session() as session:
        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        assert proposal.status == ProposalStatus.PENDING_REVIEW.value


def test_candidate_diagnostics_explain_missing_preferences_and_readiness(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.apply_hold(admin, member_a.id, "manual-review")
    with sessions.session() as session, session.begin():
        preferences = session.get(MemberMatchPreferencesRecord, member_b.id)
        assert preferences is not None
        session.delete(preferences)

    diagnostics = service.candidate_generation_diagnostics(admin)
    reasons = {item.member_id: item.reasons for item in diagnostics.members}

    assert diagnostics.ready_members == 0
    assert any("hold" in reason.casefold() for reason in reasons[member_a.id])
    assert reasons[member_b.id] == (
        "Complete matching preferences, including gender and partner age range.",
    )


@pytest.mark.parametrize(
    ("gender_b", "min_age_b", "max_age_b", "expected_reason"),
    [
        (
            Gender.MAN,
            28,
            45,
            "The pilot currently generates only Man/Woman pairs.",
        ),
        (
            Gender.WOMAN,
            45,
            55,
            "Their partner age preferences are not reciprocal.",
        ),
    ],
)
def test_candidate_diagnostics_explain_pair_incompatibility(
    pilot: Pilot,
    gender_b: Gender,
    min_age_b: int,
    max_age_b: int,
    expected_reason: str,
) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    member_b = next(
        item for item in service.members(admin) if item.email == "brooke@example.com"
    )
    service.save_match_preferences(
        AuthenticatedUser(
            id=member_b.id,
            email=member_b.email,
            name=member_b.display_name,
            role=Role.MEMBER,
            center_id=member_b.center_id,
        ),
        MatchPreferencesInput(
            gender=gender_b,
            min_partner_age=min_age_b,
            max_partner_age=max_age_b,
        ),
    )

    diagnostics = service.candidate_generation_diagnostics(admin)

    assert diagnostics.eligible_pairs == 0
    assert diagnostics.pairs[0].reasons == (expected_reason,)


def test_candidate_diagnostics_explain_prior_pair_and_safety_restriction(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    with sessions.session() as session, session.begin():
        member_a_id, member_b_id = sorted((member_a.id, member_b.id), key=str)
        session.add(
            MatchProposalRecord(
                center_id=admin.center_id,
                community_id=session.scalar(select(CommunityRecord.id)),
                member_a_id=member_a_id,
                member_b_id=member_b_id,
                status=ProposalStatus.CLOSED.value,
                score=0,
                score_breakdown=[],
                counselor_a_decision=CounselorReviewDecision.DECLINED.value,
                counselor_b_decision=CounselorReviewDecision.DECLINED.value,
            )
        )
        session.add(
            MemberBlockRecord(
                center_id=admin.center_id,
                blocker_id=member_a.id,
                blocked_id=member_b.id,
                category=SafetyCategory.OTHER.value,
                context=None,
            )
        )

    diagnostics = service.candidate_generation_diagnostics(admin)

    assert diagnostics.eligible_pairs == 0
    assert set(diagnostics.pairs[0].reasons) == {
        "A safety restriction prevents this pair from matching.",
        "This pair already has proposal history and cannot be generated again.",
    }


def test_admin_reassigns_member_to_counselor_and_closes_active_workflows(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)
    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )
    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.ACCEPTED
    )

    with pytest.raises(ValidationError):
        service.reassign_member_to_counselor(
            admin,
            member_a.id,
            "different-member@example.com",
            "counselor-onboarding",
        )
    assert member_a.id in {item.id for item in service.members(admin)}

    service.reassign_member_to_counselor(
        admin,
        member_a.id,
        member_a.email,
        "counselor-onboarding",
    )

    assert {item.id for item in service.members(admin)} == {member_b.id}
    assert member_a.id in {item.id for item in service.counselors(admin)}
    assert service.assigned_members(counselor_a) == ()
    assert service.matched_pair(member_a) is None
    assert service.matched_pair(member_b) is None

    with sessions.session() as session:
        reassigned = session.get(UserRecord, member_a.id)
        assert reassigned is not None
        assert reassigned.role == Role.COUNSELOR.value

        assignment = session.scalar(
            select(CounselorAssignmentRecord).where(
                CounselorAssignmentRecord.member_id == member_a.id
            )
        )
        assert assignment is not None
        assert assignment.ended_at is not None

        proposal = session.get(MatchProposalRecord, proposal_id)
        assert proposal is not None
        assert proposal.closed_reason == "member_role_changed"
        assert proposal.closed_at is not None

        assert session.get(MemberProfileRecord, member_a.id) is not None
        assessment = session.scalar(
            select(AssessmentAssignmentRecord).where(
                AssessmentAssignmentRecord.member_id == member_a.id
            )
        )
        assert assessment is not None

        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "identity.role_reassigned",
                AuditEventRecord.subject_id == str(member_a.id),
            )
        )
        assert audit is not None
        assert audit.safe_metadata == {
            "previous_role": Role.MEMBER.value,
            "new_role": Role.COUNSELOR.value,
            "reason_code": "counselor-onboarding",
        }

        outbox = session.scalar(
            select(OutboxMessageRecord).where(
                OutboxMessageRecord.event_type == "identity.role_reassigned"
            )
        )
        assert outbox is not None
        assert outbox.payload == {
            "user_id": str(member_a.id),
            "center_id": str(admin.center_id),
            "previous_role": Role.MEMBER.value,
            "new_role": Role.COUNSELOR.value,
            "reason_code": "counselor-onboarding",
        }

    with pytest.raises(ConflictError):
        service.reassign_member_to_counselor(
            admin,
            member_a.id,
            member_a.email,
            "counselor-onboarding",
        )
    with pytest.raises(NotFoundError):
        service.assign_counselor(admin, member_a.id, counselor_b.id)

    service.reassign_counselor_to_member(
        admin,
        member_a.id,
        member_a.email,
        "returning-to-member-journey",
    )

    assert member_a.id in {item.id for item in service.members(admin)}
    assert member_a.id not in {item.id for item in service.counselors(admin)}
    progress = service.progress(member_a)
    assert not progress.readiness.eligible
    assert progress.screening_status is ScreeningStatus.NOT_STARTED
    fresh_assessment = service.assessment(member_a)
    assert not fresh_assessment.completed

    with sessions.session() as session:
        assessments = session.scalars(
            select(AssessmentAssignmentRecord)
            .where(AssessmentAssignmentRecord.member_id == member_a.id)
            .order_by(AssessmentAssignmentRecord.assigned_at)
        ).all()
        assert len(assessments) == 2
        assert assessments[0].completed_at is not None
        assert assessments[1].completed_at is None

        screening = session.scalar(
            select(ScreeningCaseRecord).where(
                ScreeningCaseRecord.member_id == member_a.id
            )
        )
        assert screening is not None
        assert screening.expires_at is not None

        role_audits = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "identity.role_reassigned",
                AuditEventRecord.subject_id == str(member_a.id),
            )
        ).all()
        assert {
            (
                item.safe_metadata["previous_role"],
                item.safe_metadata["new_role"],
                item.safe_metadata["reason_code"],
            )
            for item in role_audits
        } == {
            ("member", "counselor", "counselor-onboarding"),
            ("counselor", "member", "returning-to-member-journey"),
        }

    with pytest.raises(ConflictError):
        service.reassign_counselor_to_member(
            admin,
            member_a.id,
            member_a.email,
            "returning-to-member-journey",
        )


def test_counselor_with_open_match_review_cannot_become_member(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    with sessions.session() as session, session.begin():
        assignments = session.scalars(
            select(CounselorAssignmentRecord).where(
                CounselorAssignmentRecord.counselor_id == counselor_a.id,
                CounselorAssignmentRecord.ended_at.is_(None),
            )
        ).all()
        for assignment in assignments:
            assignment.ended_at = datetime.now(UTC)

    with pytest.raises(ConflictError):
        service.reassign_counselor_to_member(
            admin,
            counselor_a.id,
            counselor_a.email,
            "returning-to-member-journey",
        )


def test_ineligible_member_excluded_from_candidate_generation(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)

    # Apply a hold to member_b: they lose eligibility for matching entirely.
    service.apply_hold(admin, member_b.id, "manual-review")

    created = service.generate_candidates(admin)

    assert created == 0
    assert service.candidate_queue(counselor_a) == ()
    assert service.candidate_queue(counselor_b) == ()


def test_member_without_match_preferences_is_excluded(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a = _make_ready_member(
        service,
        admin,
        counselor_a,
        email="alex@example.com",
        subject="alex-sub",
        display_name="Alex",
        birth_year=1990,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Seeking a committed Christian marriage",
        gender=Gender.MAN,
        min_partner_age=25,
        max_partner_age=40,
    )
    member_b = _invite_and_sign_in(
        service, admin, Role.MEMBER, "brooke@example.com", "brooke-sub"
    )
    service.save_profile(
        member_b,
        ProfileInput(
            display_name="Brooke",
            birth_date=date(1992, 1, 1),
            faith_affirmed=True,
            relationship_intent="Committed Christian marriage",
            denomination="Baptist",
            city="Nashville",
            state="Tennessee",
        ),
    )
    consent = service.consent(member_b)
    service.accept_consent(member_b, consent.id)
    assessment = service.assessment(member_b)
    service.submit_assessment(
        member_b,
        assessment.assignment_id,
        {question.id: 4 for question in assessment.questions},
    )
    service.assign_counselor(admin, member_b.id, counselor_b.id)
    service.record_counselor_decision(
        counselor_b, member_b.id, CounselorDecisionStatus.APPROVED
    )
    service.record_screening_status(
        admin, member_b.id, ScreeningStatus.ELIGIBLE, "event-b", "case-b"
    )
    # member_b never completes match preferences.
    assert service.progress(member_b).readiness.eligible
    assert not service.match_preferences(member_b).completed

    created = service.generate_candidates(admin)

    assert created == 0
    assert member_a is not None


def test_counselor_cannot_review_candidate_for_unassigned_member(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id

    outsider = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "outsider@example.com", "outsider-sub"
    )

    with pytest.raises(NotFoundError):
        service.review_candidate(
            outsider, proposal_id, CounselorReviewDecision.APPROVED
        )


def test_mutual_response_privacy_hides_partner_decision(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)

    # member_a accepts first; their own response is recorded but the
    # introduction stays open awaiting member_b -- member_a cannot see
    # member_b's eventual answer.
    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )
    introduction_a = service.introduction(member_a)
    assert introduction_a is not None
    assert introduction_a.my_response is MemberResponseDecision.ACCEPTED
    assert not hasattr(introduction_a, "partner_response")

    # member_b declines -- the pair closes immediately. member_a is never
    # shown member_b's raw response value, only that the introduction is no
    # longer open.
    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.DECLINED
    )

    assert service.introduction(member_a) is None
    assert service.matched_pair(member_a) is None
    assert service.matched_pair(member_b) is None
    recent = service.recent_match(member_a)
    assert recent is not None
    assert recent.partner_id == member_b.id

    service.report_member(
        member_a,
        member_b.id,
        SafetyCategory.INAPPROPRIATE_CONTACT,
        "Contact continued after the introduction closed.",
    )


def test_duplicate_candidate_generation_is_idempotent(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, counselor_b)

    first_created = service.generate_candidates(admin)
    second_created = service.generate_candidates(admin)

    assert first_created == 1
    assert second_created == 0
    with sessions.session() as session:
        count = session.scalar(
            select(MatchProposalRecord.id).order_by(MatchProposalRecord.id)
        )
        assert count is not None
        all_proposals = session.scalars(select(MatchProposalRecord)).all()
        assert len(all_proposals) == 1


def test_generation_assigns_each_member_to_at_most_one_candidate(
    pilot: Pilot,
) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, _ = _make_reciprocal_pair(
        service,
        admin,
        counselor_a,
        counselor_b,
    )
    _make_ready_member(
        service,
        admin,
        counselor_b,
        email="casey@example.com",
        subject="casey-sub",
        display_name="Casey",
        birth_year=1993,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Seeking a committed Christian marriage",
        gender=Gender.WOMAN,
        min_partner_age=28,
        max_partner_age=45,
    )

    assert service.generate_candidates(admin) == 1

    with sessions.session() as session:
        proposals = session.scalars(select(MatchProposalRecord)).all()
        assert len(proposals) == 1
        proposal_members = {
            proposals[0].member_a_id,
            proposals[0].member_b_id,
        }
        assert member_a.id in proposal_members
        assert len(proposal_members) == 2


def test_double_response_to_introduction_is_rejected(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, _member_b = _make_reciprocal_pair(
        service, admin, counselor_a, counselor_b
    )
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)

    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )

    with pytest.raises(ConflictError):
        service.respond_to_introduction(
            member_a, proposal_id, MemberResponseDecision.ACCEPTED
        )


def test_hold_closes_active_matched_pair(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)
    service.respond_to_introduction(
        member_a, proposal_id, MemberResponseDecision.ACCEPTED
    )
    service.respond_to_introduction(
        member_b, proposal_id, MemberResponseDecision.ACCEPTED
    )
    assert service.matched_pair(member_a) is not None

    service.apply_hold(admin, member_a.id, "safety-review")

    assert service.matched_pair(member_a) is None
    assert service.matched_pair(member_b) is None
    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == "closed"
        assert proposal.closed_reason == "hold_applied"


def test_block_requires_existing_introduction(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)

    with pytest.raises(NotFoundError):
        service.block_member(member_a, member_b.id, SafetyCategory.HARASSMENT, None)


def test_pending_candidate_is_never_disclosed_or_available_for_safety_actions(
    pilot: Pilot,
) -> None:
    service, _ = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(
        service,
        admin,
        counselor_a,
        counselor_b,
    )
    assert service.generate_candidates(admin) == 1

    assert service.introduction(member_a) is None
    assert service.recent_match(member_a) is None
    with pytest.raises(NotFoundError):
        service.block_member(
            member_a,
            member_b.id,
            SafetyCategory.SAFETY_CONCERN,
            None,
        )
    with pytest.raises(NotFoundError):
        service.report_member(
            member_a,
            member_b.id,
            SafetyCategory.SAFETY_CONCERN,
            "A report must require a disclosed introduction.",
        )
    assert len(service.candidate_queue(counselor_a)) == 1


def test_block_closes_introduction_and_records_safe_audit(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)

    service.block_member(
        member_a, member_b.id, SafetyCategory.SAFETY_CONCERN, "Felt unsafe"
    )

    assert service.introduction(member_a) is None
    assert service.introduction(member_b) is None
    with sessions.session() as session:
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == "closed"
        assert proposal.closed_reason == "member_block"


def test_report_context_is_redacted_from_audit_payload(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id
    service.review_candidate(counselor_a, proposal_id, CounselorReviewDecision.APPROVED)
    service.review_candidate(counselor_b, proposal_id, CounselorReviewDecision.APPROVED)

    secret_context = "Extremely sensitive private detail: 555-0100 threat made."
    service.report_member(
        member_a, member_b.id, SafetyCategory.SAFETY_CONCERN, secret_context
    )

    with sessions.session() as session:
        report_audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "safety.report_created"
            )
        )
        assert report_audit is not None
        assert report_audit.safe_metadata == {
            "category": SafetyCategory.SAFETY_CONCERN.value,
            "context_length": len(secret_context),
        }
        serialized = str(report_audit.safe_metadata)
        assert secret_context not in serialized
        assert "555-0100" not in serialized


def test_center_isolation_for_candidate_queue(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor_a)[0].proposal_id

    with sessions.session() as session, session.begin():
        other_center_id = uuid.uuid4()
        other_community_id = uuid.uuid4()
        session.add(
            CenterRecord(id=other_center_id, slug="other-center", name="Other Center")
        )
        session.add(
            CommunityRecord(
                id=other_community_id,
                center_id=other_center_id,
                slug="other-community",
                name="Other Community",
            )
        )
        other_counselor = UserRecord(
            center_id=other_center_id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-counselor-sub",
            email="other-counselor@example.com",
            name="Other Counselor",
            role=Role.COUNSELOR.value,
        )
        other_member_a = UserRecord(
            center_id=other_center_id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-a-sub",
            email="other-a@example.com",
            name="Other A",
            role=Role.MEMBER.value,
        )
        other_member_b = UserRecord(
            center_id=other_center_id,
            oidc_issuer="https://accounts.google.com",
            oidc_subject="other-b-sub",
            email="other-b@example.com",
            name="Other B",
            role=Role.MEMBER.value,
        )
        session.add_all([other_counselor, other_member_a, other_member_b])
        session.flush()
        session.add(
            MatchProposalRecord(
                center_id=other_center_id,
                community_id=other_community_id,
                member_a_id=other_member_a.id,
                member_b_id=other_member_b.id,
                status="pending_review",
                score=50.0,
                score_breakdown=[],
                counselor_a_id=other_counselor.id,
                counselor_a_decision="pending",
                counselor_b_id=other_counselor.id,
                counselor_b_decision="pending",
            )
        )
        other_counselor_id = other_counselor.id

    other_counselor_actor = AuthenticatedUser(
        id=other_counselor_id,
        email="other-counselor@example.com",
        name="Other Counselor",
        role=Role.COUNSELOR,
        center_id=other_center_id,
    )

    # counselor_a from the pilot Center still only sees their own proposal.
    queue = service.candidate_queue(counselor_a)
    assert len(queue) == 1
    assert queue[0].proposal_id == proposal_id

    # The other Center's counselor cannot see the pilot Center's proposal;
    # their queue is scoped strictly to their own Center.
    other_queue = service.candidate_queue(other_counselor_actor)
    assert len(other_queue) == 1
    assert other_queue[0].proposal_id != proposal_id

    # And the pilot Center's counselor cannot review the other Center's
    # candidate by ID either.
    with pytest.raises(NotFoundError):
        service.review_candidate(
            counselor_a,
            other_queue[0].proposal_id,
            CounselorReviewDecision.APPROVED,
        )


def test_generate_candidates_requires_admin_role(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, _counselor_b = _bootstrap_admin_and_counselors(service)
    _make_reciprocal_pair(service, admin, counselor_a, _counselor_b)

    with pytest.raises(AuthorizationError):
        service.generate_candidates(counselor_a)


def test_same_counselor_for_both_members_resolves_in_one_review(
    pilot: Pilot,
) -> None:
    service, _ = pilot
    admin = service.sign_in(identity("admin@example.com", "admin-sub"))
    assert admin is not None
    counselor = _invite_and_sign_in(
        service, admin, Role.COUNSELOR, "shared-counselor@example.com", "shared-sub"
    )
    member_a = _make_ready_member(
        service,
        admin,
        counselor,
        email="alex@example.com",
        subject="alex-sub",
        display_name="Alex",
        birth_year=1990,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Seeking a committed Christian marriage",
        gender=Gender.MAN,
        min_partner_age=25,
        max_partner_age=40,
    )
    member_b = _make_ready_member(
        service,
        admin,
        counselor,
        email="brooke@example.com",
        subject="brooke-sub",
        display_name="Brooke",
        birth_year=1992,
        city="Nashville",
        state="Tennessee",
        denomination="Baptist",
        intent="Committed Christian marriage is my goal",
        gender=Gender.WOMAN,
        min_partner_age=28,
        max_partner_age=45,
    )
    service.generate_candidates(admin)
    proposal_id = service.candidate_queue(counselor)[0].proposal_id

    service.review_candidate(counselor, proposal_id, CounselorReviewDecision.APPROVED)

    assert service.introduction(member_a) is not None
    assert service.introduction(member_b) is not None
    assert service.candidate_queue(counselor) == ()


def test_review_candidate_rejects_pending_decision(pilot: Pilot) -> None:
    service, _ = pilot
    admin, counselor_a, _counselor_b = _bootstrap_admin_and_counselors(service)
    member_a = _invite_and_sign_in(
        service, admin, Role.MEMBER, "alex@example.com", "alex-sub"
    )
    with pytest.raises(ValidationError):
        service.review_candidate(
            counselor_a,
            uuid.uuid4(),
            CounselorReviewDecision.PENDING,
        )
    assert member_a is not None


def test_hold_record_precedence_over_pending_candidate(pilot: Pilot) -> None:
    service, sessions = pilot
    admin, counselor_a, counselor_b = _bootstrap_admin_and_counselors(service)
    member_a, member_b = _make_reciprocal_pair(service, admin, counselor_a, counselor_b)
    service.generate_candidates(admin)

    service.apply_hold(admin, member_a.id, "manual-review")

    with sessions.session() as session:
        hold = session.scalar(select(HoldRecord))
        assert hold is not None
        proposal = session.scalar(select(MatchProposalRecord))
        assert proposal is not None
        assert proposal.status == "closed"
        assert proposal.closed_reason == "hold_applied"
    assert service.candidate_queue(counselor_a) == ()
    assert member_b is not None
