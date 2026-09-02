import streamlit as st
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.errors import MatchwellError
from matchwell.domain.readiness import ReadinessEvaluator
from matchwell.infrastructure.persistence.database import (
    DatabaseSessionFactory,
    SqlAlchemyDatabaseProbe,
    create_database_engine,
    upgrade_database,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    SqlAlchemyPilotRepository,
)
from matchwell.infrastructure.settings import Settings, get_settings
from matchwell.presentation.member import (
    render_assessment,
    render_consent,
    render_dashboard,
    render_profile,
)
from matchwell.presentation.operations import render_admin, render_counselor

st.set_page_config(
    page_title="Matchwell",
    page_icon=":handshake:",
    layout="wide",
)


@st.cache_resource
def build_service(_settings: Settings) -> PilotService:
    database_url = _settings.reveal_database_url()
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    if _settings.auto_migrate:
        upgrade_database(database_url)
    engine = create_database_engine(database_url)
    repository = SqlAlchemyPilotRepository(
        DatabaseSessionFactory(engine),
        ReadinessEvaluator(),
    )
    return PilotService(repository, _settings.normalized_admin_emails())


def render_landing() -> None:
    st.title("Matchwell")
    st.subheader("Readiness before relationship")
    st.write(
        "A counselor-guided Christian community for intentional, safe, and "
        "relationship-ready connections."
    )
    st.button("Continue with Google", type="primary", on_click=st.login)


def current_identity() -> OidcIdentity:
    return OidcIdentity(
        issuer=str(getattr(st.user, "iss", "https://accounts.google.com")),
        subject=str(getattr(st.user, "sub", "")),
        email=str(getattr(st.user, "email", "")),
        email_verified=bool(getattr(st.user, "email_verified", False)),
        name=str(getattr(st.user, "name", "")),
    )


def member_pages(
    service: PilotService, actor: AuthenticatedUser
) -> dict[str, list[st.Page]]:
    return {
        "My journey": [
            st.Page(
                lambda: render_dashboard(service, actor),
                title="Readiness",
                icon=":material/checklist:",
                default=True,
            ),
            st.Page(
                lambda: render_profile(service, actor),
                title="Profile",
                icon=":material/person:",
            ),
            st.Page(
                lambda: render_consent(service, actor),
                title="Consent",
                icon=":material/contract:",
            ),
            st.Page(
                lambda: render_assessment(service, actor),
                title="Assessment",
                icon=":material/assignment:",
            ),
        ]
    }


def operations_pages(
    service: PilotService,
    actor: AuthenticatedUser,
) -> dict[str, list[st.Page]]:
    if actor.role is Role.ADMIN:
        page = st.Page(
            lambda: render_admin(service, actor),
            title="Pilot operations",
            icon=":material/admin_panel_settings:",
            default=True,
        )
    else:
        page = st.Page(
            lambda: render_counselor(service, actor),
            title="Counselor workspace",
            icon=":material/clinical_notes:",
            default=True,
        )
    return {"Operations": [page]}


settings = get_settings()
database_url = settings.reveal_database_url()
health = SqlAlchemyDatabaseProbe(database_url).check()

if database_url is None:
    st.title("Matchwell setup")
    st.error("DATABASE_URL is not configured.")
    st.stop()

if not bool(getattr(st.user, "is_logged_in", False)):
    render_landing()
    st.stop()

try:
    service = build_service(settings)
    actor = service.sign_in(current_identity())
except MatchwellError as error:
    st.title("Matchwell")
    st.error(str(error))
    st.stop()
except (CommandError, RuntimeError, SQLAlchemyError):
    st.title("Matchwell")
    st.error("Application setup is incomplete. Contact the pilot administrator.")
    st.caption(
        "If this is the first deployment, enable MATCHWELL_AUTO_MIGRATE and "
        "configure MATCHWELL_ADMIN_EMAILS in Streamlit secrets."
    )
    st.stop()

if actor is None:
    st.title("Invitation required")
    st.warning(
        "This Google account does not have an active Matchwell invitation. "
        "Ask a pilot administrator to invite your exact email address."
    )
    st.button("Sign out", on_click=st.logout)
    st.stop()

with st.sidebar:
    st.write(f"**{actor.name}**")
    st.caption(f"{actor.role.value.title()} · {actor.email}")
    st.button("Sign out", on_click=st.logout, use_container_width=True)
    st.divider()
    st.caption(f"Database: {health.status.value}")

navigation = (
    member_pages(service, actor)
    if actor.role is Role.MEMBER
    else operations_pages(service, actor)
)
st.navigation(navigation).run()
