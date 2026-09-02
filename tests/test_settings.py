from pydantic import SecretStr
from pytest import MonkeyPatch

from matchwell.infrastructure.settings import Settings


def test_settings_do_not_require_database_for_static_startup(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MATCHWELL_ENVIRONMENT", raising=False)

    settings = Settings()

    assert settings.environment == "development"
    assert settings.database_url is None
    assert settings.reveal_database_url() is None


def test_database_url_is_unwrapped_only_explicitly(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:secret@db:5432/matchwell",
    )
    settings = Settings()

    assert isinstance(settings.database_url, SecretStr)
    assert "secret" not in str(settings.database_url)
    assert settings.reveal_database_url() == (
        "postgresql+psycopg://user:secret@db:5432/matchwell"
    )
