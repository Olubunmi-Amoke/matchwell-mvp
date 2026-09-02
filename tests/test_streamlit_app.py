from pathlib import Path

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest


def test_app_renders_without_database_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app_path = Path(__file__).parents[1] / "app" / "main.py"

    app = AppTest.from_file(str(app_path)).run(timeout=10)

    assert not app.exception
    assert app.title[0].value == "Matchwell"
    assert "DATABASE_URL is not configured." in app.error[0].value
