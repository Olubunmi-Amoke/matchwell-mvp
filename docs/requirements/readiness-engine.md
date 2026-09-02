# Readiness Engine Requirements

## Requirement

MW-PRD-006 defines configurable readiness-stage evaluation. The readiness
engine is the authoritative mechanism for progression and unlock decisions.

## Model

A readiness journey consists of ordered stages. Each stage references one or
more versioned requirements. A requirement may be:

- Global
- Center-specific
- Segment-specific

The initial requirement types are:

- Current consent accepted
- Required profile fields complete
- Assessment complete and current
- Counselor decision approved and current
- Screening status eligible and current
- Subscription entitlement active
- Guided task or check-in complete

Requirement definitions contain no member data. Requirement evidence references
the authoritative domain record that satisfies the definition.

## Evaluation rules

1. Resolve the active journey and requirement configuration for the member's
   Center and segment.
2. Resolve authoritative evidence from the owning domains.
3. Treat missing, incomplete, failed, revoked, or expired evidence as unmet.
4. Apply safety and administrative holds after evaluating ordinary
   requirements; holds always win.
5. Persist an explainable decision and immutable audit event.
6. Publish an outbox event only when the effective eligibility or stage changes.

Evaluations must be deterministic for the same configuration versions, evidence
versions, and evaluation time.

## API behavior

Member-facing responses may include:

- Current stage
- Completion state
- Unmet requirement labels
- Safe next actions
- Eligibility state

Internal responses may additionally include evidence references and
configuration versions when the caller is authorized. Responses must not expose
assessment answers, counseling notes, screening reports, internal safety
details, or another member's data.

## Triggering re-evaluation

The engine re-evaluates when:

- Relevant evidence is created, updated, revoked, or expires
- A requirement or journey version becomes active
- A hold is applied or released
- Center or segment membership changes
- An authorized operator requests reconciliation

Scheduled reconciliation detects missed events and time-based expiry. It is a
correctness backstop, not the primary transition mechanism.

## Acceptance criteria

- Configuration changes do not rewrite historical decisions.
- Center-specific requirements cannot affect members of another Center.
- More-specific active configuration overrides only the documented parts of a
  broader configuration.
- Safety holds override every progression and unlock.
- Applying a hold or expiring evidence can revoke a prior unlock.
- Duplicate events and reconciliation runs do not produce duplicate state
  changes.
- Each decision is traceable to exact requirement, configuration, and evidence
  versions.
- Audit and operational logs exclude sensitive evidence content.
