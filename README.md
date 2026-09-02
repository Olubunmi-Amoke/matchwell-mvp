---
title: Matchwell MVP
emoji: "\U0001F91D"
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
---

# Matchwell MVP

Scalable MVP for a counselor-guided Christian relationship platform focused on
readiness, trusted communities, intentional matching, and healthy relationship
progression.

The first release is a closed-pilot vertical slice for 30-50 invited members.
The full product blueprint remains the target product, not the initial release.

## Application foundation

The current pilot is a Dockerized Streamlit application backed by PostgreSQL.
Its domain and application layers do not depend on Streamlit or SQLAlchemy so
the presentation and infrastructure can later move to Next.js and ASP.NET Core.

### Local development with uv

Install [uv](https://docs.astral.sh/uv/), start PostgreSQL, and configure the
application:

```powershell
Copy-Item .env.example .env
uv sync --extra dev
uv run alembic upgrade head
uv run streamlit run app\main.py --server.port 7860
```

Open `http://localhost:7860`.

### Local development with Docker

```powershell
docker compose up --build
docker compose exec app alembic upgrade head
```

The Compose stack exposes Streamlit on port 7860 and PostgreSQL on port 5432.
The checked-in password is for local development only; hosted environments must
set `DATABASE_URL` and secrets through platform settings.

### Hugging Face Spaces

Create a Docker Space and push this repository to it. The README metadata,
root `Dockerfile`, non-root user, and port 7860 follow the Docker Spaces
runtime contract. Configure `DATABASE_URL` as a Space secret because local
Space storage is ephemeral.

### Replit

Import the repository into Replit. The `.replit` file starts Streamlit on
`0.0.0.0:7860` and maps it to the public web port. Configure `DATABASE_URL`
through Replit Secrets.

### Quality checks

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Documentation

- [MVP scope and requirements](docs/requirements/mvp-scope.md)
- [First engineering milestone](docs/requirements/first-engineering-milestone.md)
- [Readiness engine requirements](docs/requirements/readiness-engine.md)
- [System architecture](docs/architecture/system-architecture.md)
- [Domain boundaries](docs/architecture/domain-boundaries.md)
- [Authorization and data handling](docs/security/authorization-and-data-handling.md)
- [ADR 0001: Modular monolith](docs/decisions/0001-modular-monolith.md)

## Intended repository layout

```text
apps/web/                  Next.js responsive PWA
services/api/              ASP.NET Core REST API
services/jobs/             Background workers
packages/ui/               Shared user-interface components
packages/api-client/       Generated TypeScript OpenAPI client
packages/test-fixtures/    Synthetic test data and builders
infrastructure/bicep/      Azure infrastructure as code
docs/                      Requirements, architecture, decisions, security, runbooks
.github/workflows/         Build, test, and deployment automation
```
