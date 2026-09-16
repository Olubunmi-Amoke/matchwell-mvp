import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError


def _load_migration() -> object:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260916_0009_audited_rematching.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_sqlite_rematch_migration_upgrade_and_downgrade() -> None:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    centers = Table("centers", metadata, Column("id", String(32), primary_key=True))
    users = Table(
        "users",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
    )
    communities = Table(
        "communities",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
    )
    proposals = Table(
        "match_proposals",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
        Column(
            "community_id",
            String(32),
            ForeignKey(communities.c.id),
            nullable=False,
        ),
        Column("member_a_id", String(32), ForeignKey(users.c.id), nullable=False),
        Column("member_b_id", String(32), ForeignKey(users.c.id), nullable=False),
        Column("status", String(30), nullable=False),
        Column("score", Float, nullable=False),
        Column("score_breakdown", JSON, nullable=False),
        Column("counselor_a_id", String(32), ForeignKey(users.c.id)),
        Column("counselor_a_decision", String(20), nullable=False),
        Column("counselor_a_decided_at", DateTime(timezone=True)),
        Column("counselor_a_reason_code", String(100)),
        Column("counselor_b_id", String(32), ForeignKey(users.c.id)),
        Column("counselor_b_decision", String(20), nullable=False),
        Column("counselor_b_decided_at", DateTime(timezone=True)),
        Column("counselor_b_reason_code", String(100)),
        Column("introduced_at", DateTime(timezone=True)),
        Column("member_a_response", String(20)),
        Column("member_a_responded_at", DateTime(timezone=True)),
        Column("member_b_response", String(20)),
        Column("member_b_responded_at", DateTime(timezone=True)),
        Column("activated_at", DateTime(timezone=True)),
        Column("closed_at", DateTime(timezone=True)),
        Column("closed_reason", String(100)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("member_a_id", "member_b_id"),
    )
    ids = {key: uuid.uuid4().hex for key in ("center", "community", "a", "b")}
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(centers.insert(), {"id": ids["center"]})
        connection.execute(
            communities.insert(),
            {"id": ids["community"], "center_id": ids["center"]},
        )
        connection.execute(
            users.insert(),
            [
                {"id": ids["a"], "center_id": ids["center"]},
                {"id": ids["b"], "center_id": ids["center"]},
            ],
        )
        row = {
            "id": uuid.uuid4().hex,
            "center_id": ids["center"],
            "community_id": ids["community"],
            "member_a_id": ids["a"],
            "member_b_id": ids["b"],
            "status": "closed",
            "score": 1,
            "score_breakdown": [],
            "counselor_a_decision": "declined",
            "counselor_b_decision": "declined",
            "created_at": datetime(2026, 9, 16, tzinfo=UTC),
        }
        connection.execute(proposals.insert(), row)
        context = MigrationContext.configure(connection)
        migration = _load_migration()
        with Operations.context(context):
            migration.upgrade()  # type: ignore[attr-defined]

        assert "rematch_authorizations" in inspect(connection).get_table_names()
        assert (
            "match_proposal_participant_claims" in inspect(connection).get_table_names()
        )
        assert not inspect(connection).get_unique_constraints("match_proposals")
        second = {**row, "id": uuid.uuid4().hex}
        connection.execute(proposals.insert(), second)
        open_id = uuid.uuid4().hex
        connection.execute(
            proposals.insert(),
            {**row, "id": open_id, "status": "pending_review"},
        )
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError), savepoint:
            connection.execute(
                proposals.insert(),
                {**row, "id": uuid.uuid4().hex, "status": "introduced"},
            )
        connection.execute(
            text("DELETE FROM match_proposals WHERE id IN (:second, :open)"),
            {"second": second["id"], "open": open_id},
        )
        with Operations.context(context):
            migration.downgrade()  # type: ignore[attr-defined]
        assert "rematch_authorizations" not in inspect(connection).get_table_names()
        assert (
            "match_proposal_participant_claims"
            not in inspect(connection).get_table_names()
        )
        assert inspect(connection).get_unique_constraints("match_proposals")


