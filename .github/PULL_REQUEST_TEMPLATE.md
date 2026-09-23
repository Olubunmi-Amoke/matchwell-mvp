## Summary

<!-- What user or operator outcome does this change deliver? -->

## Requirement and scope

<!-- Link the approved issue/requirement. Note what is intentionally deferred. -->

## Security, privacy, and safety

- [ ] Authorization is enforced by the application/API, not only the UI.
- [ ] Center isolation and role boundaries are preserved.
- [ ] No secrets, production data, or sensitive member content are included.
- [ ] Audit/outbox payloads contain only privacy-safe metadata.
- [ ] Blocking, reporting, holds, and other safety controls remain authoritative.

## Data and migration impact

<!-- Describe schema/data changes, backfill behavior, rollback, and deployment order. Write "None" if not applicable. -->

## Accessibility

<!-- Describe keyboard, labels, focus, semantics, contrast, and reflow impact. -->

## Validation

<!-- List exact commands and results. -->

- [ ] Relevant tests pass.
- [ ] `uv run ruff format --check .` passes.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy` passes.
- [ ] `uv run pytest` passes with required coverage.
- [ ] Package and migration checks pass when applicable.

## Deployment and rollback

<!-- State operator steps, feature gating, monitoring, and rollback procedure. -->
