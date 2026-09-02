# Authorization and Data Handling

## Authorization principles

- Authentication establishes identity; it does not grant resource access.
- Every API command and query authorizes role, action, subject, and Center
  context.
- Deny is the default when assignment, Center, entitlement, or relationship
  context is missing.
- Role-based access is narrowed by resource relationships. A counselor role
  alone does not grant access to every member.
- Safety restrictions override ordinary access and progression rules.
- Background jobs and provider callbacks use dedicated identities and the
  minimum application permissions required.

## Initial access boundaries

| Actor | Allowed scope |
| --- | --- |
| Member | Own readiness data and authorized relationship workspaces |
| Counselor | Assigned members and counselor-owned operational records |
| Counselor supervisor | Explicitly delegated oversight scope |
| Center administrator | Operational records for authorized Centers |
| Safety staff | Global safety records according to assigned privilege |
| Platform administrator | Explicit platform operations; no implicit access to sensitive content |
| Worker or provider callback | Named machine operation only |

Privileged interface routes must not return unauthorized records and rely on
client filtering. List endpoints apply the same resource constraints as detail
endpoints.

## Sensitive data rules

| Data | Handling |
| --- | --- |
| Assessment answers | Store only in the assessments boundary; never log or include in general audit payloads |
| Counseling notes | Keep separate from structured readiness decisions; restrict to counseling purpose |
| Screening reports | Do not copy broadly into Matchwell; retain only provider reference and minimum normalized status summary |
| Identity evidence | Retain only required verification outcome and metadata |
| Messages | Encrypt in transit and at rest; exclude content from telemetry and general audit payloads |
| Files and media | Store in private Blob containers with short-lived, purpose-bound access |
| Secrets | Store in Key Vault; access through managed identity where supported |
| Test data | Use synthetic fixtures only |

## Audit requirements

Immutable events are required for:

- Consent acceptance and supersession
- Privileged access to sensitive records
- Counselor assignment and structured decisions
- Screening status transitions
- Requirement evaluations and eligibility changes
- Administrative and safety holds
- Matching approval and introduction state changes
- Role, permission, entitlement, and ledger changes

Each event includes actor, action, subject, timestamp, correlation ID, Center
context when applicable, and safe decision metadata. Audit records exclude
assessment answers, counseling notes, screening reports, message content,
secrets, authentication tokens, and unnecessary personal data.

## Required tests

- Role and resource authorization for every protected endpoint
- Cross-Center access attempts for list and detail operations
- Direct-object-reference attempts between members
- Revoked role, assignment, entitlement, and relationship access
- Safety hold and block precedence
- Machine identity permissions and callback authenticity
- Sensitive-value exclusion from logs, telemetry, events, API errors, and audit
  payloads
