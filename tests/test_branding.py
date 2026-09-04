from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_branding_assets_have_expected_dimensions() -> None:
    with Image.open(PROJECT_ROOT / "assets" / "matchwell-icon.png") as image:
        assert image.size == (512, 512)
        assert image.format == "PNG"
