# Monitoring and Alerts Runbook

Matchwell's pilot has no hosted monitoring provider. Monitoring consists of
three things instead: structured JSON application logs, an administrator
operations dashboard (**Pilot operations → Dashboard**), and this
documented set of thresholds and actions. This runbook describes how to
read all three.

## Structured logs

Both the Streamlit app and the companion FastAPI webhook service configure
JSON logging on startup (`matchwell.infrastructure.observability.logging`).
Every log line is a single JSON object with `timestamp`, `level`, `logger`,
`event`, `correlation_id`, and an optional `fields` object.

- Event names are drawn from `OperationalEvent` -- an enumerated, closed
  set (`auth.sign_in_rejected`, `account.disabled`,
  `provider.billing_webhook_unapplied`, `health.database_unavailable`,
  etc.). There is no free-form event naming.
- Every field passed to a log call is checked against a redaction
  denylist (password, secret, token, cookie, message, reflection, note,
  answer, email, name, body, payload, card, bank, and more) before it is
  ever written. A call that tries to log a disallowed field raises
  immediately in tests rather than silently leaking in production.
- Generic infrastructure failures (`logger.exception(...)` in the database
  migration/connection handlers) still route through the same JSON
  formatter but do not include a traceback in the JSON payload, by design,
  to avoid ever accidentally serializing a sensitive value from an
  exception argument.
- Whatever platform hosts the Streamlit app / webhook service is
  responsible for capturing stdout/stderr into its own log retention. This
  pilot does not ship a log aggregator itself.

## Admin operations dashboard

Sign in as an administrator and open **Pilot operations → Dashboard**. It
has two sections:

1. **Alerts** -- the same metrics an external monitor would evaluate,
   computed live from the database and rendered with an OK/Warning/Critical
   badge per metric.
2. **Pilot funnel and safety analytics** -- Center-scoped aggregate counts
   from invitation through active guided journey, plus small-cell-suppressed
   safety and provider-failure counts. See
   [Authorization and data handling](../security/authorization-and-data-handling.md)
   for the privacy rules this dashboard follows.

## Alert metrics, thresholds, and actions

| Metric | Signal source | Warning | Critical | Action |
| --- | --- | --- | --- | --- |
| Denied sign-ins (24h) | `audit_events` rows with action `identity.sign_in_denied_disabled`, this Center, last 24h | ≥ 5 | ≥ 20 | Review **Account access** for repeated attempts against a specific disabled account; investigate why the account holder is still trying to sign in. |
| Unapplied provider events | `billing_webhook_receipts` + `screening_event_receipts` rows with `applied = false`, this Center | ≥ 1 | ≥ 10 | Open **Billing → Webhook failures** / **Screening failures** and follow [Provider failure recovery](provider-failure-recovery.md). |
| Overdue operational queue items | Guided-journey check-ins past their 30/60/90-day due date with no submission, this Center | ≥ 1 | ≥ 10 | Review **Guided journeys** in the counselor workspace; follow up with the assigned counselor. |
| Recent safety activity (7d) | Blocks + reports created in the last 7 days, this Center (shown only as a single combined count, never member-attributed here) | ≥ 1 | ≥ 5 | Review **Member readiness → Safety and administrative hold** and the counselor safety queue. |
| Backup drill age | Days since the most recent `verification_passed = true` row in `backup_drill_runs` | ≥ 45 days | ≥ 90 days, or never recorded | Run a restore drill following [Backup and restore](backup-and-restore.md) and record it. |

Thresholds are deliberately conservative for a 30-50 member pilot: a
single unresolved provider failure or a single recent safety event is
already worth an administrator's attention, so both start at "Warning"
rather than waiting for volume.

## What this dashboard intentionally does not do

- It does not page or notify anyone. An administrator must open the
  dashboard to see current status. There is no external alert channel in
  this pilot.
- It does not persist a history of past alert evaluations; each page load
  recomputes the metrics live.
- It does not invent a signal it cannot truthfully measure. The backup
  drill age metric is only as good as an operator's discipline in running
  [the restore drill](backup-and-restore.md) and recording it in
  `backup_drill_runs`; if that never happens, the metric correctly and
  permanently reports "Critical: no drill has ever been recorded."

## Health endpoints

Both processes expose a safe health check:

- Streamlit: the sidebar shows `Database: ready|degraded` from
  `SqlAlchemyDatabaseProbe`.
- FastAPI webhook service: `GET /health` returns `200` when every
  component (`PostgreSQL`, `Migrations`) is ready, `503` otherwise, with a
  JSON body naming each component's status and a safe, non-sensitive
  detail string. Neither the connection string nor a raw database error is
  ever included in that detail text (see `tests/test_database.py` for the
  regression tests enforcing this).
- `Migrations` reports whether the schema's `alembic_version` matches the
  latest migration in this checkout, catching a deploy that shipped code
  ahead of (or behind) its database schema.
