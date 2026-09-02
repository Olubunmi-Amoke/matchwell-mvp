# ADR 0001: Use a Modular Monolith for the MVP

- Status: Accepted
- Date: 2026-09-02

## Context

The MVP must validate a connected readiness-to-relationship journey for a
30-50-member pilot. Its domains have distinct ownership, but the initial scale
does not justify the deployment, consistency, observability, and operational
cost of microservices.

## Decision

Build the backend as an ASP.NET Core modular monolith backed by PostgreSQL.
Domain modules deploy together but own their models and writes. Cross-module
work uses explicit contracts and an outbox for reliable events. Run background
work in an independently deployable hosted worker connected through Azure
Service Bus.

Use external adapters for screening, notifications, billing, identity, and file
storage so provider details do not leak into domain models.

## Consequences

### Positive

- One deployable API and one primary transaction boundary accelerate the pilot.
- Explicit modules preserve domain ownership and future extraction seams.
- The outbox prevents database changes and asynchronous events from diverging.
- Provider adapters keep external change localized.

### Trade-offs

- Module boundaries require review and tests because process isolation does not
  enforce them.
- Shared deployment couples release timing.
- Independently scaling one API module is not available until extraction.

## Extraction rule

Extract a module only when production evidence shows a need for independent
scale, reliability isolation, security isolation, or release cadence. Expected
early candidates are notifications, matching jobs, and messaging. Extraction
must preserve ownership, authorization, idempotency, and event contracts.
