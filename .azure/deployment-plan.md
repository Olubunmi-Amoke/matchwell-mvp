# Matchwell Pilot Implementation Plan

> **Status:** Validated

## Active Milestone: Guided Matched-Pair Journey

**Goal:** Give mutually accepted matched pairs a structured, counselor-guided
journey with curriculum tasks and 30/60/90-day check-ins while preserving
private member reflections and existing safety controls.

### Scope

- Either currently assigned counselor may assign one active version of the
  `Pilot Foundations` curriculum to an active matched pair. Assignment is
  recorded once per pair and becomes visible to both members and both currently
  assigned counselors.
- Seed a versioned pilot curriculum with six non-clinical activities:
  communication expectations, faith and values, healthy boundaries, conflict
  repair, family/community support, and relationship goals.
- Shared activities are visible to both members and complete only after each
  member records their own completion. Individual activities are visible only
  to the assigned member. Completion may be reversed by that same member, and
  counselors see only the latest completion state.
- Schedule private check-ins for each member at 30, 60, and 90 days from the
  counselor-assigned journey start date.
- Check-ins capture a structured relationship status, whether counselor support
  is requested, whether a concern exists, and an optional private reflection.
  The member may explicitly share that reflection with their own counselor.
- A counselor sees pair-level task progress plus due/overdue check-in status for
  both members. For the counselor's own assigned member, they also see support
  requests, concern status, and explicitly shared reflection text. A counselor
  never sees the other counselor's member's private reflection.
- Members see in-app `upcoming`, `due`, and `overdue` badges. External email,
  SMS, and push reminders remain deferred.
- Automatic access closure when the underlying match is no longer active.
- Safe audit and outbox events without private response content.

### Confirmed product decisions

- A counselor manually starts the journey; it is not assigned automatically.
- Check-in answers are private unless the member explicitly shares a reflection.
- Members may reverse their own task completion.
- This milestone provides in-app reminders only.

### Data and authorization design

- Add a dedicated guided-journey domain module; do not fold curriculum or
  check-in behavior into messaging.
- Add versioned curriculum template/task tables, one pair-journey assignment
  table, per-member task completion records, and per-member 30/60/90-day
  check-in records.
- Put `center_id` on pair-owned journey records and preserve the active proposal
  as the authorization root.
- Require `Role.MEMBER` for member task/check-in operations and
  `Role.COUNSELOR` for assignment and progress operations.
- Verify active proposal status and current counselor assignment on every read
  and mutation. Pair closure blocks further journey access while retaining
  historical records for audit and future controlled retention.
- Never return one member's private individual-task state to their partner.
- Never include private reflections in audit metadata, outbox payloads, logs,
  administrator views, or counselor results unless the member explicitly
  selected sharing with their own counselor.
- Emit metadata-only events for journey assignment, task-state changes,
  check-in submission, and support/concern signals.

### User experience

- Upgrade the active matched-pair workspace into clear `Journey`,
  `Conversation`, and `Safety` sections.
- Show curriculum progress, task ownership, completion controls, and the next
  check-in/reminder state in the member journey section.
- Add check-in forms with explicit privacy copy and a separate
  `Share reflection with my counselor` control.
- Expand counselor `Match activity` cards with an assignment action, pair
  progress, check-in due/overdue status, and privacy-safe attention flags.
- Keep administrators out of member reflection content.

### Delivery boundaries

- Continue using the existing Streamlit modular-monolith and PostgreSQL recipe.
- Add migration `20260904_0005` after matched-pair messaging migration
  `20260904_0004`.
- No Azure resources, external notification provider, billing change, or
  infrastructure generation is included.

### Planned validation

- Participant, counselor, role, and Center isolation.
- Active-match enforcement across every read and mutation.
- Different-counselor assignment and visibility end to end.
- Shared versus individual task visibility, per-member completion, reversal,
  and aggregate progress.
- Check-in scheduling, 30/60/90-day due-state calculation, submission
  validation, and in-app reminders.
- Partner privacy, cross-counselor privacy, explicit reflection sharing, and
  audit/outbox redaction.
- Block, report, hold, readiness-loss, counselor-reassignment, and
  role-reassignment access closure.
- Duplicate assignment and concurrent update safeguards.
- Migration upgrade/downgrade, Streamlit UI, lint, types, tests, and CI.

