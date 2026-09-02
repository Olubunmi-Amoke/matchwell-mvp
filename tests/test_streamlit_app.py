from pathlib import Path

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from matchwell.infrastructure.settings import get_settings


def test_app_renders_without_database_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    app_path = Path(__file__).parents[1] / "app" / "main.py"

    app = AppTest.from_file(str(app_path)).run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "Matchwell setup"
    assert "DATABASE_URL is not configured." in app.error[0].value


def test_app_renders_google_login_when_database_is_ready(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    app_path = Path(__file__).parents[1] / "app" / "main.py"

    app = AppTest.from_file(str(app_path)).run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "Matchwell"
    assert app.button[0].label == "Continue with Google"
