import logging

import streamlit as st
from alembic.util.exc import CommandError
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
from streamlit.errors import StreamlitSecretNotFoundError

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, OidcIdentity, Role
from matchwell.domain.errors import MatchwellError
from matchwell.domain.matching import MatchScorer
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
from matchwell.infrastructure.settings import Settings, get_runtime_settings
from matchwell.presentation.branding import (
    APP_ICON_PATH,
    LOGO_PATH,
    render_app_branding,
)
from matchwell.presentation.member import (
    render_assessment,
    render_consent,
    render_dashboard,
    render_introduction,
    render_matching,
    render_profile,
)
from matchwell.presentation.operations import render_admin, render_counselor
from matchwell.presentation.theme import inject_theme

st.set_page_config(
    page_title="Matchwell",
    page_icon=APP_ICON_PATH,
    layout="wide",
)
inject_theme()
render_app_branding()

logger = logging.getLogger(__name__)


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
        MatchScorer(),
    )
    return PilotService(repository, _settings.normalized_admin_emails())


def render_landing() -> None:
    _, logo_column, _ = st.columns([1, 2, 1])
    with logo_column:
        st.image(LOGO_PATH, width="stretch")
    st.title("Matchwell")
    st.subheader("Find love built on readiness")
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
                title="Community",
                icon=":material/checklist:",
                url_path="readiness",
                default=True,
            ),
            st.Page(
                lambda: render_profile(service, actor),
                title="Profile",
                icon=":material/person:",
                url_path="profile",
            ),
            st.Page(
                lambda: render_consent(service, actor),
                title="Consent",
                icon=":material/contract:",
                url_path="consent",
            ),
            st.Page(
                lambda: render_assessment(service, actor),
                title="Assessment",
                icon=":material/assignment:",
                url_path="assessment",
            ),
        ],
        "Matching": [
            st.Page(
                lambda: render_matching(service, actor),
                title="Matching",
                icon=":material/favorite:",
                url_path="matching",
            ),
            st.Page(
                lambda: render_introduction(service, actor),
                title="Introduction",
                icon=":material/handshake:",
                url_path="introduction",
            ),
        ],
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
            url_path="operations",
            default=True,
        )
    else:
        page = st.Page(
            lambda: render_counselor(service, actor),
            title="Counselor workspace",
            icon=":material/clinical_notes:",
            url_path="counselor",
            default=True,
        )
    return {"Operations": [page]}


try:
    settings = get_runtime_settings(st.secrets)
except StreamlitSecretNotFoundError:
    settings = get_runtime_settings({})
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
except CommandError:
    logger.exception("Database migration configuration failed.")
    st.title("Matchwell")
    st.error("Database migration configuration failed.")
    st.caption("Confirm MATCHWELL_AUTO_MIGRATE = true, save secrets, and reboot.")
    st.stop()
except OperationalError:
    logger.exception("Database connection failed during application startup.")
    st.title("Matchwell")
    st.error("The database became unavailable during application startup.")
    st.caption("Verify the hosted DATABASE_URL and that the database is active.")
    st.stop()
except (RuntimeError, SQLAlchemyError):
    logger.exception("Database migration failed during application startup.")
    st.title("Matchwell")
    st.error("The database schema could not be prepared.")
    st.caption("Review the Streamlit logs for the migration error type.")
    st.stop()

try:
    actor = service.sign_in(current_identity())
except MatchwellError as error:
    st.title("Matchwell")
    st.error(str(error))
    st.stop()
except ProgrammingError:
    logger.exception("Required database schema is unavailable.")
    st.title("Matchwell")
    st.error("The readiness database tables are missing.")
    st.caption("Set MATCHWELL_AUTO_MIGRATE = true, save secrets, and reboot.")
    st.stop()
except OperationalError:
    logger.exception("Database connection failed during account bootstrap.")
    st.title("Matchwell")
    st.error("The database became unavailable while signing in.")
    st.stop()
except SQLAlchemyError:
    logger.exception("Account bootstrap failed.")
    st.title("Matchwell")
    st.error("The administrator account could not be prepared.")
    st.caption("Review the Streamlit logs for the database error type.")
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
