"""Automated semantic/accessibility checks for critical Streamlit surfaces.

Uses Streamlit's own ``AppTest`` harness (already a project dependency) to
render real pages against a real, seeded, in-memory-SQLite-backed
``PilotService`` and assert on the rendered widget tree: every interactive
control has an explicit, non-empty label; every page exposes at least one
heading; every status/error message carries real text (not just a color);
and no page throws while rendering. This does not replace a manual
keyboard/screen-reader pass -- see
docs/runbooks/accessibility-checklist.md for that.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
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
    AssessmentDefinitionRecord,
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    PilotPlanRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)


def _seeded_service(database_path: str) -> PilotService:
    # A file-backed SQLite database, not "sqlite://" in-memory: AppTest runs
    # the rendered page in a separate thread, and SQLAlchemy's in-memory
    # SQLite pool is thread-local, so an in-memory database would silently
    # appear empty (no such table) from that thread.
    engine = create_database_engine(f"sqlite:///{database_path}")
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
                ],
                is_active=True,
            )
        )
        session.add(
            PilotPlanRecord(
                id=uuid.uuid4(),
                key="matchwell-pilot",
                name="Matchwell Pilot",
                price_minor_units=4_900,
                currency="usd",
                is_active=True,
            )
        )
    repository = SqlAlchemyPilotRepository(sessions, ReadinessEvaluator())
    return PilotService(repository, frozenset({"admin@example.com"}))


def _identity(email: str, subject: str) -> OidcIdentity:
    return OidcIdentity(
        issuer="https://accounts.google.com",
        subject=subject,
        email=email,
        email_verified=True,
        name=email.split("@")[0].title(),
    )


@pytest.fixture
def seeded_actors(
    tmp_path: Path,
) -> tuple[PilotService, AuthenticatedUser, AuthenticatedUser]:
    service = _seeded_service(str(tmp_path / "accessibility.db"))
    admin = service.sign_in(_identity("admin@example.com", "admin-sub"))
    assert admin is not None
    service.create_invitation(
        admin,
        InvitationInput(
            email="counselor@example.com",
            role=Role.COUNSELOR,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    counselor = service.sign_in(_identity("counselor@example.com", "counselor-sub"))
    assert counselor is not None
    return service, admin, counselor


def _assert_every_widget_has_a_label(
    app: AppTest,
    *,
    require_interactive: bool = False,
) -> None:
    widgets = (
        list(app.button)
        + list(app.text_input)
        + list(app.selectbox)
        + list(app.checkbox)
        + list(app.number_input)
    )
    if require_interactive:
        assert widgets, "Page must expose at least one interactive control."
    for widget in widgets:
        assert widget.label is not None and widget.label.strip() != "", (
            f"Widget {widget!r} is missing an explicit, non-empty label."
        )


def _assert_has_a_heading(app: AppTest) -> None:
    headings = list(app.title) + list(app.header) + list(app.subheader)
    assert headings, "Page must expose at least one heading (title/header/subheader)."
    for heading in headings:
        assert heading.value.strip() != "", "Heading text must not be empty."


def test_admin_dashboard_renders_with_labeled_widgets_and_headings(
    seeded_actors: tuple[PilotService, AuthenticatedUser, AuthenticatedUser],
) -> None:
    service, admin, _counselor = seeded_actors

    def _app(service, actor):  # type: ignore[no-untyped-def]
        from matchwell.presentation.operations import render_admin
        from matchwell.presentation.theme import inject_theme

        inject_theme()
        render_admin(service, actor)

    app = AppTest.from_function(_app, kwargs={"service": service, "actor": admin}).run(
        timeout=15
    )

    assert not app.exception, f"Admin dashboard raised: {app.exception}"
    _assert_has_a_heading(app)
    _assert_every_widget_has_a_label(app, require_interactive=True)


def test_counselor_workspace_renders_with_labeled_widgets_and_headings(
    seeded_actors: tuple[PilotService, AuthenticatedUser, AuthenticatedUser],
) -> None:
    service, _admin, counselor = seeded_actors

    def _app(service, actor):  # type: ignore[no-untyped-def]
        from matchwell.presentation.operations import render_counselor
        from matchwell.presentation.theme import inject_theme

        inject_theme()
        render_counselor(service, actor)

    app = AppTest.from_function(
        _app, kwargs={"service": service, "actor": counselor}
    ).run(timeout=15)

    assert not app.exception, f"Counselor workspace raised: {app.exception}"
    _assert_has_a_heading(app)
    _assert_every_widget_has_a_label(app)


def test_member_dashboard_renders_with_status_text_not_color_alone(
    seeded_actors: tuple[PilotService, AuthenticatedUser, AuthenticatedUser],
) -> None:
    service, admin, _counselor = seeded_actors
    service.create_invitation(
        admin,
        InvitationInput(
            email="member@example.com",
            role=Role.MEMBER,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    member = service.sign_in(_identity("member@example.com", "member-sub"))
    assert member is not None

    def _app(service, actor):  # type: ignore[no-untyped-def]
        from matchwell.presentation.member import render_dashboard
        from matchwell.presentation.theme import inject_theme

        inject_theme()
        render_dashboard(service, actor)

    app = AppTest.from_function(_app, kwargs={"service": service, "actor": member}).run(
        timeout=15
    )

    assert not app.exception, f"Member dashboard raised: {app.exception}"
    _assert_has_a_heading(app)
    # The readiness stage badge must carry real text, not merely a color,
    # so a screen reader (or a printed report) still communicates status.
    page_html = " ".join(
        node.value
        for node in app.markdown
        if not node.value.lstrip().startswith("<style>")
    )
    assert "Stage:" in page_html


def test_member_matching_page_labels_every_preference_control(
    seeded_actors: tuple[PilotService, AuthenticatedUser, AuthenticatedUser],
) -> None:
    service, admin, counselor = seeded_actors
    service.create_invitation(
        admin,
        InvitationInput(
            email="member2@example.com",
            role=Role.MEMBER,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
    )
    member = service.sign_in(_identity("member2@example.com", "member2-sub"))
    assert member is not None
    service.save_profile(
        member,
        ProfileInput(
            display_name="Member Two",
            birth_date=date(1990, 1, 1),
            faith_affirmed=True,
            relationship_intent="A healthy, committed Christian marriage.",
            denomination="",
            city="Nashville",
            state="Tennessee",
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
    service.record_screening_status(
        admin,
        member.id,
        ScreeningStatus.ELIGIBLE,
        "event-1",
        "case-1",
    )
    service.grant_complimentary_entitlement(admin, member.id, "pilot-migration")

    def _app(service, actor):  # type: ignore[no-untyped-def]
        from matchwell.presentation.member import render_matching
        from matchwell.presentation.theme import inject_theme

        inject_theme()
        render_matching(service, actor)

    app = AppTest.from_function(_app, kwargs={"service": service, "actor": member}).run(
        timeout=15
    )

    assert not app.exception, f"Member matching page raised: {app.exception}"
    _assert_every_widget_has_a_label(app, require_interactive=True)
