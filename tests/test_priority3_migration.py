import importlib.util
import io
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    inspect,
    select,
)


def _migration() -> object:
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "20260916_0010_personality_and_matching_modes.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_priority3_migration_upgrades_sqlite_and_backfills_members() -> None:
    migration = _migration()
    engine = create_engine("sqlite://")
    metadata = MetaData()
    centers = Table("centers", metadata, Column("id", Uuid, primary_key=True))
    communities = Table(
        "communities",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("center_id", Uuid, nullable=False),
        Column("slug", String(100), nullable=False),
        Column("name", String(200), nullable=False),
    )
    users = Table(
        "users",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("center_id", Uuid, nullable=False),
        Column("role", String(30), nullable=False),
    )
    Table("match_proposals", metadata, Column("id", Uuid, primary_key=True))
    center_id = migration.PILOT_CENTER_ID  # type: ignore[attr-defined]
    community_id = uuid.uuid4()
    member_id = uuid.uuid4()
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(centers.insert(), {"id": center_id})
        connection.execute(
            communities.insert(),
            {
                "id": community_id,
                "center_id": center_id,
                "slug": "intentional-relationships",
                "name": "Intentional Relationships Community",
            },
        )
        connection.execute(
            users.insert(),
            {"id": member_id, "center_id": center_id, "role": "member"},
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()  # type: ignore[attr-defined]
        inspector = inspect(connection)
        assert {
            "member_community_assignments",
            "personality_inventory_definitions",
            "personality_inventory_assignments",
            "personality_inventory_responses",
            "personality_inventory_scores",
            "self_paced_suggestion_interests",
        }.issubset(inspector.get_table_names())
        reflected = MetaData()
        reflected.reflect(connection)
        assignments = reflected.tables["member_community_assignments"]
        assert (
            connection.execute(select(assignments.c.community_id)).scalar_one()
            == community_id.hex
        )
        definition = reflected.tables["personality_inventory_definitions"]
        assert connection.execute(select(definition.c.version)).scalar_one() == "1.0"
        rows = connection.execute(
            select(reflected.tables["communities"].c.matching_mode)
        ).scalars()
        assert set(rows) == {"counselor_based", "self_paced"}


def test_priority3_migration_renders_offline_postgresql_sql() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        _migration().upgrade()  # type: ignore[attr-defined]
    sql = output.getvalue()
    assert "CREATE TABLE personality_inventory_responses" in sql
    assert "CREATE TABLE self_paced_suggestion_interests" in sql
    assert "WHERE ended_at IS NULL" in sql
