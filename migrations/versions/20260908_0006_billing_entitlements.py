"""Create billing, entitlement, and counselor earnings tables.

Revision ID: 20260908_0006
Revises: 20260904_0005
Create Date: 2026-09-08
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0006"
down_revision: str | None = "20260904_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PILOT_PLAN_ID = uuid.UUID("6f9c9a6e-2f8c-4e6a-9d0a-6a4e2b7d5c11")
_PILOT_PLAN_KEY = "matchwell-pilot"
_PILOT_PLAN_NAME = "Matchwell Pilot"
_PILOT_PLAN_PRICE_MINOR_UNITS = 4_900
_PILOT_PLAN_CURRENCY = "usd"


def upgrade() -> None:
    op.create_table(
        "pilot_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("price_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "billing_customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id"),
        sa.UniqueConstraint("provider", "provider_customer_id"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=200), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_provider_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["pilot_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id"),
    )
    op.create_table(
        "entitlement_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entitlement_history_member_occurred",
        "entitlement_history",
        ["member_id", "occurred_at"],
    )
    op.create_table(
        "billing_webhook_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("unresolved_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id"),
    )
    op.create_table(
        "counselor_earnings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("center_id", sa.Uuid(), nullable=False),
        sa.Column("counselor_id", sa.Uuid(), nullable=False),
        sa.Column("intake_member_id", sa.Uuid(), nullable=True),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["center_id"], ["centers.id"]),
        sa.ForeignKeyConstraint(["counselor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["intake_member_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_counselor_earnings_center_id",
        "counselor_earnings",
        ["center_id"],
    )
    op.create_index(
        "ix_counselor_earnings_counselor_id",
        "counselor_earnings",
        ["counselor_id"],
    )
    op.create_index(
        "uq_counselor_earnings_intake_member",
        "counselor_earnings",
        ["intake_member_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'intake_credit'"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_counselor_earning_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'counselor_earnings are append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER counselor_earnings_append_only
        BEFORE UPDATE OR DELETE ON counselor_earnings
        FOR EACH ROW EXECUTE FUNCTION prevent_counselor_earning_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER entitlement_history_append_only
        BEFORE UPDATE OR DELETE ON entitlement_history
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
        """
    )

    plans = sa.table(
        "pilot_plans",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("price_minor_units", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        plans,
        [
            {
                "id": _PILOT_PLAN_ID,
                "key": _PILOT_PLAN_KEY,
                "name": _PILOT_PLAN_NAME,
                "price_minor_units": _PILOT_PLAN_PRICE_MINOR_UNITS,
                "currency": _PILOT_PLAN_CURRENCY,
                "is_active": True,
            }
        ],
    )

    # Backfill: existing members receive a complimentary pilot entitlement so
    # migration never silently locks out an already-onboarded member. New
    # members created after this migration have no row here until they
    # complete Stripe checkout, so they are never silently entitled. This
    # step only runs online (real upgrade): offline ``--sql`` rendering has
    # no live data to read, so schema/seed DDL still generates cleanly and
    # the backfill is skipped rather than failing.
    if context.is_offline_mode():
        return
    connection = op.get_bind()
    now = connection.execute(sa.text("SELECT now()")).scalar()
    existing_members = connection.execute(
        sa.text("SELECT id, center_id FROM users WHERE role = 'member'")
    ).fetchall()
    subscriptions = sa.table(
        "subscriptions",
        sa.column("id", sa.Uuid()),
        sa.column("member_id", sa.Uuid()),
        sa.column("center_id", sa.Uuid()),
        sa.column("plan_id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("provider", sa.String()),
        sa.column("cancel_at_period_end", sa.Boolean()),
        sa.column("reason_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    history = sa.table(
        "entitlement_history",
        sa.column("id", sa.Uuid()),
        sa.column("member_id", sa.Uuid()),
        sa.column("center_id", sa.Uuid()),
        sa.column("from_status", sa.String()),
        sa.column("to_status", sa.String()),
        sa.column("source", sa.String()),
        sa.column("reason_code", sa.String()),
        sa.column("safe_metadata", postgresql.JSONB()),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
    )
    if existing_members:
        op.bulk_insert(
            subscriptions,
            [
                {
                    "id": uuid.uuid4(),
                    "member_id": row.id,
                    "center_id": row.center_id,
                    "plan_id": _PILOT_PLAN_ID,
                    "status": "complimentary",
                    "provider": "complimentary",
                    "cancel_at_period_end": False,
                    "reason_code": "migration-backfill",
                    "created_at": now,
                    "updated_at": now,
                }
                for row in existing_members
            ],
        )
        op.bulk_insert(
            history,
            [
                {
                    "id": uuid.uuid4(),
                    "member_id": row.id,
                    "center_id": row.center_id,
                    "from_status": None,
                    "to_status": "complimentary",
                    "source": "migration_backfill",
                    "reason_code": "migration-backfill",
                    "safe_metadata": {},
                    "occurred_at": now,
                }
                for row in existing_members
            ],
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS entitlement_history_append_only ON entitlement_history;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS counselor_earnings_append_only ON counselor_earnings;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_counselor_earning_mutation();")
    op.drop_index(
        "uq_counselor_earnings_intake_member", table_name="counselor_earnings"
    )
    op.drop_index("ix_counselor_earnings_counselor_id", table_name="counselor_earnings")
    op.drop_index("ix_counselor_earnings_center_id", table_name="counselor_earnings")
    op.drop_table("counselor_earnings")
    op.drop_table("billing_webhook_receipts")
    op.drop_index(
        "ix_entitlement_history_member_occurred", table_name="entitlement_history"
    )
    op.drop_table("entitlement_history")
    op.drop_table("subscriptions")
    op.drop_table("billing_customers")
    op.drop_table("pilot_plans")
