# Matchwell Pilot Implementation Plan

> **Status:** Ready for Validation

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

- [ ] All validation checks pass
  - [x] Ruff lint and formatting
  - [x] Strict mypy type checking
  - [x] Complete pytest suite with coverage threshold
  - [x] PostgreSQL upgrade and downgrade SQL generation
  - [x] Streamlit health endpoint smoke verification
  - [ ] GitHub Actions Docker image build

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
| Role reassignment Docker image | GitHub Actions `docker build .` | Pending | 2026-09-03 |

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
