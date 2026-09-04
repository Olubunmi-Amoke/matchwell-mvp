"""Matchwell logo and browser branding."""

from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOGO_PATH = PROJECT_ROOT / "assets" / "matchwell-logo.jpg"
APP_ICON_PATH = PROJECT_ROOT / "assets" / "matchwell-icon.png"


def render_app_branding() -> None:
    """Render the shared Matchwell logo."""
    st.logo(LOGO_PATH, size="large", icon_image=APP_ICON_PATH)
