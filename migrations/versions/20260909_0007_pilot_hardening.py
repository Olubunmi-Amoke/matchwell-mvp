"""Pilot hardening: account status, webhook/screening scoping, backup drills.

Revision ID: 20260909_0007
Revises: 20260908_0006
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0007"
down_revision: str | None = "20260908_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACCOUNT_STATUSES = ("active", "disabled")
_DISABLE_REASON_CODES = (
    "safety_concern",
    "policy_violation",
    "member_requested",
    "duplicate_account",
    "inactive_account",
    "other_operational",
)
_REACTIVATE_REASON_CODES = (
    "safety_concern_resolved",
    "member_requested",
    "admin_allowlist_restored",
    "entered_in_error",
    "other_operational",
)
_SCREENING_REASON_CODES = (
    "identity_verification_failed",
    "provider_ineligible_result",
    "provider_error",
    "provider_timeout",
    "document_unreadable",
    "duplicate_submission",
    "manual_review_required",
    "expired",
    "other_operational",
)


def upgrade() -> None:
    # --- Account revocation state -----------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "users",
        sa.Column("disabled_reason_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("disabled_by_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("reactivated_reason_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("reactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("reactivated_by_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_disabled_by_id",
        "users",
        "users",
        ["disabled_by_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_users_reactivated_by_id",
        "users",
        "users",
        ["reactivated_by_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_users_status_allowlist",
        "users",
        f"status IN ({', '.join(repr(v) for v in _ACCOUNT_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_users_disabled_reason_allowlist",
        "users",
        "disabled_reason_code IS NULL OR disabled_reason_code IN "
        f"({', '.join(repr(v) for v in _DISABLE_REASON_CODES)})",
    )
    op.create_check_constraint(
        "ck_users_reactivated_reason_allowlist",
        "users",
        "reactivated_reason_code IS NULL OR reactivated_reason_code IN "
        f"({', '.join(repr(v) for v in _REACTIVATE_REASON_CODES)})",
    )
    op.create_index("ix_users_status", "users", ["status"])
    op.alter_column("users", "status", server_default=None)

    # --- Center-scoped billing webhook diagnostics --------------------------
    op.add_column(
        "billing_webhook_receipts",
        sa.Column("center_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_billing_webhook_receipts_center_id",
        "billing_webhook_receipts",
        "centers",
        ["center_id"],
        ["id"],
    )
    op.create_index(
        "ix_billing_webhook_receipts_center_id",
        "billing_webhook_receipts",
        ["center_id"],
    )

    # --- Screening provider failure/retry diagnostics -----------------------
    # Mirrors the billing webhook receipt shape so admins get the same safe
    # triage/reprocess workflow. Never stores a screening report or free text.
    op.add_column(
        "screening_event_receipts",
        sa.Column("member_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "screening_event_receipts",
        sa.Column("center_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "screening_event_receipts",
        sa.Column(
            "event_type",
            sa.String(length=50),
            nullable=False,
            server_default="status_update",
        ),
    )
    op.add_column(
        "screening_event_receipts",
        sa.Column(
            "applied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "screening_event_receipts",
        sa.Column("unresolved_reason", sa.String(length=50), nullable=True),
    )
    op.create_foreign_key(
        "fk_screening_event_receipts_member_id",
        "screening_event_receipts",
        "users",
        ["member_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_screening_event_receipts_center_id",
        "screening_event_receipts",
        "centers",
        ["center_id"],
        ["id"],
    )
    op.create_index(
        "ix_screening_event_receipts_center_id",
        "screening_event_receipts",
        ["center_id"],
    )
    op.alter_column("screening_event_receipts", "event_type", server_default=None)
    op.alter_column("screening_event_receipts", "applied", server_default=None)

    # --- Constrain screening reason codes to a safe allowlist ---------------
    allowed_screening_reasons = ", ".join(repr(v) for v in _SCREENING_REASON_CODES)
    op.execute(
        sa.text(
            "UPDATE screening_cases SET reason_code = 'other_operational' "
            "WHERE reason_code IS NOT NULL "
            f"AND reason_code NOT IN ({allowed_screening_reasons})"
        )
    )
    op.create_check_constraint(
        "ck_screening_cases_reason_allowlist",
        "screening_cases",
        f"reason_code IS NULL OR reason_code IN ({allowed_screening_reasons})",
    )
    op.create_check_constraint(
        "ck_screening_event_receipts_reason_allowlist",
        "screening_event_receipts",
        "unresolved_reason IS NULL OR unresolved_reason IN "
        f"({allowed_screening_reasons})",
    )

    # --- Operator-recorded backup/restore drill evidence --------------------
    # Populated only by a human operator completing the restore runbook
    # against a real backup; never written by application request handling.
    op.create_table(
        "backup_drill_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_by", sa.String(length=320), nullable=False),
        sa.Column("target_description", sa.String(length=200), nullable=False),
        sa.Column("verification_passed", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("backup_drill_runs")

    op.drop_constraint(
        "ck_screening_event_receipts_reason_allowlist",
        "screening_event_receipts",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_cases_reason_allowlist", "screening_cases", type_="check"
    )

    op.drop_index(
        "ix_screening_event_receipts_center_id", table_name="screening_event_receipts"
    )
    op.drop_constraint(
        "fk_screening_event_receipts_center_id",
        "screening_event_receipts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_screening_event_receipts_member_id",
        "screening_event_receipts",
        type_="foreignkey",
    )
    op.drop_column("screening_event_receipts", "unresolved_reason")
    op.drop_column("screening_event_receipts", "applied")
    op.drop_column("screening_event_receipts", "event_type")
    op.drop_column("screening_event_receipts", "center_id")
    op.drop_column("screening_event_receipts", "member_id")

    op.drop_index(
        "ix_billing_webhook_receipts_center_id", table_name="billing_webhook_receipts"
    )
    op.drop_constraint(
        "fk_billing_webhook_receipts_center_id",
        "billing_webhook_receipts",
        type_="foreignkey",
    )
    op.drop_column("billing_webhook_receipts", "center_id")

    op.drop_index("ix_users_status", table_name="users")
    op.drop_constraint("ck_users_reactivated_reason_allowlist", "users", type_="check")
    op.drop_constraint("ck_users_disabled_reason_allowlist", "users", type_="check")
    op.drop_constraint("ck_users_status_allowlist", "users", type_="check")
    op.drop_constraint("fk_users_reactivated_by_id", "users", type_="foreignkey")
    op.drop_constraint("fk_users_disabled_by_id", "users", type_="foreignkey")
    op.drop_column("users", "reactivated_by_id")
    op.drop_column("users", "reactivated_at")
    op.drop_column("users", "reactivated_reason_code")
    op.drop_column("users", "disabled_by_id")
    op.drop_column("users", "disabled_at")
    op.drop_column("users", "disabled_reason_code")
    op.drop_column("users", "status")
