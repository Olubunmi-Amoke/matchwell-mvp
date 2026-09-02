# MVP Scope and Requirements

## Purpose

Matchwell's MVP validates a counselor-guided readiness-to-relationship journey,
not the entire product blueprint. The initial release is a closed pilot for
30-50 invited adult Christian members. After the pilot meets its success and
safety criteria, the product may expand to a one-region private beta for
150-300 members.

## MVP outcome

An invited member can:

1. Create and verify an adult Christian account.
2. Accept the current versions of required policies and consent.
3. Complete a profile and configured readiness assessments.
4. Complete counselor intake and receive a counselor decision.
5. Receive an eligibility status from a background-screening provider.
6. Unlock an eligible community when every requirement is satisfied.
7. Receive an explainable, counselor-approved introduction.
8. Mutually accept or decline the introduction.
9. Complete guided curriculum and 30-, 60-, and 90-day check-ins.
10. Block or report another member.

## P0 capabilities

| ID | Capability | Minimum acceptance criteria |
| --- | --- | --- |
| MW-PRD-001 | Identity and consent | Invited adults can authenticate, verify required account attributes, pass an age gate, and accept versioned consent. |
| MW-PRD-002 | Profile and preferences | Members can maintain the profile and partner preferences required for readiness and matching. |
| MW-PRD-003 | Configurable assessments | Authorized staff can configure assessment versions; members can complete the version assigned to them. |
| MW-PRD-004 | Counselor operations | Authorized staff can approve counselors, assign members, schedule intake, and record structured intake decisions. |
| MW-PRD-005 | Screening | A provider adapter submits eligible members, processes idempotent status updates, and stores only the minimum status summary. |
| MW-PRD-006 | Readiness stages | The API evaluates configured requirements, evidence, expiry, holds, and unlock decisions. |
| MW-PRD-007 | Centers and communities | The pilot supports one Center and one Matchwell community while preserving Center-scoped data boundaries. |
| MW-PRD-008 | Matching | Eligible members can be scored with explainable weighted rules and placed in a counselor review queue. |
| MW-PRD-009 | Introductions | A counselor-approved introduction is disclosed only as configured and becomes active only after mutual acceptance. |
| MW-PRD-010 | Messaging | Members in an eligible relationship context can exchange basic secure messages subject to block and safety rules. |
| MW-PRD-011 | Guided journey | Staff can configure curriculum; members can complete tasks and 30-, 60-, and 90-day check-ins. |
| MW-PRD-012 | Safety | Members can block or report another member; authorized staff can apply safety holds that override all progression. |
| MW-PRD-013 | Administration and audit | Role-scoped queues support operations; every privileged access and eligibility decision creates an immutable audit event. |
| MW-PRD-014 | Billing and entitlements | Subscription state controls entitlements, and counselor earnings are recorded in a ledger without automated payout. |

## Cross-cutting acceptance criteria

All P0 capabilities must satisfy these rules:

- The API is authoritative for authorization, eligibility, and state
  transitions. Interface visibility is not a security boundary.
- Center-owned records carry a `center_id`; users and safety records remain
  global.
- Privileged access, consent, requirement evaluation, eligibility, counselor
  decisions, and safety actions are auditable.
- Screening webhooks, payment webhooks, and background jobs are idempotent.
- Sensitive values are minimized in storage, logs, test fixtures, and events.
- Safety holds override every unlock, introduction, message entitlement, and
  relationship-stage transition.
- Requirement configuration and consent are versioned so historical decisions
  remain explainable.
- Accessibility and authorization behavior are testable acceptance criteria,
  not pilot-hardening afterthoughts.

## Deferred capabilities

The following are explicitly outside the MVP:

- Native mobile applications
- In-app video counseling
- Automated counselor marketplace discovery
- Fully automated counselor payouts
- Advanced event marketplace
- Cross-country pricing and localization
- Cross-Center matching
- Machine-learning matching
- AI counseling or relationship decisions
- Data warehouse and experimentation platform
- Enterprise single sign-on

Deferred features must not shape the first implementation into premature
services or generalized marketplaces. Extension points are appropriate only at
external provider boundaries and established domain seams.

## Release gates

### Closed pilot

- 30-50 invited users
- One region, one Center, and one community
- Human-operated counselor, screening, matching, and safety queues
- The first engineering milestone is complete and tested before matching or
  payments begin

### Private beta

- 150-300 users in the same region
- Pilot safety and operational findings have been addressed
- Authorization isolation, backup restoration, webhook failure handling,
  accessibility, monitoring, alerts, and security review have passed
- Expansion is approved by product, counseling operations, and safety owners

## Delivery policy

Engineering work should be organized as testable vertical slices. Each issue
must reference one or more requirement IDs, state its acceptance criteria, and
identify applicable authorization and audit expectations. Prompts and fixtures
must never contain production secrets, screening reports, counseling notes, or
real member information.
