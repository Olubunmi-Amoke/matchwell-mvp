# Accessibility Checklist

This is a release-gate checklist, not a report of manual testing that has
already happened. Every row below must be actually completed and signed
off before this milestone is marked "launch ready" in
[the pilot launch checklist](../security/pilot-launch-checklist.md); do
not check a row unless a specific person did the specific thing on a
specific date.

## What is automated today

`tests/test_accessibility.py` uses Streamlit's own `AppTest` harness to
render the admin dashboard, counselor workspace, member dashboard, and
member matching page against a real, seeded database, and asserts:

- Every `st.button`, `st.text_input`, `st.selectbox`, `st.checkbox`, and
  `st.number_input` has an explicit, non-empty label (Streamlit requires a
  `label` argument, so this catches an accidentally blank string, not a
  missing parameter).
- Every rendered page exposes at least one non-empty heading
  (`st.title`/`st.header`/`st.subheader`).
- Status communication (e.g. the readiness stage badge) carries real text
  content, not merely a background color, so a screen reader or a printed
  page still communicates status.
- No page raises an exception while rendering.

Running the current suite against the pilot's actual pages did not
surface a labeling violation to remediate; the existing consistent use of
explicit Streamlit widget labels and `render_badges`'s escaped-text badges
already satisfied these checks. This is a regression guard, not a
substitute for the manual pass below -- automated checks cannot evaluate
focus order, contrast, zoom/reflow, or real screen-reader output.

## Manual release-gate checklist

| # | Item | Status | Completed by | Date | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Keyboard-only: complete Google sign-in, accept consent, complete the assessment, save match preferences, and send a matched-pair message using only Tab/Shift+Tab/Enter/Space -- no mouse. | Not started | | | |
| 2 | Keyboard-only: as an administrator, invite a member, disable an account, and reactivate it using only the keyboard. | Not started | | | |
| 3 | Screen reader (NVDA or VoiceOver): navigate the member dashboard, matching page, and a guided-journey check-in form; confirm headings, labels, and status text are announced correctly. | Not started | | | |
| 4 | Screen reader: navigate the admin Dashboard tab (alerts + funnel analytics) and confirm suppressed ("Suppressed (<5)") safety/provider-failure values are announced as text, not silently skipped. | Not started | | | |
| 5 | Zoom/reflow: verify the member and admin pages remain usable at 200% browser zoom and at a narrow (mobile-width) viewport, with no horizontal scrolling required to reach primary actions. | Not started | | | |
| 6 | Contrast: verify the Matchwell theme's text/background and badge color pairs meet WCAG AA (4.5:1 for normal text, 3:1 for large text/UI components) using a contrast checker against `src/matchwell/presentation/theme.py`'s `_TONE_STYLES` and CSS variables. | Not started | | | |
| 7 | Errors: trigger a validation error (e.g. an invalid profile field, a disallowed role transition) and confirm the error text is specific, visible without color alone, and announced by a screen reader. | Not started | | | |
| 8 | Focus: confirm focus moves sensibly after a form submission (e.g. after "Disable account", focus does not silently vanish or jump to an unrelated element). | Not started | | | |
| 9 | Link/button text: spot-check that button labels describe their action out of context (e.g. "Disable account", not just "Submit") across the admin and member pages. | Not started | | | |

## Known accessibility limitations to track

- Streamlit's own generated markup (tabs, forms, dataframes) is outside
  this application's direct control; a component-level accessibility bug
  in Streamlit itself is a upstream dependency risk, not something this
  checklist can remediate directly. Record any such finding here with a
  link to the upstream Streamlit issue if one is filed.
- `st.dataframe` tables (used throughout the admin queues) rely on
  Streamlit's built-in grid component; verify during the manual pass
  whether it exposes adequate table semantics to a screen reader, and
  record the result even if it is a known limitation rather than a fix
  this repository can make.

## Sign-off

Do not mark this milestone accessibility-ready until every "Manual
release-gate checklist" row above has a real completed-by name and date.
A row is not truthfully complete just because the automated suite passes.
