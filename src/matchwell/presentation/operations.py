"""Counselor and administrator operations pages, redesigned around queues."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import streamlit as st

from matchwell.application.pilot import (
    COUNSELOR_TO_MEMBER_REASON_CODES,
    ROLE_REASSIGNMENT_REASON_CODES,
    PilotService,
)
from matchwell.domain.access import AuthenticatedUser, Role
from matchwell.domain.errors import MatchwellError
from matchwell.domain.matching import CounselorReviewDecision
from matchwell.domain.pilot import (
    CounselorDecisionStatus,
    InvitationInput,
    OperationsMember,
    ScreeningStatus,
)
from matchwell.presentation.theme import (
    humanize,
    render_badges,
    render_empty_state,
    render_eyebrow,
    render_success_state,
)


def render_admin(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Pilot operations")
    st.caption("Task-oriented queues for the pilot Center.")
    invitation_tab, members_tab, counselors_tab, matching_tab = st.tabs(
        ["Invitations", "Member readiness", "Counselors", "Matching"]
    )
    with invitation_tab:
        _render_invitations(service, actor)
    with members_tab:
        _render_member_operations(service, actor)
    with counselors_tab:
        _render_counselor_operations(service, actor)
    with matching_tab:
        _render_admin_matching(service, actor)


def render_counselor(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Counselor workspace")
    intake_tab, matching_tab, activity_tab = st.tabs(
        ["Assigned members", "Matching review", "Match activity"]
    )
    with intake_tab:
        _render_counselor_intake(service, actor)
    with matching_tab:
        _render_counselor_matching(service, actor)
    with activity_tab:
        _render_counselor_conversation_activity(service, actor)


def _render_counselor_intake(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    try:
        members = service.assigned_members(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not members:
        render_empty_state("No members are currently assigned to you.")
        return

    selected = _member_selector(members, "counselor-member")
    with st.container(border=True):
        render_eyebrow(f"Stage: {humanize(selected.readiness.stage.value)}")
        st.write(f"**Readiness:** {selected.readiness_completed_count}/7")
        render_badges(
            [
                (humanize(selected.screening_status.value), "info"),
                (humanize(selected.counselor_status.value), "neutral"),
                (
                    "Community eligible" if selected.eligible else "Not yet eligible",
                    "success" if selected.eligible else "warning",
                ),
            ]
        )
        if not selected.eligible:
            st.caption("Missing requirements and next actions")
            for explanation in selected.readiness.explanations:
                st.write(f"- {explanation}")
        st.caption("Assessment answers are intentionally excluded from this queue.")

    with st.form("counselor-decision"):
        status = st.selectbox(
            "Structured intake decision",
            options=[
                CounselorDecisionStatus.APPROVED,
                CounselorDecisionStatus.DECLINED,
                CounselorDecisionStatus.PENDING,
            ],
            format_func=lambda item: humanize(item.value),
        )
        reason_code = st.text_input(
            "Reason code (optional)",
            help="Use a short operational code, not counseling notes.",
        )
        submitted = st.form_submit_button("Record decision", type="primary")
    if submitted:
        try:
            service.record_counselor_decision(
                actor,
                selected.id,
                status,
                reason_code or None,
            )
        except MatchwellError as error:
            st.error(str(error))
        else:
            render_success_state("Counselor decision recorded.")
            st.rerun()


def _render_counselor_matching(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    try:
        queue = service.candidate_queue(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not queue:
        render_empty_state("No candidates are waiting for your review.")
        return

    st.write(
        "Prioritized by compatibility score, highest first. Both assigned "
        "counselors must approve before either member sees the "
        "introduction."
    )
    for item in queue:
        with st.container(border=True):
            st.subheader(f"{item.member_display_name} + {item.partner_display_name}")
            render_badges(
                [
                    (f"Score {round(item.score)}", "info"),
                    (
                        "Partner counselor: "
                        f"{humanize(item.partner_counselor_decision.value)}",
                        "neutral",
                    ),
                ]
            )
            st.caption("Why this candidate")
            for explanation in item.explanations:
                st.write(f"- {explanation}")
            with st.form(f"review-{item.proposal_id}"):
                reason_code = st.text_input(
                    "Reason code (optional)",
                    key=f"reason-{item.proposal_id}",
                )
                approve_col, decline_col = st.columns(2)
                with approve_col:
                    approve_clicked = st.form_submit_button(
                        "Approve",
                        type="primary",
                        use_container_width=True,
                    )
                with decline_col:
                    decline_clicked = st.form_submit_button(
                        "Decline",
                        use_container_width=True,
                    )
            if approve_clicked or decline_clicked:
                decision = (
                    CounselorReviewDecision.APPROVED
                    if approve_clicked
                    else CounselorReviewDecision.DECLINED
                )
                try:
                    service.review_candidate(
                        actor,
                        item.proposal_id,
                        decision,
                        reason_code or None,
                    )
                except MatchwellError as error:
                    st.error(str(error))
                else:
                    render_success_state("Candidate review recorded.")
                    st.rerun()


def _render_counselor_conversation_activity(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    st.subheader("Matched-pair activity")
    st.caption(
        "Participation metadata supports timely guidance. Private message "
        "content is never available to counselors."
    )
    try:
        statuses = service.conversation_statuses(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not statuses:
        render_empty_state("No active matched pairs involve your members.")
        return
    for status in statuses:
        with st.container(border=True):
            st.write(
                f"**{status.member_a_display_name} + {status.member_b_display_name}**"
            )
            if status.started and status.latest_activity_at is not None:
                render_badges([("Conversation started", "success")])
                st.caption(
                    "Latest activity "
                    f"{status.latest_activity_at.strftime('%b %d, %Y at %I:%M %p')}"
                    f" · {status.message_count} message"
                    f"{'' if status.message_count == 1 else 's'}"
                )
            else:
                render_badges([("Not started", "neutral")])
                st.caption("No conversation activity yet.")


def _render_admin_matching(service: PilotService, actor: AuthenticatedUser) -> None:
    st.subheader("Candidate generation")
    st.write(
        "Generate deterministic, explainable candidate proposals for every "
        "7/7 community-eligible member who has completed matching "
        "preferences and has no open proposal. Assessment answers, "
        "screening details, and counselor notes are never used."
    )
    expand_diagnostics = False
    if st.button("Generate candidates", type="primary"):
        try:
            created = service.generate_candidates(actor)
        except MatchwellError as error:
            st.error(str(error))
        else:
            if created:
                render_success_state(f"Created {created} candidate proposal(s).")
            else:
                render_empty_state("No new eligible candidate pairs were found.")
                expand_diagnostics = True

    with st.expander("Candidate diagnostics", expanded=expand_diagnostics):
        diagnostics_requested = st.button(
            "Refresh diagnostics",
            key="refresh-candidate-diagnostics",
        )
        if expand_diagnostics or diagnostics_requested:
            _render_candidate_diagnostics(service, actor)
        else:
            st.caption(
                "Run diagnostics to see member and pair eligibility explanations."
            )


def _render_candidate_diagnostics(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    try:
        diagnostics = service.candidate_generation_diagnostics(actor)
    except MatchwellError as error:
        st.error(str(error))
        return

    summary = st.columns(4)
    with summary[0]:
        st.metric("Members", diagnostics.total_members)
    with summary[1]:
        st.metric("Ready for pairing", diagnostics.ready_members)
    with summary[2]:
        st.metric("Pairs evaluated", diagnostics.evaluated_pairs)
    with summary[3]:
        st.metric("Eligible pairs", diagnostics.eligible_pairs)

    blocked_members = [
        member for member in diagnostics.members if not member.ready_for_pairing
    ]
    if blocked_members:
        st.caption("Member-level next actions")
        for member in blocked_members:
            st.write(f"**{member.display_name}**")
            for reason in member.reasons:
                st.write(f"- {reason}")

    if diagnostics.pairs:
        st.caption("Pair compatibility")
        for pair in diagnostics.pairs:
            label = f"{pair.member_a_display_name} + {pair.member_b_display_name}"
            if pair.eligible:
                st.success(f"{label}: eligible for candidate generation.")
            else:
                st.write(f"**{label}**")
                for reason in pair.reasons:
                    st.write(f"- {reason}")
    elif diagnostics.ready_members < 2:
        render_empty_state(
            "At least two individually ready members are needed to evaluate a pair."
        )


def _render_invitations(service: PilotService, actor: AuthenticatedUser) -> None:
    with st.form("create-invitation"):
        email = st.text_input("Verified Google account email")
        role = st.selectbox(
            "Role",
            options=[Role.MEMBER, Role.COUNSELOR],
            format_func=lambda item: humanize(item.value),
        )
        expiry_days = st.number_input(
            "Expires in days",
            min_value=1,
            max_value=30,
            value=7,
        )
        submitted = st.form_submit_button("Create invitation", type="primary")
    if submitted:
        try:
            service.create_invitation(
                actor,
                InvitationInput(
                    email=email,
                    role=role,
                    expires_at=datetime.now(UTC) + timedelta(days=expiry_days),
                ),
            )
        except (MatchwellError, ValueError) as error:
            st.error(str(error))
        else:
            render_success_state(
                "Invitation created. Ask the member to sign in with that email."
            )

    try:
        invitations = service.invitations(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if invitations:
        st.dataframe(
            [
                {
                    "Email": item.email,
                    "Role": humanize(item.role.value),
                    "Expires": item.expires_at,
                    "Accepted": item.accepted_at,
                }
                for item in invitations
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_counselor_operations(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    try:
        counselors = service.counselors(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not counselors:
        render_empty_state("No counselors are currently active in this Center.")
        return

    st.dataframe(
        [
            {
                "Counselor": counselor.name,
                "Email": counselor.email,
            }
            for counselor in counselors
        ],
        hide_index=True,
        use_container_width=True,
    )
    selected = st.selectbox(
        "Counselor",
        options=counselors,
        format_func=lambda item: f"{item.name} ({item.email})",
        key="operations-counselor",
    )

    st.subheader("Role management")
    with st.container(border=True):
        st.warning(
            "Changing this counselor to a member is permanent in the pilot. "
            "First reassign all active members and resolve their open match "
            "reviews. Historical counseling activity will be retained, while "
            "prior screening eligibility will reset."
        )
        with st.form(f"counselor-role-reassignment-{selected.id}"):
            role_reason = st.selectbox(
                "Reason",
                options=COUNSELOR_TO_MEMBER_REASON_CODES,
                format_func=humanize,
                key=f"counselor-role-reason-{selected.id}",
            )
            confirmation_email = st.text_input(
                f"Type {selected.email} to confirm",
                autocomplete="off",
                key=f"counselor-role-email-{selected.id}",
            )
            impact_confirmed = st.checkbox(
                "I understand this account will restart the member readiness journey.",
                key=f"counselor-role-confirmed-{selected.id}",
            )
            reassign_submitted = st.form_submit_button("Change counselor to member")
        if reassign_submitted:
            if not impact_confirmed:
                st.error("Confirm that you understand the role change.")
            else:
                try:
                    service.reassign_counselor_to_member(
                        actor,
                        selected.id,
                        confirmation_email,
                        role_reason,
                    )
                except MatchwellError as error:
                    st.error(str(error))
                else:
                    render_success_state(
                        "Role changed to member with fresh assessment and "
                        "screening requirements. Ask the user to sign out and "
                        "back in to open the member workspace."
                    )
                    st.rerun()


def _render_member_operations(
    service: PilotService,
    actor: AuthenticatedUser,
) -> None:
    try:
        members = service.members(actor)
        counselors = service.counselors(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not members:
        render_empty_state("Invite a member to begin the pilot journey.")
        return

    eligible_count = sum(1 for item in members if item.eligible)
    held_count = sum(1 for item in members if item.hold_active)
    summary = st.columns(3)
    with summary[0]:
        st.metric("Members", len(members))
    with summary[1]:
        st.metric("Community eligible", eligible_count)
    with summary[2]:
        st.metric("Active holds", held_count)

    st.dataframe(
        [
            {
                "Member": item.display_name,
                "Email": item.email,
                "Readiness": f"{item.readiness_completed_count}/7",
                "Stage": humanize(item.readiness.stage.value),
                "Counselor": humanize(item.counselor_status.value),
                "Screening": humanize(item.screening_status.value),
                "Hold": "Active" if item.hold_active else "None",
                "Eligible": "Yes" if item.eligible else "No",
            }
            for item in members
        ],
        hide_index=True,
        use_container_width=True,
    )
    selected = _member_selector(members, "operations-member")

    if not selected.eligible:
        with st.container(border=True):
            render_eyebrow("Missing requirements and next actions")
            for explanation in selected.readiness.explanations:
                st.write(f"- {explanation}")

    st.subheader("Counselor assignment")
    if not counselors:
        render_empty_state("Invite a counselor before assigning this member.")
    else:
        counselor = st.selectbox(
            "Counselor",
            options=counselors,
            format_func=lambda item: f"{item.name} ({item.email})",
        )
        if st.button("Assign counselor", type="primary"):
            try:
                service.assign_counselor(actor, selected.id, counselor.id)
            except MatchwellError as error:
                st.error(str(error))
            else:
                render_success_state("Counselor assigned.")
                st.rerun()

    st.subheader("Screening status")
    with st.form("screening-status"):
        screening_status = st.selectbox(
            "Normalized status",
            options=list(ScreeningStatus),
            format_func=lambda item: humanize(item.value),
        )
        provider_reference = st.text_input("Provider reference")
        provider_event_id = st.text_input(
            "Provider event ID",
            value=str(uuid.uuid4()),
        )
        reason_code = st.text_input("Reason code (optional)")
        screening_submitted = st.form_submit_button("Record screening status")
    if screening_submitted:
        try:
            created = service.record_screening_status(
                actor,
                selected.id,
                screening_status,
                provider_event_id,
                provider_reference,
                reason_code or None,
            )
        except MatchwellError as error:
            st.error(str(error))
        else:
            if created:
                render_success_state("Screening status recorded.")
            else:
                render_empty_state("That provider event was already processed.")
            st.rerun()

    st.subheader("Safety and administrative hold")
    if selected.hold_active:
        if st.button("Release active hold"):
            try:
                service.release_hold(actor, selected.id)
            except MatchwellError as error:
                st.error(str(error))
            else:
                render_success_state("Hold released.")
                st.rerun()
    else:
        with st.form("apply-hold"):
            hold_reason = st.text_input("Safe reason code")
            hold_submitted = st.form_submit_button("Apply hold")
        if hold_submitted:
            try:
                service.apply_hold(actor, selected.id, hold_reason)
            except MatchwellError as error:
                st.error(str(error))
            else:
                render_success_state("Hold applied. Eligibility is blocked.")
                st.rerun()

    st.divider()
    st.subheader("Role management")
    with st.container(border=True):
        st.warning(
            "Changing this member to a counselor is permanent in the pilot. "
            "Their history is retained, but their active counselor assignment "
            "and any open introduction or matched-pair access will close."
        )
        with st.form(f"role-reassignment-{selected.id}"):
            role_reason = st.selectbox(
                "Reason",
                options=ROLE_REASSIGNMENT_REASON_CODES,
                format_func=humanize,
            )
            confirmation_email = st.text_input(
                f"Type {selected.email} to confirm",
                autocomplete="off",
            )
            impact_confirmed = st.checkbox(
                "I understand this account will become a counselor."
            )
            reassign_submitted = st.form_submit_button("Change member to counselor")
        if reassign_submitted:
            if not impact_confirmed:
                st.error("Confirm that you understand the role change.")
            else:
                try:
                    service.reassign_member_to_counselor(
                        actor,
                        selected.id,
                        confirmation_email,
                        role_reason,
                    )
                except MatchwellError as error:
                    st.error(str(error))
                else:
                    render_success_state(
                        "Role changed to counselor. Ask the user to sign out "
                        "and back in to open the counselor workspace."
                    )
                    st.rerun()


def _member_selector(
    members: Sequence[OperationsMember],
    key: str,
) -> OperationsMember:
    return st.selectbox(
        "Member",
        options=members,
        format_func=lambda item: f"{item.display_name} ({item.email})",
        key=key,
    )