**Implementation status:** Complete.

**Validation status:** Validated: Ruff lint and formatting, strict mypy, 110
tests with 95.93% coverage, PostgreSQL offline upgrade and downgrade generation,
package build, Streamlit health smoke test, focused code review, and GitHub
Actions Python and container jobs all passed.

## Active Milestone: Secure Matched-Pair Messaging

**Goal:** Let mutually accepted matched members communicate safely inside their
active matched-pair workspace.

### Counselor model

- Matched members may have different counselors.
- Each member keeps their existing counselor assignment.
- Both assigned counselors must approve the candidate before introduction.
- Counselors can see messaging participation metadata, such as whether the
  conversation has started and the latest activity time, but cannot read
  private message content.

### Messaging behavior

- Enable messaging only while the proposal status is `active`.
- Permit only the two members in that matched pair to read or send messages.
- Store plain text only, with a 1,000-character maximum.
- Preserve chronological message history; MVP messages cannot be edited or
  deleted.
- Reject empty messages and normalize surrounding whitespace.
- Close message access immediately when a block, report, hold, readiness loss,
  role change, or other existing safety transition closes the matched pair.
- Do not allow messaging across Centers or between unrelated members.

### Safety and privacy

- Store message content only in the messaging boundary.
- Never include message content in audit events, outbox payloads, counselor
  queues, administrator queues, logs, or notifications.
- Audit message sends using message ID, pair ID, and sender ID only.
- Expose reporting through the existing structured safety workflow; no
  automated content interpretation or counseling decisions.
- Add pagination-ready retrieval with a bounded recent-message limit.

### User experience

- Add a conversation section to the active matched-pair member page.
- Show messages in chronological order with clear sender labels and timestamps.
- Add a compact send form with character guidance and safety reminder.
- Show a clear unavailable state when the pair is no longer active.
- Add counselor-visible conversation status without message bodies.

### Validation

- Test participant-only access, Center isolation, active-pair enforcement,
  ordering, limits, empty/oversized content, and message-content redaction from
  audit/outbox records.
- Test that blocks, reports, holds, readiness loss, and role changes prevent
  further reads and sends.
- Test different-counselor pairs end to end.
- Run Ruff, formatting, strict mypy, the complete pytest suite, PostgreSQL
  migration round-trip checks, Streamlit smoke verification, and CI container
  build.

**Implementation status:** Complete.

**Validation status:** Locally validated. Azure validation and deployment are not
applicable because this milestone adds no Azure infrastructure and the selected
delivery recipe remains the portable Docker application.

## Active Milestone: Candidate Generation Diagnostics

**Goal:** Explain why candidate generation produced no new pairs and give
administrators clear corrective actions.

### Diagnostic behavior

- Add an administrator-only diagnostic query that does not mutate matching
  records.
- Evaluate every member in the administrator's Center against the same
  readiness and matching rules used by candidate generation.
- Report member-level exclusions such as incomplete readiness, missing matching
  preferences, missing active counselor assignment, active proposal, or
  unavailable profile data.
- Evaluate otherwise available member pairs and report pair-level exclusions:
  incompatible gender rule, non-reciprocal age preferences, safety restriction,
  or prior proposal history.
- Show a clear ready-state when a pair is eligible for generation.

### User experience

- Add a **Candidate diagnostics** section beside candidate generation.
- Show summary counts for total members, individually ready members, evaluated
  pairs, and eligible pairs.
- Display member names with plain-language next actions.
- Display pair names with safe compatibility explanations.
- Refresh diagnostics after candidate generation so operators can distinguish
  newly created/open proposals from unresolved eligibility problems.

### Privacy and authorization

- Restrict diagnostics to administrators and the current Center.
- Do not expose assessment answers, counseling notes, screening details,
  safety-report context, exact birth dates, or private member responses.
- Use only operational status and corrective guidance already available to the
  administrator.
- Keep diagnostic reads audited with summary counts, not member details.

### Validation

- Test administrator-only access and Center isolation.
- Test missing preferences, readiness, counselor, open proposal, gender,
  reciprocal-age, safety restriction, prior-pair, and eligible-pair reasons.
