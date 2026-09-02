import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from matchwell.domain.system_health import ComponentHealth, HealthStatus

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def create_database_engine(database_url: str) -> Engine:
    connect_args = (
        {"connect_timeout": 5}
        if make_url(database_url).get_backend_name() == "postgresql"
        else {}
    )
    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


def upgrade_database(database_url: str) -> None:
    config = Config(str(Path(__file__).parents[4] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    engine = create_database_engine(database_url)
    lock_key = 6_409_402_025
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


class DatabaseSessionFactory:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._session_factory() as session:
            yield session


class SqlAlchemyDatabaseProbe:
    def __init__(self, database_url: str | None) -> None:
        self._engine = (
            create_database_engine(database_url) if database_url is not None else None
        )

    def check(self) -> ComponentHealth:
        if self._engine is None:
            return ComponentHealth(
                name="PostgreSQL",
                status=HealthStatus.DEGRADED,
                detail="DATABASE_URL is not configured.",
            )

        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            logger.warning(
                "Database health check failed.",
                extra={"error_type": type(error).__name__},
            )
            return ComponentHealth(
                name="PostgreSQL",
                status=HealthStatus.DEGRADED,
                detail="Database connection failed.",
            )

        return ComponentHealth(
            name="PostgreSQL",
            status=HealthStatus.READY,
            detail="Database connection is healthy.",
        )
