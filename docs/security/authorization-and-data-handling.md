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
| Screening reports | Do not copy broadly into Matchwell; retain only provider reference and minimum normalized status summary; screening reason/status codes are drawn from a fixed safe allow-list, never free text |
| Payment data | Store only provider customer/subscription IDs, normalized status, dates, currency, and integer minor-unit amounts; never store card or bank details; Stripe secret keys and webhook secrets are environment secrets, never logged, audited, or placed in outbox payloads |
| Identity evidence | Retain only required verification outcome and metadata |
| Messages | Encrypt in transit (TLS); exclude content from telemetry and general audit payloads. See the encryption-at-rest note below -- the pilot does not add its own row-level encryption at rest |
| Files and media | Store in private Blob containers with short-lived, purpose-bound access (target architecture; the pilot stores no member-uploaded files) |
| Secrets | Store in Key Vault; access through managed identity where supported (target architecture). The pilot's actual secret handling is Streamlit `secrets.toml` / environment variables provided by the hosting platform -- see the pilot launch checklist for evidence requirements |
| Test data | Use synthetic fixtures only |

### Encryption-at-rest: application behavior vs. hosting evidence

This distinction is intentionally explicit because the two are easy to
conflate:

- **Application behavior today:** the Streamlit/FastAPI pilot does not
  perform its own field- or row-level encryption of stored data. It relies
  entirely on the encryption the selected PostgreSQL host provides for its
  underlying storage (and, if applicable, for automated backups of that
  storage).
- **What must be true for launch:** the pilot administrator must obtain and
  record the selected hosting/managed-Postgres provider's own evidence that
  its storage (and backups) are encrypted at rest. Do not state or imply
  that Matchwell encrypts data at rest itself; state only what the host
  provides, with a link to that provider's evidence. See
  [Backup and restore](../runbooks/backup-and-restore.md#encryption-evidence-host-responsibility)
  and the [pilot launch checklist](pilot-launch-checklist.md).
- **In transit:** TLS is required end-to-end (browser to Streamlit,
  Streamlit/webhook service to PostgreSQL via `sslmode=require`, and to
  Stripe). This is an application/deployment configuration requirement, not
  something this document overstates as already proven.


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