- Verify diagnostic evaluation does not create or modify proposals.
- Run Ruff, formatting, strict mypy, the full pytest suite, PostgreSQL migration
  SQL checks, Streamlit smoke verification, and CI container build.

**Implementation status:** Complete.

**Validation status:** Ruff, formatting, strict mypy, 90 tests with 95.72%
coverage, PostgreSQL migration SQL generation, Streamlit smoke, read-path
safety review, and CI container validation passed.

## Active Milestone: Audited Counselor-to-Member Reassignment

**Goal:** Let a platform administrator convert an existing counselor account
into a member account without deleting identity, counseling history, or prior
member history.

### Supported transition

- Support `Counselor -> Member` only in this command.
- Keep the existing `Member -> Counselor` command unchanged.
- Require exact-email confirmation, an explicit impact acknowledgment, and a
  standardized operational reason code.

### Preconditions

- The actor must be an administrator in the counselor's Center.
- The target must currently be a counselor.
- Reject the transition while the counselor has active member assignments.
- Reject the transition while an open matching proposal still depends on the
  counselor's review.
- Administrators must reassign those responsibilities before changing the role.

### Transactional behavior

1. Lock the target account.
2. Recheck the target role, Center, active assignments, and open reviews.
3. Change the account role to member.
4. Preserve all historical counselor assignments, decisions, audits, profiles,
   consents, assessments, screenings, safety records, and matching records.
5. Expire any prior member screening eligibility so it cannot be silently
   reused.
6. Create a new incomplete readiness-assessment assignment.
7. Re-evaluate readiness so the account remains ineligible until the new member
   journey is complete.
8. Append safe `identity.role_reassigned` audit and outbox events containing
   only old/new roles and the standardized reason code.

### User experience

- Add a **Counselors** tab to Pilot operations.
- Show the current counselor roster and a selected-counselor role-management
  form.
- Explain responsibility prerequisites and retained history.
- Require the counselor's exact email and explicit acknowledgment.
- After success, remove the account from counselor controls, add it to the
  member readiness queue, and instruct the user to sign out and back in.

### Validation

- Cover administrator-only authorization, confirmation, reason validation,
  Center isolation, repeated requests, responsibility blocking, fresh
  assessment creation, screening reset, history preservation, queue movement,
  and safe audit/outbox payloads.
- Run Ruff, formatting, strict mypy, the complete pytest suite, PostgreSQL
  migration SQL checks, Streamlit smoke verification, and CI container build.

**Implementation status:** Complete.

**Validation status:** Ruff, formatting, strict mypy, 82 tests with 95.24%
coverage, PostgreSQL migration SQL generation, and Streamlit startup smoke
verification passed. CI container validation is pending.

## Active Milestone: Audited Member-to-Counselor Reassignment

**Goal:** Let a platform administrator convert an existing member account into a
counselor account without deleting identity or historical records.

### Supported transition

- Support `Member -> Counselor` only.
- Do not permit administrator reassignment, counselor demotion, self-service
  role changes, or multi-role accounts in this milestone.
- Require an explicit administrator confirmation and a short operational reason
  code.

### Transactional behavior

1. Verify the actor is an administrator and the target is a member in the same
   Center.
2. Lock the target account and related active workflow records.
3. End the target's active counselor assignment as a member.
4. Close pending, introduced, or active match proposals involving the target
   with reason `member_role_changed`.
5. Change the account role to counselor.
6. Preserve profile, consent, assessment, readiness, screening, hold, safety,
   matching, and prior assignment records as historical evidence.
7. Append an immutable `identity.role_reassigned` audit event containing only
   the old role, new role, and safe reason code.
8. Append a transactional `identity.role_reassigned` outbox event.

### User experience

- Add a clearly labeled **Role management** section to the administrator's
  member-readiness workspace.
- Explain that historical data is retained and active member workflows will
  close.
- Require the administrator to type the member's exact email and provide a
  reason code before completing reassignment.
- After success, remove the account from member queues and make it available in
  counselor assignment controls.
- Tell the reassigned user to sign out and back in to load the counselor
  workspace.

### Safety and authorization

- Never delete or rewrite historical audit, screening, readiness, counseling,
  safety, or matching records.
- Do not expose assessment answers, screening details, counseling notes, or
  report context in audit/outbox payloads.
- Reject stale, repeated, cross-Center, non-member, and non-administrator
  requests.
