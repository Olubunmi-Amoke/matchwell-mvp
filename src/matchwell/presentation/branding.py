"""Matchwell logo, favicon, and mobile home-screen branding."""

from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOGO_PATH = PROJECT_ROOT / "assets" / "matchwell-logo.jpg"
APP_ICON_PATH = PROJECT_ROOT / "app" / "static" / "matchwell-icon-512.png"

_PWA_METADATA = """
<script>
const documentHead = window.parent.document.head;

function upsertLink(rel, href, sizes = null) {
    let element = documentHead.querySelector(`link[rel="${rel}"]`);
    if (!element) {
        element = window.parent.document.createElement("link");
        element.rel = rel;
        documentHead.appendChild(element);
    }
    element.href = href;
    if (sizes) {
        element.sizes = sizes;
    }
}

function upsertMeta(name, content) {
    let element = documentHead.querySelector(`meta[name="${name}"]`);
    if (!element) {
        element = window.parent.document.createElement("meta");
        element.name = name;
        documentHead.appendChild(element);
    }
    element.content = content;
}

const staticRoot = `${window.parent.location.origin}/app/static`;
upsertLink("apple-touch-icon", `${staticRoot}/matchwell-icon-180.png`, "180x180");
upsertLink("manifest", `${staticRoot}/manifest.webmanifest`);
upsertMeta("apple-mobile-web-app-capable", "yes");
upsertMeta("apple-mobile-web-app-status-bar-style", "default");
upsertMeta("apple-mobile-web-app-title", "Matchwell");
upsertMeta("mobile-web-app-capable", "yes");
upsertMeta("theme-color", "#fffdf8");
</script>
"""


def render_app_branding() -> None:
    """Render the shared logo and install mobile app metadata."""
    st.logo(LOGO_PATH, size="large", icon_image=APP_ICON_PATH)
    st.html(_PWA_METADATA, width="content", unsafe_allow_javascript=True)
