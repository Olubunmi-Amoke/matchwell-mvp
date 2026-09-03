"""Central Matchwell visual system: theme, CSS, and safe UI primitives.

Presentation modules should use these helpers instead of ad hoc HTML so the
warm, calm Matchwell look stays consistent and user-derived content is never
interpolated into raw HTML without escaping.
"""

import html
from collections.abc import Sequence
from typing import Literal

import streamlit as st

Tone = Literal["success", "info", "warning", "danger", "neutral"]

_TONE_STYLES: dict[Tone, tuple[str, str]] = {
    "success": ("#eaf6ef", "#1f7a4d"),
    "info": ("#eaf1fb", "#2a5c9a"),
    "warning": ("#fdf3e0", "#9a6a1f"),
    "danger": ("#fbeaea", "#a3312c"),
    "neutral": ("#f2f1ee", "#5a5750"),
}

_CSS = """
<style>
:root {
    --mw-bg: #fbf8f3;
    --mw-ink: #33302b;
    --mw-muted: #786f63;
    --mw-accent: #b6784f;
    --mw-border: #e7e0d4;
}
.stApp { background-color: var(--mw-bg); }
h1, h2, h3 { color: var(--mw-ink); }
.mw-muted { color: var(--mw-muted); font-size: 0.92rem; }
.mw-eyebrow {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    color: var(--mw-accent);
    font-weight: 700;
    margin-bottom: 0.1rem;
}
.mw-badge-row { margin: 0.35rem 0 0.6rem 0; }
.mw-badge {
    display: inline-block;
    padding: 0.18rem 0.7rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    margin-right: 0.4rem;
    margin-bottom: 0.25rem;
}
</style>
"""


def inject_theme() -> None:
    """Inject the shared Matchwell CSS once per page render.

    The stylesheet is static application chrome only; it never contains
    member-provided or otherwise user-derived text.
    """
    st.markdown(_CSS, unsafe_allow_html=True)


def humanize(value: str) -> str:
    """Turn an enum or database-style value into human-readable copy."""
    return value.replace("_", " ").title()


def render_eyebrow(label: str) -> None:
    """Render a small uppercase section label above a title.

    ``label`` must be static application copy, never user-derived text.
    """
    st.markdown(
        f'<p class="mw-eyebrow">{html.escape(label)}</p>', unsafe_allow_html=True
    )


def render_badges(items: Sequence[tuple[str, Tone]]) -> None:
    """Render a row of status badges.

    Labels are escaped before being embedded in HTML so this remains safe even
    if a caller ever passes through operator- or member-influenced text.
    """
    spans = "".join(
        f'<span class="mw-badge" style="background:{background};color:{ink};">'
        f"{html.escape(label)}</span>"
        for label, (background, ink) in (
            (label, _TONE_STYLES[tone]) for label, tone in items
        )
    )
    st.markdown(f'<div class="mw-badge-row">{spans}</div>', unsafe_allow_html=True)


def render_empty_state(message: str, icon: str = "🌱") -> None:
    """Render a calm empty state using a native, auto-escaping widget."""
    st.info(f"{icon} {message}")


def render_success_state(message: str, icon: str = "🎉") -> None:
    """Render a calm success state using a native, auto-escaping widget."""
    st.success(f"{icon} {message}")
