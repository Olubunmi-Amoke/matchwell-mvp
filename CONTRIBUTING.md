# Contributing to Matchwell

Matchwell is currently a proprietary, closed-pilot product. Contributions are
accepted only when authorized by the repository owner. By submitting a
contribution, you confirm that you have the right to provide it and agree that
it may be incorporated into this proprietary project.

## Before starting

1. Review the [Code of Conduct](CODE_OF_CONDUCT.md).
2. Search existing issues and pull requests to avoid duplicate work.
3. Open or obtain an approved issue for material changes.
4. Never include production data, credentials, member information, counselor
   notes, assessment answers, screening details, payment data, or private
   communications in an issue, commit, test fixture, screenshot, or prompt.
5. Report vulnerabilities through [SECURITY.md](SECURITY.md), not a public
   issue.

## Development setup

Install Python 3.12 and `uv`, then run:

```powershell
uv sync --frozen --extra dev
Copy-Item .env.example .env
uv run alembic upgrade head
```

Use only synthetic local data. The checked-in Compose credentials are for
local development and must never be reused in a hosted environment.

## Change requirements

- Reference the relevant requirement or approved issue.
- Keep domain boundaries and API authorization authoritative.
- Preserve Center isolation, immutable audit history, idempotency, and data
  minimization.
- Add or update migrations for persistent model changes.
- Add tests for behavior, authorization, privacy, failure paths, and
  accessibility as applicable.
- Update directly related requirements, architecture, security, or runbook
  documentation.
- Do not weaken safety holds, blocks, reports, eligibility checks, or
  privileged-access auditing.

## Validation

Run the smallest relevant tests while developing, then complete:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

For migration changes, also generate PostgreSQL upgrade and downgrade SQL and
verify the migration against the supported database test path.

## Pull requests

- Keep each pull request focused and explain user-visible behavior.
- Describe security, privacy, migration, and rollback implications.
- Include the commands and results used for validation.
- Confirm that no secrets or sensitive data are present.
- Wait for all required checks and review before merging.

The repository owner retains final acceptance and licensing authority.
