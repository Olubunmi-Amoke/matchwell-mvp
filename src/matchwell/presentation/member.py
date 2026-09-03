"""Member readiness, matching, and introduction journey pages."""

import uuid
from datetime import date, timedelta

import streamlit as st

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser
from matchwell.domain.errors import MatchwellError
from matchwell.domain.matching import (
    MAX_PARTNER_AGE,
    MIN_PARTNER_AGE,
    Gender,
    MatchPreferencesInput,
    MemberResponseDecision,
    SafetyCategory,
)
from matchwell.domain.pilot import ProfileInput
from matchwell.presentation.theme import (
    humanize,
    render_badges,
    render_empty_state,
    render_eyebrow,
    render_success_state,
)


def render_dashboard(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title(f"Welcome, {actor.name}")
    st.caption("Your community readiness journey")
    try:
        progress = service.progress(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    completed = 7 - len(progress.readiness.unmet_requirements)
    with st.container(border=True):
        render_eyebrow(f"Stage: {humanize(progress.readiness.stage.value)}")
        st.progress(
            completed / 7,
            text=f"{completed} of 7 readiness requirements complete",
        )
        if progress.readiness.eligible:
            render_badges([(f"Eligible for {progress.community_name}", "success")])
        else:
            st.subheader("Your next steps")
            for explanation in progress.readiness.explanations:
                st.write(f"- {explanation}")

    left, right = st.columns(2)
    with left:
        st.metric("Counselor intake", humanize(progress.counselor_status.value))
    with right:
        st.metric("Screening", humanize(progress.screening_status.value))


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


def render_matching(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Matching")
    st.caption(
        "Complete your matching preferences to enter the pool once you are "
        "fully community eligible."
    )
    try:
        progress = service.progress(actor)
        preferences = service.match_preferences(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    with st.container(border=True):
        render_eyebrow("Matching preferences")
        st.write(
            "Used only for safe, explainable candidate matching. Never "
            "combined with assessment answers, screening details, or "
            "counselor notes."
        )
        with st.form("match-preferences"):
            gender = st.selectbox(
                "I identify as",
                options=[Gender.MAN, Gender.WOMAN],
                index=(1 if preferences.gender is Gender.WOMAN else 0),
                format_func=lambda item: humanize(item.value),
            )
            age_range = st.slider(
                "Acceptable partner age range",
                min_value=MIN_PARTNER_AGE,
                max_value=MAX_PARTNER_AGE,
                value=(
                    preferences.min_partner_age or MIN_PARTNER_AGE,
                    preferences.max_partner_age or (MIN_PARTNER_AGE + 10),
                ),
            )
            submitted = st.form_submit_button("Save preferences", type="primary")
        if submitted:
            try:
                service.save_match_preferences(
                    actor,
                    MatchPreferencesInput(
                        gender=gender,
                        min_partner_age=age_range[0],
                        max_partner_age=age_range[1],
                    ),
                )
            except MatchwellError as error:
                st.error(str(error))
            else:
                render_success_state("Matching preferences saved.")
                st.rerun()

    with st.container(border=True):
        render_eyebrow("Matching pool status")
        if not progress.readiness.eligible:
            render_empty_state(
                "Finish your readiness requirements to enter the matching pool."
            )
        elif not preferences.completed:
            render_empty_state(
                "Save your matching preferences above to enter the matching pool."
            )
        else:
            render_badges([("In the matching pool", "success")])
            st.write(
                "A counselor reviews every candidate and must approve before "
                "you receive an introduction."
            )


def render_introduction(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Introduction")
    try:
        matched_pair = service.matched_pair(actor)
        introduction = service.introduction(actor)
        recent_match = service.recent_match(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    if matched_pair is not None:
        with st.container(border=True):
            render_eyebrow("Matched-pair workspace")
            st.subheader(f"You and {matched_pair.partner_display_name}")
            render_badges([("Mutually accepted", "success")])
            st.caption(f"Activated {matched_pair.activated_at.strftime('%B %d, %Y')}")
            st.write(
                "Guided curriculum, check-ins, and messaging arrive in a "
                "later milestone. For now, this page confirms your match is "
                "active."
            )
        _render_safety_actions(
            service,
            actor,
            matched_pair.partner_id,
            matched_pair.partner_display_name,
        )
        return

    if introduction is None:
        render_empty_state(
            "You do not have a pending introduction yet. Your counselor will "
            "let you know when one is ready."
        )
        if recent_match is not None:
            _render_safety_actions(
                service,
                actor,
                recent_match.partner_id,
                recent_match.partner_display_name,
            )
        return

    with st.container(border=True):
        render_eyebrow("Counselor-approved introduction")
        st.subheader(introduction.partner_display_name)
        render_badges([("Awaiting responses", "info")])
        location_col, denomination_col = st.columns(2)
        with location_col:
            st.metric(
                "Location",
                f"{introduction.partner_city}, {introduction.partner_state}",
            )
        with denomination_col:
            st.metric(
                "Denomination",
                introduction.partner_denomination or "Not specified",
            )
        st.caption("Relationship intent")
        st.write(introduction.partner_relationship_intent)
        st.caption("Why this introduction")
        for explanation in introduction.explanations:
            st.write(f"- {explanation}")

    if introduction.my_response is not None:
        render_empty_state(
            "You already responded. We will let you know once your match "
            "responds too. Your answer stays private until then."
        )
    else:
        st.write(
            "Review the safe summary above, then privately accept or "
            "decline. Your match will never see your response."
        )
        accept_col, decline_col = st.columns(2)
        with accept_col:
            accept_clicked = st.button(
                "Accept introduction",
                type="primary",
                use_container_width=True,
            )
        with decline_col:
            decline_clicked = st.button(
                "Decline introduction",
                use_container_width=True,
            )
        if accept_clicked:
            _respond(service, actor, introduction.proposal_id, accepted=True)
        if decline_clicked:
            _respond(service, actor, introduction.proposal_id, accepted=False)

    _render_safety_actions(
        service,
        actor,
        introduction.partner_id,
        introduction.partner_display_name,
    )


def _respond(
    service: PilotService,
    actor: AuthenticatedUser,
    proposal_id: uuid.UUID,
    *,
    accepted: bool,
) -> None:
    decision = (
        MemberResponseDecision.ACCEPTED if accepted else MemberResponseDecision.DECLINED
    )
    try:
        service.respond_to_introduction(actor, proposal_id, decision)
    except MatchwellError as error:
        st.error(str(error))
    else:
        render_success_state("Your response was recorded privately.")
        st.rerun()


def _render_safety_actions(
    service: PilotService,
    actor: AuthenticatedUser,
    partner_id: uuid.UUID,
    partner_display_name: str,
) -> None:
    with st.expander("Safety: block or report this member"):
        st.caption(
            "Blocking or reporting immediately closes this introduction or "
            "workspace and notifies Matchwell operations. Neither action is "
            "visible to the other member."
        )
        block_tab, report_tab = st.tabs(["Block", "Report"])
        with block_tab:
            with st.form("block-member"):
                block_category = st.selectbox(
                    "Reason",
                    options=list(SafetyCategory),
                    format_func=lambda item: humanize(item.value),
                    key="block-category",
                )
                block_context = st.text_area(
                    "Additional context (optional)",
                    max_chars=500,
                    key="block-context",
                )
                block_submitted = st.form_submit_button("Block member")
            if block_submitted:
                try:
                    service.block_member(
                        actor,
                        partner_id,
                        block_category,
                        block_context or None,
                    )
                except MatchwellError as error:
                    st.error(str(error))
                else:
                    render_success_state(f"{partner_display_name} has been blocked.")
                    st.rerun()
        with report_tab:
            with st.form("report-member"):
                report_category = st.selectbox(
                    "Reason",
                    options=list(SafetyCategory),
                    format_func=lambda item: humanize(item.value),
                    key="report-category",
                )
                report_context = st.text_area(
                    "Describe what happened (required, kept confidential)",
                    max_chars=500,
                    key="report-context",
                )
                report_submitted = st.form_submit_button("Submit report")
            if report_submitted:
                try:
                    service.report_member(
                        actor,
                        partner_id,
                        report_category,
                        report_context,
                    )
                except MatchwellError as error:
                    st.error(str(error))
                else:
                    render_success_state(
                        "Your report was submitted to Matchwell operations."
                    )
                    st.rerun()
