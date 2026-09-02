# First Engineering Milestone

## Goal

Deliver one complete thin slice:

> Account creation -> consent -> assessment -> counselor decision -> screening
> status -> community eligibility

Matching, introductions, messaging, subscriptions, and payments must not begin
until this slice is working end to end and its authorization and audit behavior
is tested.

## Included requirements

- MW-PRD-001: Identity and consent
- MW-PRD-002: Profile and preferences, limited to readiness-required fields
- MW-PRD-003: Configurable assessments
- MW-PRD-004: Counselor operations, limited to assignment and intake decision
- MW-PRD-005: Screening status
- MW-PRD-006: Readiness stages
- MW-PRD-007: One Center and one community
- MW-PRD-013: Administration and audit

## Journey

1. An administrator invites a member into the pilot.
2. The member authenticates through Microsoft Entra External ID.
3. The API verifies the age gate and records the versions of all required
   consent accepted by the member.
4. The member completes required profile fields and the assigned assessment.
5. A counselor is assigned and records a structured intake decision.
6. The screening adapter creates a provider request without exposing the
   provider report to Matchwell.
7. An idempotent provider callback updates the screening status summary.
8. The readiness engine evaluates all applicable requirements.
9. If every requirement is satisfied and no hold applies, the API grants
   community eligibility.
10. Every decision and privileged action is appended to the audit log.

## Acceptance criteria

### Account and consent

- Only invited users can enter the pilot journey.
- A user below the configured adult age cannot progress.
- Required consent records include policy type, version, acceptance timestamp,
  and member identity.
- A newly required consent version blocks progression until accepted.

### Assessment

- Assessment definitions and assignments are versioned.
- Only the assigned member can submit their answers.
- Completion evidence is available to the readiness engine without placing
  answers in logs or audit-event payloads.
- An incomplete or expired assessment blocks eligibility.

### Counselor decision

- Only an authorized counselor assigned to the member, or an explicitly
  authorized supervisor, can view the intake queue and record a decision.
- Decisions use structured outcomes needed by the readiness engine.
- Counseling notes are not included in audit events, notifications, or
  general-purpose member responses.
- A rejected, pending, or expired decision blocks eligibility.

### Screening

- The API talks to screening providers only through a provider-neutral adapter.
- Callback authenticity is verified before processing.
- Replaying a callback does not duplicate transitions, jobs, or audit events.
- Matchwell stores the provider reference, normalized status, relevant
  timestamps, and reason code only when necessary; it does not store a broad
  copy of the screening report.
- An adverse, pending, failed, or expired status does not unlock the community.

### Eligibility

- Requirements can be global, Center-specific, or segment-specific.
- The API returns the current stage, unmet requirements, and safe
  human-readable reasons.
- A safety or administrative hold overrides otherwise complete requirements.
- Re-evaluation revokes eligibility when evidence expires or a hold is applied.
- Every evaluation persists the configuration versions and evidence references
  that explain the result.

### Authorization and isolation

- A member cannot read or change another member's journey.
- A counselor can access only assigned members unless a separately authorized
  supervisory role applies.
- Center-scoped staff cannot access records belonging to another Center.
- Authorization tests cover direct-object-reference attempts and role changes.

### Audit

- Consent, counselor decisions, screening status transitions, requirement
  evaluations, holds, and eligibility changes create immutable audit events.
- Events identify actor, action, subject, timestamp, correlation ID, and safe
  decision metadata.
- Events never contain assessment answers, screening reports, counseling notes,
  secrets, or message content.

## Required test coverage

- Unit tests for requirement evaluation, precedence, expiry, and hold behavior
- Integration tests for the complete journey and outbox processing
- Authorization tests for member, counselor, supervisor, Center administrator,
  and system callback boundaries
- Contract tests for the screening adapter and callback normalization
- Idempotency tests for duplicate callbacks and retried background jobs
- Audit tests that verify event creation and prohibited-data exclusion
- Isolation tests using at least two synthetic Centers, despite the pilot
  operating with one

## Completion definition

The milestone is complete only when a synthetic invited member can traverse the
journey through public API contracts, eligibility is correctly granted and
revoked, all failure paths leave a safe and explainable state, and the required
tests pass in continuous integration.
