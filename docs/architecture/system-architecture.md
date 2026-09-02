# System Architecture

## Architecture summary

Matchwell begins as a responsive Next.js PWA backed by an ASP.NET Core modular
monolith and PostgreSQL. Background work is dispatched reliably through an
outbox and processed by a hosted worker using Azure Service Bus. External
systems are isolated behind provider adapters.

```text
Next.js responsive PWA
        |
ASP.NET Core REST API
        |
Azure Database for PostgreSQL
        |
Transactional outbox
        |
Azure Service Bus + hosted worker
        |
Screening, notifications, billing, and storage adapters
```

## Components

| Concern | MVP choice |
| --- | --- |
| User experience | One Next.js responsive application with role-based workspaces |
| Backend | ASP.NET Core REST API organized by domain module |
| Database | Azure Database for PostgreSQL |
| Identity | Microsoft Entra External ID |
| Background processing | Azure Service Bus and hosted worker |
| Files | Private Azure Blob Storage |
| Secrets and workload identity | Azure Key Vault and managed identities |
| Notifications | Azure Communication Services |
| Payments | Stripe Billing; defer Connect payout automation |
| Monitoring | Application Insights and Azure Monitor |
| Infrastructure | Bicep |
| Delivery | GitHub Actions with protected environments |

The TypeScript client in `packages/api-client` is generated from the API's
OpenAPI document. Hand-maintained duplicate request and response contracts are
not permitted.

## Deployment model

The API's domain modules deploy as one process for the MVP but retain explicit
module boundaries. Modules may share a PostgreSQL server while owning their
schemas and controlling writes to their tables. Cross-module behavior uses
application contracts or domain events rather than direct writes to another
module's data.

The worker is independently deployable so retries and provider latency do not
consume API request capacity. Notifications, matching jobs, or messaging should
be extracted into independent services only after measured load or operational
isolation justifies the cost.

## Reliability patterns

### Transactional outbox

State changes and resulting events are committed in one database transaction.
A dispatcher publishes pending outbox records to Service Bus. Consumers are
idempotent, and processed-message identifiers are retained according to the
operation's replay window.

### External webhooks

Screening and payment callbacks:

- Verify provider authenticity
- Persist a provider event identifier
- Return safely on an already-processed event
- Normalize provider states through an adapter
- Perform transitions asynchronously when processing may be slow
- Correlate resulting changes and audit events

### Failure handling

Retries are bounded and use backoff. Permanently failing work enters an
operator-visible queue with safe diagnostics. Failures must not be converted
into successful eligibility, entitlement, or payment states.

## Multi-tenancy

The MVP operates one Center but is tested with at least two synthetic Centers.
Center-owned records include `center_id`, and every Center-scoped query and
command is authorized against it. Users, identity links, blocks, reports,
safety cases, and immutable audit records are global so safety controls cannot
be bypassed by changing Centers.

Cross-Center matching is deferred. Any future implementation requires an
explicit policy and consent model rather than removing Center filters.

## Security and privacy

- Authorization is enforced at API commands, queries, and resource boundaries.
- Managed identities replace stored cloud credentials where supported.
- Blob containers are private; access is short-lived and purpose-bound.
- Sensitive fields are classified and excluded from logs, telemetry, events,
  and general-purpose exports.
- Screening data is reduced to an eligibility summary; broad reports are not
  copied into Matchwell.
- Privileged access and eligibility decisions are auditable.
- Production data and secrets are prohibited from coding prompts and test
  fixtures.

See
[Authorization and data handling](../security/authorization-and-data-handling.md)
for mandatory controls.

## Delivery sequence

1. Foundation: repository, CI/CD, environments, identity, authorization, audit,
   migrations, and feature flags.
2. Readiness: profiles, consent, assessments, stages, evidence, holds, and
   progress.
3. Trust operations: counselors, scheduling, intake, screening, membership,
   and review queues.
4. Matching: eligibility filtering, weighted scoring, explanations, review,
   introductions, blocks, reports, and holds.
5. Guided journey: curriculum, tasks, stages, check-ins, notifications, and
   entitlements.
6. Pilot hardening: isolation, authorization, restore, provider failures,
   accessibility, observability, security review, and pilot analytics.
