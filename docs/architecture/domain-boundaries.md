# Domain Boundaries

## Boundary rules

Each module owns its model and writes. Modules may expose read contracts needed
for orchestration, but no module writes directly to another module's tables.
Cross-module state changes use explicit application services and reliable
outbox events.

| Module | Owns | Key outbound facts |
| --- | --- | --- |
| Identity and consent | User identity links, invitations, age verification state, consent versions and acceptance | User verified, consent accepted or superseded |
| Profiles and media | Member profile, preferences, private media references | Profile completed or changed |
| Assessments | Definitions, versions, assignments, responses, completion evidence | Assessment completed, revoked, or expired |
| Readiness stages | Journey definitions, requirements, evidence references, holds, decisions, unlocks | Stage or eligibility changed |
| Counselor operations | Counselor approval, assignments, availability, appointments, structured intake decisions | Counselor assigned, intake decision changed |
| Screening | Provider requests, callback receipts, normalized status summaries | Screening status changed |
| Centers | Centers, staff membership, Center policy references | Center membership changed |
| Communities | Community definitions, eligibility policy, membership | Community membership changed |
| Matching and introductions | Candidate filters, scores, explanations, counselor review, introductions, responses | Introduction approved, accepted, declined, or closed |
| Guided programs and check-ins | Curriculum templates, assignments, task completion, relationship stage, check-ins | Task or check-in completed, stage changed |
| Messaging and notifications | Conversation entitlement, messages, delivery requests and results | Message sent, notification delivery changed |
| Safety and moderation | Blocks, reports, safety cases, restrictions | Block or safety hold changed |
| Billing and entitlements | Customer mapping, subscription state, entitlements, counselor earnings ledger | Entitlement or ledger state changed |
| Audit and administration | Immutable audit events and role-scoped operational queue projections | Audit event appended |

## Data ownership

### Global records

- Users and identity links
- Blocks and reports
- Safety cases and restrictions
- Immutable audit events

### Center-owned records

- Center staff membership
- Counselor assignments and appointments
- Center-specific requirement configuration
- Community definitions and membership
- Center-scoped matching reviews

Every Center-owned record carries `center_id`. Global records may reference a
Center as context but are not owned by one.

## Critical interactions

### Readiness evaluation

The readiness module reads stable evidence contracts from consent, profile,
assessment, counselor, screening, billing, and guided-program modules. It does
not copy sensitive source data. The safety module supplies overriding hold
state.

### Matching

Matching considers only readiness-authorized candidates from the same eligible
Center and community. It stores rule contributions and safe explanation text,
not assessment answers or counseling notes. A counselor decision is required
before an introduction is offered.

### Safety

Blocks and safety restrictions synchronously affect authorization where member
interaction is attempted. A safety event also triggers asynchronous
reconciliation of eligibility, introductions, conversations, and guided
journeys.

### Billing

Billing owns commercial subscription state. Other modules consume entitlements,
not Stripe-specific objects. Counselor earnings ledger entries are immutable
adjustment records; automated payout remains deferred.

## Candidate extraction criteria

A module becomes an independent service only when measured scale, reliability,
security isolation, or independent deployment needs outweigh distributed-system
cost. Likely candidates are notifications, matching jobs, and messaging. Domain
ownership and event contracts remain unchanged after extraction.