def test_sqlite_rematch_migration_backfills_cross_side_claims_and_refuses_repeat_downgrade() -> (
    None
):
    engine = create_engine("sqlite://")
    metadata = MetaData()
    centers = Table("centers", metadata, Column("id", String(32), primary_key=True))
    users = Table(
        "users",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
    )
    communities = Table(
        "communities",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
    )
    proposals = Table(
        "match_proposals",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("center_id", String(32), ForeignKey(centers.c.id), nullable=False),
        Column(
            "community_id", String(32), ForeignKey(communities.c.id), nullable=False
        ),
        Column("member_a_id", String(32), ForeignKey(users.c.id), nullable=False),
        Column("member_b_id", String(32), ForeignKey(users.c.id), nullable=False),
        Column("status", String(30), nullable=False),
        Column("score", Float, nullable=False),
        Column("score_breakdown", JSON, nullable=False),
        Column("counselor_a_id", String(32)),
        Column("counselor_a_decision", String(20), nullable=False),
        Column("counselor_a_decided_at", DateTime(timezone=True)),
        Column("counselor_a_reason_code", String(100)),
        Column("counselor_b_id", String(32)),
        Column("counselor_b_decision", String(20), nullable=False),
        Column("counselor_b_decided_at", DateTime(timezone=True)),
        Column("counselor_b_reason_code", String(100)),
        Column("introduced_at", DateTime(timezone=True)),
        Column("member_a_response", String(20)),
        Column("member_a_responded_at", DateTime(timezone=True)),
        Column("member_b_response", String(20)),
        Column("member_b_responded_at", DateTime(timezone=True)),
        Column("activated_at", DateTime(timezone=True)),
        Column("closed_at", DateTime(timezone=True)),
        Column("closed_reason", String(100)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("member_a_id", "member_b_id"),
    )
    ids = {key: uuid.uuid4().hex for key in ("center", "community", "a", "b", "c")}
    base = {
        "center_id": ids["center"],
        "community_id": ids["community"],
        "score": 1,
        "score_breakdown": [],
        "counselor_a_decision": "declined",
        "counselor_b_decision": "declined",
        "created_at": datetime(2026, 9, 16, tzinfo=UTC),
    }
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(centers.insert(), {"id": ids["center"]})
        connection.execute(
            communities.insert(),
            {"id": ids["community"], "center_id": ids["center"]},
        )
        connection.execute(
            users.insert(),
            [{"id": ids[key], "center_id": ids["center"]} for key in ("a", "b", "c")],
        )
        first_id = uuid.uuid4().hex
        connection.execute(
            proposals.insert(),
            {
                **base,
                "id": first_id,
                "member_a_id": ids["a"],
                "member_b_id": ids["b"],
                "status": "pending_review",
            },
        )
        context = MigrationContext.configure(connection)
        migration = _load_migration()
        with Operations.context(context):
            migration.upgrade()  # type: ignore[attr-defined]
        claims = connection.execute(
            text("SELECT proposal_id, member_id FROM match_proposal_participant_claims")
        ).all()
        assert set(claims) == {(first_id, ids["a"]), (first_id, ids["b"])}
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError), savepoint:
            connection.execute(
                text(
                    "INSERT INTO match_proposal_participant_claims "
                    "(proposal_id, member_id) VALUES (:proposal, :member)"
                ),
                {"proposal": uuid.uuid4().hex, "member": ids["a"]},
            )
        connection.execute(
            text(
                "UPDATE match_proposals SET status = 'closed', closed_at = :now "
                "WHERE id = :id"
            ),
            {"now": datetime.now(UTC), "id": first_id},
        )
        connection.execute(
            text(
                "DELETE FROM match_proposal_participant_claims WHERE proposal_id = :id"
            ),
            {"id": first_id},
        )
        connection.execute(
            proposals.insert(),
            {
                **base,
                "id": uuid.uuid4().hex,
                "member_a_id": ids["a"],
                "member_b_id": ids["b"],
                "status": "closed",
            },
        )
        with pytest.raises(RuntimeError, match="repeat pair history"):
            with Operations.context(context):
                migration.downgrade()  # type: ignore[attr-defined]
        assert "rematch_authorizations" in inspect(connection).get_table_names()
