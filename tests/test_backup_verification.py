from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _verification_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "backup" / "verify_invariants.py"
    spec = spec_from_file_location("verify_invariants", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_neon_postgresql_url_uses_psycopg3_driver() -> None:
    module = _verification_module()

    assert module._sqlalchemy_url(
        "postgresql://user:password@example.neon.tech/neondb?sslmode=require"
    ).startswith("postgresql+psycopg://")
