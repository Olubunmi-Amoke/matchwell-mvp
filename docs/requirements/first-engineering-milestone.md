# First Engineering Milestone

## Audited rematching policy

- There is no cooldown or minimum wait.
- Proposal, authorization, audit, block, and report history is never deleted or
  rewritten.
- A previously proposed pair remains excluded unless Member Operations creates
  a Center-scoped request with an allow-listed reason and both members'
  currently assigned, distinct counselors separately approve. Authorization
  records bind to the two assignment IDs; reassignment atomically revokes a
  pending or approved authorization, including away-and-back reassignment.
- Ordinary closures (member/counselor decline, entitlement lapse, or other
  benign operational closure) may be authorized.
- Any current or historical pair block/report or safety/readiness enforcement
  closure permanently prevents authorization and consumption.
- Existing open proposal/member protections remain. Candidate creation and
  authorization consumption are one transaction; failed generation does not
  consume authorization. A database-unique participant claim prevents a member
  from occupying either side of two open proposals concurrently and is released
  on every proposal closure.
- Members receive no rematch-request or approval detail before the normal
  introduction stage.

## Goal

Deliver one complete thin slice:

> Account creation -> consent -> faith/community covenant -> assessment ->
> counselor decision -> screening status -> community eligibility

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
4. The member separately affirms the exact required keys of the current global
   faith/community covenant.
5. The member completes required profile fields and the assigned assessment.
6. A counselor is assigned and records a structured intake decision.
7. The screening adapter creates a provider request without exposing the
   provider report to Matchwell.
8. An idempotent provider callback updates the screening status summary.
9. The readiness engine evaluates all applicable requirements.
10. If every requirement is satisfied and no hold applies, the API grants
   community eligibility.
11. Every decision and privileged action is appended to the audit log.

## Acceptance criteria

### Account and consent

- Only invited users can enter the pilot journey.
- A user below the configured adult age cannot progress.
- Required consent records include policy type, version, acceptance timestamp,
  and member identity.
- A newly required consent version blocks progression until accepted.
- Each consent version defines stable required acknowledgement keys. Acceptance
  must contain exactly the current required set; UI checkboxes are not the
  authoritative enforcement boundary.
- The active pilot document is clearly marked **DRAFT — pending legal review**
  and does not claim legal approval.

### Profile denomination

- Members choose a curated denomination code. `Other` requires optional-display
  text in a dedicated field; legacy free text migrates to a recognized code or
  is preserved as `other` text.
- Matching compares codes and awards no denomination points for `other` or
  `prefer_not_to_say`.

### Faith and community covenant

- Covenant definitions are global, versioned, immutable, and deterministic,
  with exactly one active revision enforced by repository validation and a
  database partial unique index.
- Acceptance requires the exact current affirmation-key set at the application
  boundary. Stale, partial, or additional keys fail; exact retries are
  idempotent and no free text is accepted.
- Activating a new revision makes all existing members unmet until they
  re-affirm it. Historical acceptances remain unchanged.
- Operator views expose only whether the current covenant requirement is
  missing. Audit contains policy/version and key names, never labels or body.
- This replaces the proposed LGBT-friendliness eligibility criterion. Matchwell
  does not collect or infer sexual orientation, attitudes toward LGBT people,
  or proxies. Covenant commitments do not authorize mistreatment.

### Complimentary introductory 1:1 session

- Every member has at most one dedicated, auditable benefit with the lifecycle
  `available -> scheduled -> completed` or `scheduled -> cancelled`.
- A cancelled benefit can be rescheduled; completion cannot mint or reopen a
  benefit.
- Member Operations schedules, reschedules, and cancels within its Center.
  Only the active assigned counselor can complete the session.
- Audit and outbox metadata is constrained and excludes private notes and
  unnecessary scheduling detail.

### Operations ownership

- The existing administrator workspace is labeled **Member Operations**.
- Member Operations, Counselor Operations, and Trust & Safety responsibilities
  are distinct. This naming introduces no HR role and grants no new broad
  permission.

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

- Consent, covenant acceptance, counselor decisions, screening status transitions, requirement
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