- Perform the entire transition atomically.

### Validation

- Add application authorization and input-validation tests.
- Add repository tests for successful transition, history preservation,
  assignment ending, proposal closure, audit/outbox creation, cross-Center
  isolation, and repeated-request rejection.
- Run Ruff, formatting, strict mypy, the complete pytest suite, migration checks,
  and Streamlit startup smoke verification.

**Implementation status:** Complete.

**Validation status:** Ruff, formatting, strict mypy, 76 tests, PostgreSQL
migration SQL generation, and Streamlit startup smoke verification passed.

## Active Milestone: Community Matching and Introductions

**Goal:** Carry community-eligible members from 7/7 readiness through an
explainable, counselor-reviewed candidate match and mutually accepted
introduction, while making every role workspace intuitive and visually aligned
with Matchwell.

**Requirements:** MW-PRD-002, MW-PRD-008, MW-PRD-009, MW-PRD-012, and
MW-PRD-013.

**Compatibility decision:** Pilot profiles identify members as `Man` or `Woman`.
Candidate generation permits only Man/Woman pairs. This is an explicit pilot
product rule and remains separate from identity-provider attributes.

### Member journey

1. Complete matching preferences, including acceptable age range.
2. Enter the candidate pool only after 7/7 readiness and without an active hold.
3. Receive a counselor-approved introduction with a safe profile summary and
   plain-language compatibility explanation.
4. Privately accept or decline without seeing the other member's response.
5. Enter an active matched-pair workspace only after mutual acceptance.
6. Block or report the introduced member at any point; either action immediately
   closes access and applies the appropriate safety restriction.

### Matching and operations

- Generate candidates only inside the same Center and community.
- Require reciprocal Man/Woman and age-range compatibility.
- Use deterministic weighted rules for age preference, location, denomination,
  and relationship-intent alignment.
- Persist rule contributions and safe explanations, never assessment answers,
  counselor notes, or screening details.
- Give counselors a prioritized review queue for their assigned members.
- Require counselor approval before either member sees an introduction.
- Prevent duplicate active proposals and introductions for the same pair.
- Reconcile open proposals and introductions when readiness, holds, blocks, or
  reports change.
- Audit candidate review, approval, member response, mutual activation, block,
  report, and closure transitions.

### Experience redesign

- Add a warm, calm Matchwell theme with consistent typography, color, cards,
  status badges, spacing, and responsive behavior.
- Replace raw enum labels and database-style values with human-readable copy.
- Show `x/7`, current stage, missing requirements, and next action in member,
  counselor, and administrator workspaces.
- Give administrators a summary dashboard and task-oriented member queue.
- Give counselors clear intake and matching-review sections with action status.
- Add empty states, success guidance, and progressive disclosure so controls
  appear in the order operators need them.

### Privacy and safety

- Do not disclose email, exact birth date, screening data, assessment answers,
  counselor notes, or direct contact information in introductions.
- Do not disclose one member's response until both have responded.
- Holds and blocks override candidate, introduction, and matched-pair access.
- Reports store structured categories and minimum operational context only.
- All mutations remain authorized in the application service and scoped by
  Center, counselor assignment, or participating member.

### Delivery

- Extend domain types, application ports, SQLAlchemy records, and Alembic
  migrations without coupling matching logic to Streamlit.
- Add deterministic matching and introduction services with immutable audit and
  transactional outbox transitions.
- Redesign Streamlit member and operations pages around the new journeys.
- Add unit, integration, authorization, idempotency, privacy, migration, and
  Streamlit smoke tests.

**Out of scope:** Free-form messaging, automated matching decisions, machine
learning, cross-Center matching, photos/media, contact-detail exchange,
subscriptions, payments, and guided curriculum.

**Implementation status:** Implemented. Match preferences (gender identity plus
acceptable partner age range) are stored in a new, opt-in
`member_match_preferences` table so existing hosted members never silently
become matching-eligible. Deterministic weighted candidate scoring, a
counselor-prioritized review queue requiring both assigned counselors'
approval, privacy-safe introductions with independent accept/decline, mutual
activation of a matched-pair workspace, duplicate-proposal prevention, and
member block/report with structured category and minimum context are all in
place. Holds, blocks, reports, and lost readiness reconcile and close open
proposals and introductions. Admin and counselor workspaces were redesigned
into task-oriented queues showing x/7 readiness, stage, and missing
requirements. A central Streamlit theme module provides warm, calm styling,
status badges, and empty/success states, with all user-derived content
rendered through native widgets or explicitly escaped HTML.

