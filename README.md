# Matchwell MVP

Scalable MVP for a counselor-guided Christian relationship platform focused on
readiness, trusted communities, intentional matching, and healthy relationship
progression.

The first release is a closed-pilot vertical slice for 30-50 invited members.
The full product blueprint remains the target product, not the initial release.

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
