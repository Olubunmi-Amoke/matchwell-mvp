import json
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_branding_assets_have_expected_dimensions() -> None:
    expected_sizes = {
        "matchwell-icon-180.png": (180, 180),
        "matchwell-icon-192.png": (192, 192),
        "matchwell-icon-512.png": (512, 512),
    }

    for filename, expected_size in expected_sizes.items():
        with Image.open(PROJECT_ROOT / "app" / "static" / filename) as image:
            assert image.size == expected_size
            assert image.format == "PNG"


def test_web_manifest_references_existing_icons() -> None:
    manifest_path = PROJECT_ROOT / "app" / "static" / "manifest.webmanifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "Matchwell"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (manifest_path.parent / icon["src"]).is_file()


def test_iphone_install_page_has_static_icon_metadata() -> None:
    document = (
        PROJECT_ROOT / "app" / "static" / "install-matchwell-v2.html"
    ).read_text(encoding="utf-8")

    assert 'rel="apple-touch-icon"' in document
    assert 'href="matchwell-home-v2-180.png"' in document
    assert 'rel="apple-touch-icon-precomposed"' in document
    assert 'rel="icon"' in document
    assert 'rel="manifest"' in document
    assert "Add to Home Screen" in document