**Validation status:** Ruff, formatting, strict mypy, and 70 automated tests
(95.39% backend coverage) passed, including a full synthetic two-member
matching-to-activation journey, ineligible/missing-preference exclusion,
counselor authorization and Center isolation, mutual-response privacy,
single-active-proposal enforcement, pre-approval disclosure prevention,
duplicate-proposal idempotency, hold/block precedence, post-closure safety
access, and audit-payload redaction. Alembic offline `--sql` upgrade and
downgrade generation for migration `20260902_0003` passed against a PostgreSQL
dialect URL; a live PostgreSQL round trip is configured in CI. The Streamlit
runtime health and page endpoints were also verified locally.

## Active Milestone

**Goal:** Implement the first usable readiness journey from invited member
onboarding through community eligibility.

**Requirements:** MW-PRD-001 through MW-PRD-007 and MW-PRD-013, limited to the
first engineering milestone.

**Identity decision:** Google OIDC through Streamlit's native authentication.
Google proves identity; Matchwell remains authoritative for invitations, roles,
assignments, Center access, and eligibility.

### Member journey

1. Sign in with Google.
2. Match the verified email to an active, unused invitation.
3. Complete an adult age gate and Christian faith affirmation.
4. Accept the active consent version.
5. Complete readiness-required profile fields.
6. Complete the assigned, versioned readiness assessment.
7. See counselor intake and screening status.
8. Receive an explainable community eligibility result.

### Operations journey

- Bootstrap platform administrators from `MATCHWELL_ADMIN_EMAILS`.
- Create single-use, expiring member and counselor invitations.
- Assign counselors to members within the pilot Center.
- Record structured counselor intake decisions.
- Record provider-neutral screening status events idempotently.
- Apply and release safety or administrative holds.
- Review member progress without exposing assessment answers in queues or audit
  events.

### Application structure

- Use Streamlit's dynamic `st.navigation` for member and operations workspaces.
- Keep OIDC, Streamlit widgets, and SQLAlchemy outside the domain model.
- Add repository ports and transactional SQLAlchemy adapters.
- Add a deterministic readiness evaluator with hold precedence and
  human-readable unmet requirements.
- Append immutable audit events for consent, assignments, decisions, screening,
  holds, and eligibility changes.
- Append outbox events only when effective eligibility changes.
- Extend Alembic with versioned pilot schema and seed configuration.
- Permit explicitly enabled startup migration for Streamlit Community Cloud,
  where no release shell is available.

### Data minimization

- Store only normalized screening states, provider references, reason codes, and
  event identifiers; never screening reports.
- Store assessment responses only in the assessment boundary and never include
  them in progress queues, logs, or audit metadata.
- Keep counselor notes out of the milestone; store structured decisions only.
- Use opaque OIDC subject identifiers and normalized invited email addresses.

### Test plan

- Unit-test age calculation and deterministic readiness evaluation.
- Integration-test the complete synthetic member journey against PostgreSQL.
- Test invitation, role, assignment, and Center authorization boundaries.
- Test duplicate screening events and eligibility-change outbox idempotency.
- Test audit payloads for prohibited sensitive values.
- Exercise member, counselor, and administrator navigation with Streamlit
  application tests.

**Out of scope:** Matching, introductions, messaging, payments, real screening
provider connectivity, counselor notes, and automated decisions.

**Implementation status:** Complete.

**Validation status:** Ruff, formatting, strict mypy, 29 automated tests,
95.20% backend coverage, Alembic offline SQL generation, and Streamlit HTTP
startup verification passed. PostgreSQL migration round-trip validation is
configured in CI.

Generated: 2026-09-02T10:03:16-05:00

---

## 1. Project Overview

**Goal:** Create a portable Streamlit foundation for the Matchwell closed
pilot. The application must run in Docker on developer machines, Hugging Face
Spaces, and Replit while preserving clean seams for a later Next.js and ASP.NET
Core implementation.

**Path:** New Project

**Current phase:** Application foundation only. No Azure deployment is included
in this phase.

