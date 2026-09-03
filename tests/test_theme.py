from streamlit.testing.v1 import AppTest


def test_humanize_converts_enum_style_values_to_title_case() -> None:
    from matchwell.presentation.theme import humanize

    assert humanize("not_started") == "Not Started"
    assert humanize("pending_review") == "Pending Review"


def test_render_badges_escapes_html_special_characters() -> None:
    app = AppTest.from_string(
        """
import streamlit as st
from matchwell.presentation.theme import render_badges

render_badges([("<script>alert(1)</script>", "danger")])
"""
    ).run(timeout=10)

    assert not app.exception
    rendered = "".join(element.value for element in app.markdown)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_eyebrow_escapes_html_special_characters() -> None:
    app = AppTest.from_string(
        """
import streamlit as st
from matchwell.presentation.theme import render_eyebrow

render_eyebrow("<img src=x onerror=alert(1)>")
"""
    ).run(timeout=10)

    assert not app.exception
    rendered = "".join(element.value for element in app.markdown)
    assert "<img src=x" not in rendered
    assert "&lt;img" in rendered


def test_render_empty_and_success_states_use_native_widgets() -> None:
    app = AppTest.from_string(
        """
import streamlit as st
from matchwell.presentation.theme import render_empty_state, render_success_state

render_empty_state("Nothing to see yet.")
render_success_state("All done.")
"""
    ).run(timeout=10)

    assert not app.exception
    assert "Nothing to see yet." in app.info[0].value
    assert "All done." in app.success[0].value
