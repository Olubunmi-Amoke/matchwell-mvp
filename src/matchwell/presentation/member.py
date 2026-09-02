"""Member readiness journey pages."""

from datetime import date, timedelta

import streamlit as st

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser
from matchwell.domain.errors import MatchwellError
from matchwell.domain.pilot import ProfileInput


def render_dashboard(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title(f"Welcome, {actor.name}")
    st.caption("Your readiness journey")
    try:
        progress = service.progress(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    completed = 7 - len(progress.readiness.unmet_requirements)
    st.caption(
        f"Current stage: {progress.readiness.stage.value.replace('_', ' ').title()}"
    )
    st.progress(completed / 7, text=f"{completed} of 7 readiness requirements complete")

    if progress.readiness.eligible:
        st.success(f"You are eligible for {progress.community_name}.")
    else:
        st.subheader("Your next steps")
        for explanation in progress.readiness.explanations:
            st.write(f"- {explanation}")

    left, right = st.columns(2)
    left.metric(
        "Counselor intake", progress.counselor_status.value.replace("_", " ").title()
    )
    right.metric("Screening", progress.screening_status.value.replace("_", " ").title())


def render_profile(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Profile and adult eligibility")
    st.write("Complete the fields your counselor needs for the readiness journey.")
    try:
        current = service.profile(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    today = date.today()
    default_birth_date = (
        current.birth_date if current else today - timedelta(days=25 * 365)
    )
    with st.form("member-profile"):
        display_name = st.text_input(
            "Display name",
            value=current.display_name if current else actor.name,
        )
        birth_date = st.date_input(
            "Date of birth",
            value=default_birth_date,
            max_value=today,
        )
        faith_affirmed = st.checkbox(
            "I identify as Christian and want faith to guide this journey.",
            value=current.faith_affirmed if current else False,
        )
        relationship_intent = st.text_area(
            "What are you seeking in a committed relationship?",
            value=current.relationship_intent if current else "",
            max_chars=300,
        )
        denomination = st.text_input(
            "Denomination or church tradition (optional)",
            value=current.denomination if current else "",
        )
        city = st.text_input("City", value=current.city if current else "")
        state = st.text_input("State", value=current.state if current else "")
        submitted = st.form_submit_button("Save profile", type="primary")

    if submitted:
        try:
            service.save_profile(
                actor,
                ProfileInput(
                    display_name=display_name,
                    birth_date=birth_date,
                    faith_affirmed=faith_affirmed,
                    relationship_intent=relationship_intent,
                    denomination=denomination,
                    city=city,
                    state=state,
                ),
            )
        except MatchwellError as error:
            st.error(str(error))
        else:
            st.success("Your profile is complete.")


def render_consent(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Participation consent")
    try:
        consent = service.consent(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    st.subheader(consent.title)
    st.caption(f"Version {consent.version}")
    st.markdown(consent.body_markdown)
    if consent.accepted:
        st.success("You accepted this version.")
        return

    with st.form("consent"):
        confirmed = st.checkbox("I have read and agree to this consent.")
        submitted = st.form_submit_button("Accept consent", type="primary")
    if submitted:
        if not confirmed:
            st.error("Confirm that you agree before continuing.")
            return
        try:
            service.accept_consent(actor, consent.id)
        except MatchwellError as error:
            st.error(str(error))
        else:
            st.success("Consent recorded.")
            st.rerun()


def render_assessment(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Readiness assessment")
    try:
        assessment = service.assessment(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    st.subheader(assessment.title)
    st.write(assessment.description)
    if assessment.completed:
        st.success("Assessment complete. You may update your reflection.")

    answers: dict[str, int] = {}
    with st.form("readiness-assessment"):
        for question in assessment.questions:
            answers[question.id] = st.slider(
                question.prompt,
                min_value=1,
                max_value=5,
                value=3,
                key=f"assessment-{question.id}",
            )
        submitted = st.form_submit_button("Submit assessment", type="primary")

    if submitted:
        try:
            service.submit_assessment(actor, assessment.assignment_id, answers)
        except MatchwellError as error:
            st.error(str(error))
        else:
            st.success("Your assessment was submitted securely.")
