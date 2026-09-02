import streamlit as st

from matchwell.application.health import SystemHealthService
from matchwell.domain.system_health import HealthStatus
from matchwell.infrastructure.persistence.database import SqlAlchemyDatabaseProbe
from matchwell.infrastructure.settings import get_settings

st.set_page_config(
    page_title="Matchwell",
    page_icon=":handshake:",
    layout="centered",
)

settings = get_settings()
health_service = SystemHealthService(
    database_probe=SqlAlchemyDatabaseProbe(settings.reveal_database_url())
)
health = health_service.check()

st.title("Matchwell")
st.subheader("Readiness before relationship")
st.write(
    "A counselor-guided Christian community for intentional, safe, and "
    "relationship-ready connections."
)

st.info(
    "This foundation establishes the pilot runtime and persistence boundary. "
    "Member onboarding will be implemented as the first product slice."
)

st.header("System readiness")
for component in health.components:
    if component.status is HealthStatus.READY:
        st.success(f"{component.name}: {component.detail}")
    else:
        st.error(f"{component.name}: {component.detail}")

with st.sidebar:
    st.caption(f"Environment: {settings.environment}")
    st.caption(f"Overall status: {health.status.value}")