---

## 2. Requirements

| Attribute | Value |
|-----------|-------|
| Classification | Production-facing closed pilot foundation |
| Scale | Small: 30-300 invited users |
| Budget | Balanced with low-cost pilot services |
| Data residency | US-hosted services when external persistence is configured |
| Hosting | Dockerized Streamlit on Hugging Face Spaces or Replit |
| Persistence | PostgreSQL through `DATABASE_URL`; Docker Compose for local development |
| Azure subscription | Not applicable; this phase provisions no Azure resources |
| Azure location | Not applicable; this phase provisions no Azure resources |

### Security baseline

- No production secrets or member data in source, fixtures, logs, or prompts.
- Configuration is supplied through environment variables.
- The initial scaffold contains no simulated authentication that could be
  mistaken for production authorization.
- Health responses and logs contain no secrets or connection strings.
- Synthetic fixtures are used for tests.

---

## 3. Components

| Component | Type | Technology | Path |
|-----------|------|------------|------|
| Matchwell UI | Web application | Python 3.12, Streamlit | `app/` |
| Application services | Use-case boundary | Python | `src/matchwell/application/` |
| Domain | Business model boundary | Python | `src/matchwell/domain/` |
| Persistence | Infrastructure adapter | SQLAlchemy, PostgreSQL | `src/matchwell/infrastructure/` |
| Database migrations | Schema management | Alembic | `migrations/` |
| Tests | Automated verification | pytest | `tests/` |
| CI | Build and test | GitHub Actions | `.github/workflows/` |

No specialized Copilot SDK, Azure Functions, or existing application framework
was detected.

---

## 4. Delivery Recipe

**Selected:** Portable Docker application

**Rationale:**

- A single Docker contract works on Hugging Face Docker Spaces, Replit, and
  developer machines.
- Streamlit provides the fastest collaborative prototype surface.
- PostgreSQL preserves a relational model compatible with the target product.
- Domain and application layers do not import Streamlit or SQLAlchemy, reducing
  migration cost when Next.js and ASP.NET Core replace the prototype stack.
- Azure Developer CLI and Bicep are deferred until Azure hosting is requested.

---

## 5. Architecture

```text
Streamlit UI
    |
Application services
    |
Domain model and ports
    |
SQLAlchemy adapters
    |
PostgreSQL
```

Streamlit owns presentation and session state only. Business rules live in the
domain layer, orchestration lives in application services, and database details
live behind repository interfaces. Future Next.js and ASP.NET Core services can
replace the outer layers without changing the documented requirement IDs or
domain language.

### Runtime mapping

| Component | Current runtime | Future target |
|-----------|-----------------|---------------|
| Web UI | Streamlit container on port 7860 | Next.js responsive PWA |
| Application API | In-process application services | ASP.NET Core REST API |
| Worker | Not generated in this foundation | Hosted worker with Service Bus |
| Database | PostgreSQL via `DATABASE_URL` | Azure Database for PostgreSQL |
| Secrets | Platform environment variables | Azure Key Vault and managed identity |
| Monitoring | Structured standard output | Application Insights and Azure Monitor |

---

## 6. Provisioning Limit Checklist

This phase creates no Azure resources, so Azure quota and capacity validation
are not applicable. Hosting-account quotas for Hugging Face Spaces or Replit
remain an operator concern and are not provisioned by repository automation.

| Resource Type | Number to Deploy | Quota Validation |
|---------------|------------------|------------------|
| Azure resources | 0 | Not applicable |

**Status:** No cloud resources are provisioned by this plan.

---

## 7. Execution Checklist

### Phase 1: Planning

- [x] Analyze workspace
- [x] Gather classification, scale, budget, compliance, and hosting requirements
- [x] Scan codebase and specialized technology markers
- [x] Select portable Docker recipe
- [x] Plan migration-friendly architecture
- [x] Confirm PostgreSQL persistence approach
- [x] User approved this plan

### Phase 2: Application Foundation

- [x] Scaffold Python package and Streamlit application
- [x] Add typed environment configuration
- [x] Add PostgreSQL connection and health adapter
- [x] Add Alembic migration baseline
- [x] Add Dockerfile and local Docker Compose stack
- [x] Add Hugging Face Spaces and Replit-compatible runtime configuration
- [x] Add unit and smoke tests
- [x] Add GitHub Actions continuous integration
- [x] Document local and hosted development workflows

