"""Counselor and administrator operations pages."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import streamlit as st

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser, Role
from matchwell.domain.errors import MatchwellError
from matchwell.domain.pilot import (
    CounselorDecisionStatus,
    InvitationInput,
    OperationsMember,
    ScreeningStatus,
)


def render_admin(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Pilot operations")
    invitation_tab, members_tab = st.tabs(["Invitations", "Member readiness"])
    with invitation_tab:
        _render_invitations(service, actor)
    with members_tab:
        _render_member_operations(service, actor)


def render_counselor(service: PilotService, actor: AuthenticatedUser) -> None:
    st.title("Counselor workspace")
    try:
        members = service.assigned_members(actor)
    except MatchwellError as error:
        st.error(str(error))
        return
    if not members:
        st.info("No members are currently assigned to you.")
        return

    selected = _member_selector(members, "counselor-member")
    st.write(f"**Screening:** {selected.screening_status.value.title()}")
    st.write(f"**Current decision:** {selected.counselor_status.value.title()}")
    st.write(f"**Community eligible:** {'Yes' if selected.eligible else 'No'}")
    st.caption("Assessment answers are intentionally excluded from this queue.")

    with st.form("counselor-decision"):
        status = st.selectbox(
            "Structured intake decision",
            options=[
                CounselorDecisionStatus.APPROVED,
                CounselorDecisionStatus.DECLINED,
                CounselorDecisionStatus.PENDING,
            ],
            format_func=lambda item: item.value.title(),
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
            st.success("Counselor decision recorded.")
            st.rerun()


def _render_invitations(service: PilotService, actor: AuthenticatedUser) -> None:
    with st.form("create-invitation"):
        email = st.text_input("Verified Google account email")
        role = st.selectbox(
            "Role",
            options=[Role.MEMBER, Role.COUNSELOR],
            format_func=lambda item: item.value.title(),
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
            st.success("Invitation created. Ask the member to sign in with that email.")

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
                    "Role": item.role.value.title(),
                    "Expires": item.expires_at,
                    "Accepted": item.accepted_at,
                }
                for item in invitations
            ],
            hide_index=True,
            use_container_width=True,
        )


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
        st.info("Invite a member to begin the pilot journey.")
        return

    st.dataframe(
        [
            {
                "Member": item.display_name,
                "Email": item.email,
                "Counselor": item.counselor_status.value.title(),
                "Screening": item.screening_status.value.title(),
                "Hold": "Active" if item.hold_active else "None",
                "Eligible": "Yes" if item.eligible else "No",
            }
            for item in members
        ],
        hide_index=True,
        use_container_width=True,
    )
    selected = _member_selector(members, "operations-member")

    st.subheader("Counselor assignment")
    if not counselors:
        st.info("Invite a counselor before assigning this member.")
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
                st.success("Counselor assigned.")
                st.rerun()

    st.subheader("Screening status")
    with st.form("screening-status"):
        screening_status = st.selectbox(
            "Normalized status",
            options=list(ScreeningStatus),
            format_func=lambda item: item.value.replace("_", " ").title(),
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
                st.success("Screening status recorded.")
            else:
                st.info("That provider event was already processed.")
            st.rerun()

    st.subheader("Safety and administrative hold")
    if selected.hold_active:
        if st.button("Release active hold"):
            try:
                service.release_hold(actor, selected.id)
            except MatchwellError as error:
                st.error(str(error))
            else:
                st.success("Hold released.")
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
                st.success("Hold applied. Eligibility is blocked.")
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
