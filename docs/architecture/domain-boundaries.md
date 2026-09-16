# Domain Boundaries

## Boundary rules

Each module owns its model and writes. Modules may expose read contracts needed
for orchestration, but no module writes directly to another module's tables.
Cross-module state changes use explicit application services and reliable
outbox events.

| Module | Owns | Key outbound facts |
| --- | --- | --- |
| Identity and consent | User identity links, invitations, age verification state, consent versions and acceptance | User verified, consent accepted or superseded |
| Faith and community covenant | Global immutable covenant definitions, structured affirmation keys, version-specific member acceptance | Current covenant accepted |
| Profiles and media | Member profile, preferences, private media references | Profile completed or changed |
| Assessments | Readiness definitions, versions, assignments, responses, completion evidence | Assessment completed, revoked, or expired |
| Personality reflection | Versioned public-domain IPIP definitions, assignments, sensitive responses and scores | Completion metadata only; raw values never leave this boundary |
| Readiness stages | Journey definitions, requirements, evidence references, holds, decisions, unlocks | Stage or eligibility changed |
| Counselor operations | Counselor approval, assignments, availability, appointments, structured intake decisions | Counselor assigned, intake decision changed |
| Screening | Provider requests, callback receipts, normalized status summaries | Screening status changed |
| Centers | Centers, staff membership, Center policy references | Center membership changed |
| Communities | Community definitions, constrained matching mode, explicit current assignment history | Community assignment changed |
| Matching and introductions | Candidate filters, scores, explanations, counselor review, minimized self-paced suggestions/interests, introductions, responses and participant claims | Introduction approved, reciprocal interest activated, accepted, declined, or closed |
| Guided programs and check-ins | Curriculum templates, assignments, task completion, relationship stage, check-ins | Task or check-in completed, stage changed |
| Messaging and notifications | Conversation entitlement, messages, delivery requests and results | Message sent, notification delivery changed |
| Safety and moderation | Blocks, reports, safety cases, restrictions | Block or safety hold changed |
| Billing and entitlements | Customer mapping, subscription state, entitlements, counselor earnings ledger | Entitlement or ledger state changed |
| Audit and administration | Immutable audit events and role-scoped operational queue projections | Audit event appended |

## Data ownership

### Global records

- Users and identity links
- Faith and community covenant definitions and version-specific acceptances
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
the global covenant, assessment, counselor, screening, billing, and
guided-program modules. It does
not copy sensitive source data. The safety module supplies overriding hold
state.

### Matching

Matching considers only readiness-authorized candidates from the same Center
and current community. Counselor-based proposals require counselor review.
Self-paced suggestions disclose a minimized projection and reciprocal interest
activates the normal proposal directly. Both modes use identical eligibility,
reciprocal gender/age rules, deterministic score/rank, safety history,
proposal-history/rematch, and participant-claim controls. Personality adds
neutral explanation text only; it never changes the score/order. Matching
stores no assessment answers, personality answers/scores, or counseling notes.

Eligibility and matching do not collect, infer, display, audit, or use sexual
orientation, attitudes toward LGBT people, or proxy attributes. The Christian
covenant and reciprocal Man/Woman matching scope do not authorize protected or
sensitive attitude screening.

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