### Phase 3: Verification

- [x] Install declared dependencies
- [x] Run formatting and static analysis
- [x] Run automated tests
- [x] Build the Docker image in GitHub Actions
- [x] Start the application and verify the health surface
- [x] Record validation proof below

### Audited role reassignment validation

- [x] All validation checks pass
  - [x] Ruff lint and formatting
  - [x] Strict mypy type checking
  - [x] Complete pytest suite with coverage threshold
  - [x] PostgreSQL upgrade and downgrade SQL generation
  - [x] Streamlit health endpoint smoke verification
  - [x] GitHub Actions Docker image build

### Counselor-to-member validation

- [x] All validation checks pass
  - [x] Ruff lint and formatting
  - [x] Strict mypy type checking
  - [x] Complete pytest suite with coverage threshold
  - [x] PostgreSQL upgrade and downgrade SQL generation
  - [x] Streamlit health endpoint smoke verification
  - [x] GitHub Actions Docker image build

### Candidate diagnostics validation

- [x] All validation checks pass
  - [x] Ruff lint and formatting
  - [x] Strict mypy type checking
  - [x] Complete pytest suite with coverage threshold
  - [x] PostgreSQL upgrade and downgrade SQL generation
  - [x] Streamlit health endpoint smoke verification
  - [x] GitHub Actions Docker image build

### Guided matched-pair journey validation

- [x] All validation checks pass
  - [x] Ruff lint and formatting
  - [x] Strict mypy type checking
  - [x] Complete pytest suite with coverage threshold
  - [x] PostgreSQL upgrade and downgrade SQL generation
  - [x] Python source distribution and wheel build
  - [x] Streamlit health endpoint smoke verification
  - [x] Focused privacy, authorization, and concurrency review
  - [x] GitHub Actions Docker image build

### Phase 4: Future Azure Preparation

- [ ] Confirm Azure subscription and US region
- [ ] Select Azure hosting services and SKUs
- [ ] Validate Azure quotas
- [ ] Generate Bicep and `azure.yaml`
- [ ] Invoke `azure-validate`
- [ ] Invoke `azure-deploy` only after explicit deployment approval

---

## 8. Validation Proof

| Check | Command | Result | Timestamp |
|-------|---------|--------|-----------|
| Dependency resolution | `uv sync --frozen --extra dev` | Pass | 2026-09-02T12:35:31-05:00 |
| Lint | `uv run --no-sync ruff check .` | Pass | 2026-09-02T12:35:31-05:00 |
| Formatting | `uv run --no-sync ruff format --check .` | Pass | 2026-09-02T12:35:31-05:00 |
| Type check | `uv run --no-sync mypy` | Pass | 2026-09-02T12:35:31-05:00 |
| Tests | `uv run --no-sync pytest` | 10 passed, 100% coverage | 2026-09-02T12:35:31-05:00 |
| Migration generation | `alembic upgrade head --sql` | Pass | 2026-09-02T12:35:31-05:00 |
| Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-02T12:35:31-05:00 |
| Docker image | GitHub Actions `docker build .` | Pass | 2026-09-02T12:37:20-05:00 |
| CI workflow | GitHub Actions `python` and `container` jobs | Pass | 2026-09-02T12:37:20-05:00 |
| Role reassignment lint | `uv run ruff check .` | Pass | 2026-09-03 |
| Role reassignment formatting | `uv run ruff format --check .` | Pass | 2026-09-03 |
| Role reassignment types | `uv run mypy` | Pass | 2026-09-03 |
| Role reassignment tests | `uv run pytest -q` | 76 passed, 95.29% coverage | 2026-09-03 |
| PostgreSQL migration SQL | `alembic upgrade head --sql`; `alembic downgrade head:base --sql` | Pass | 2026-09-03 |
| Role reassignment Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-03 |
| Role reassignment Docker image | GitHub Actions `docker build .` | Pass | 2026-09-03 |
| Counselor-to-member lint | `uv run ruff check .` | Pass | 2026-09-03 |
| Counselor-to-member formatting | `uv run ruff format --check .` | Pass | 2026-09-03 |
| Counselor-to-member types | `uv run mypy` | Pass | 2026-09-03 |
| Counselor-to-member tests | `uv run pytest -q` | 82 passed, 95.24% coverage | 2026-09-03 |
| Counselor-to-member migration SQL | `alembic upgrade head --sql`; `alembic downgrade head:base --sql` | Pass | 2026-09-03 |
| Counselor-to-member Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-03 |
| Counselor-to-member Docker image | GitHub Actions `docker build .` | Pass | 2026-09-03 |
| Candidate diagnostics lint | `uv run ruff check .` | Pass | 2026-09-04 |
| Candidate diagnostics formatting | `uv run ruff format --check .` | Pass | 2026-09-04 |
| Candidate diagnostics types | `uv run mypy` | Pass | 2026-09-04 |
| Candidate diagnostics tests | `uv run pytest -q` | 90 passed, 95.72% coverage | 2026-09-04 |
| Candidate diagnostics migration SQL | `alembic upgrade head --sql`; `alembic downgrade head:base --sql` | Pass | 2026-09-04 |
| Candidate diagnostics Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-04 |
| Candidate diagnostics Docker image | GitHub Actions `docker build .` | Pass | 2026-09-04 |
| Secure messaging lint | `uv run --no-sync ruff check .` | Pass | 2026-09-04T14:23:58-05:00 |
| Secure messaging formatting | `uv run --no-sync ruff format --check .` | Pass | 2026-09-04T14:23:58-05:00 |
| Secure messaging types | `uv run --no-sync mypy` | Pass | 2026-09-04T14:23:58-05:00 |
| Secure messaging tests | `uv run --no-sync pytest -q` | 100 passed, 95.92% coverage | 2026-09-04T14:23:58-05:00 |
| Secure messaging migration SQL | `alembic upgrade head --sql`; `alembic downgrade head:base --sql` | Pass | 2026-09-04T14:23:58-05:00 |
| Secure messaging Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-04T14:23:58-05:00 |
| Secure messaging CI | GitHub Actions `python` and `container` jobs | Pass | 2026-09-04 |
| Guided journey lint | `uv run --no-sync ruff check .` | Pass | 2026-09-04 |
| Guided journey formatting | `uv run --no-sync ruff format --check .` | Pass | 2026-09-04 |
| Guided journey types | `uv run --no-sync mypy` | Pass | 2026-09-04 |
| Guided journey tests | `uv run --no-sync pytest` | 110 passed, 95.93% coverage | 2026-09-04 |
| Guided journey migration SQL | `alembic upgrade head --sql`; `alembic downgrade head:base --sql` | Pass | 2026-09-04 |
| Guided journey package build | `uv build --out-dir <session-artifacts>` | Source distribution and wheel built | 2026-09-04 |
| Guided journey Streamlit health | `GET /_stcore/health` | HTTP 200 `ok` | 2026-09-04 |
| Guided journey review | Focused diff review | No significant issues found | 2026-09-04 |
| Guided journey CI | GitHub Actions `python` and `container` jobs | Pass | 2026-09-04 |

### Functional verification

- Status: Verified
- Backend: PostgreSQL probe behavior and migration SQL tested
- UI: Streamlit smoke test and local HTTP response tested
- Notes: The application intentionally reports degraded readiness when
  `DATABASE_URL` is absent.

### Role assignment verification

- Status: Not applicable
- Identities checked: None; this phase contains no Azure infrastructure
- Roles confirmed: None required
- Issues: None

---

## 9. Files to Generate

| File or directory | Purpose | Status |
|-------------------|---------|--------|
| `.azure/deployment-plan.md` | Implementation and future deployment plan | Complete |
| `pyproject.toml` and `uv.lock` | Python dependencies and tool configuration | Complete |
| `app/` | Streamlit presentation layer | Complete |
| `src/matchwell/` | Domain, application, and infrastructure layers | Complete |
| `migrations/` | PostgreSQL schema migrations | Complete |
| `tests/` | Unit and smoke tests | Complete |
| `Dockerfile` | Portable production image | Complete |
| `compose.yaml` | Local application and PostgreSQL stack | Complete |
| `.github/workflows/ci.yml` | Continuous integration | Complete |

---

## 10. Next Step

The application foundation is validated. Azure provisioning remains deferred
until a subscription, region, and hosting architecture are approved.
