# Pilot Launch Security Checklist

This is the final release gate for inviting real members to the closed
pilot (30-50 members). It consolidates the evidence required by the Pilot
Hardening milestone. Every row must be truthfully evidenced by a named
owner before invitations go out -- do not check a row that has not
actually happened, and do not deploy, commit, or push as part of
completing this checklist.

## How to use this checklist

- "Automated" rows are proven by the test suite and CI; they are safe to
  mark complete once `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy` all pass, and the
  GitHub Actions `python`, `backup-restore-drill`, and `container` jobs
  are green.
- "Operator" rows require a specific person to do a specific real-world
  action (choosing a host, running a drill against a real backup,
  generating a production secret) and record evidence here or in the
  linked runbook.

## Account security

| Item | Status | Evidence |
| --- | --- | --- |
| Explicit account enabled/disabled state checked on every sign-in | Automated | `tests/test_account_hardening.py` |
| Self-disable and last-active-administrator disable are both prohibited | Automated | `test_administrator_cannot_disable_their_own_account`, `test_last_active_administrator_cannot_be_disabled` |
| Admin allow-list (`MATCHWELL_ADMIN_EMAILS`) reconciled on every sign-in, both directions | Automated | `test_admin_allowlist_removal_revokes_access_on_next_sign_in`, `test_admin_allowlist_re_addition_restores_access_but_not_reactivation` |
| Re-adding an allow-list email never silently reactivates a disabled account | Automated | `test_admin_allowlist_re_addition_restores_access_but_not_reactivation` |
| Missing/forged OIDC issuer fails closed | Automated | `test_missing_oidc_issuer_fails_closed`; `app/main.py`'s `current_identity()` no longer defaults the issuer |
| Streamlit `cookie_secret` is a ≥32-byte CSPRNG value, generated for production (not reused from development), with a documented rotation plan | Operator | `.streamlit/secrets.toml.example`'s `[auth]` section documents generation and rotation; owner must generate and store the real production value outside this repository |

## Center isolation and authorization

| Item | Status | Evidence |
| --- | --- | --- |
| Role/Center/IDOR matrix: matching exclusions, webhook diagnostics, screening diagnostics, counselor earnings/ledger, billing queue, account access | Automated | `tests/test_matching_repository.py`, `tests/test_billing_repository.py`, `tests/test_account_hardening.py` |
| `_matching_pair_sets` scopes proposal history per Center while keeping block/report safety restrictions global | Automated | `test_matching_pair_sets_scope_proposals_by_center_but_not_safety_restrictions` |
| Billing webhook and screening event receipts carry `center_id`; unresolved/unattributable events are never shown to any Center admin | Automated | migration `20260909_0007`, `test_admin_webhook_failures_queue_lists_unapplied_receipts_only`, `test_process_screening_provider_event_is_idempotent_and_scoped` |

## Backup and restore

| Item | Status | Evidence |
| --- | --- | --- |
| `pg_dump`/`pg_restore` operator scripts refuse unsafe targets and never leak secrets on the command line or in logs | Automated + manual review | `scripts/backup/*.ps1`, `scripts/backup/*.sh` |
| Automated dump/restore/migrate/verify drill in CI, using synthetic data | Automated | `.github/workflows/ci.yml` `backup-restore-drill` job |
| Operator runbook covers ownership, retention, RPO/RTO, safe restore, rollback, and evidence capture | Documented | [Backup and restore](../runbooks/backup-and-restore.md) |
| Managed-host automatic backups confirmed enabled for the selected production database | Operator | Record in [Backup and restore](../runbooks/backup-and-restore.md)'s evidence table |
| At least one real production restore drill performed and recorded in `backup_drill_runs` | Operator | Not yet performed -- do not claim otherwise |

## Provider failure handling

| Item | Status | Evidence |
| --- | --- | --- |
| Screening reason/status codes constrained to a safe enum, enforced at the application boundary and by a database CHECK constraint | Automated | `ScreeningReasonCode`, migration `20260909_0007`'s `ck_screening_*_reason_allowlist` constraints |
| Screening provider failure/retry diagnostics, analogous to billing, without storing screening reports | Automated | `screening_event_receipts` columns, `PilotService.screening_failures` |
| Stripe and screening failure/replay/recovery runbook | Documented | [Provider failure recovery](../runbooks/provider-failure-recovery.md) |
| Malformed/replayed/out-of-order/unresolved provider flow tests | Automated | `tests/test_billing_repository.py`, `test_process_screening_provider_event_is_idempotent_and_scoped` |

## Observability

| Item | Status | Evidence |
| --- | --- | --- |
| Structured JSON logs with correlation IDs and denylist-based redaction | Automated | `tests/test_observability_logging.py` |
| No message bodies, reflections, assessment answers, counselor notes, screening details, tokens, cookies, secrets, or raw webhook bodies ever logged | Automated | `SENSITIVE_KEY_DENYLIST` enforcement tests |
| Safe application/database/migration health reporting | Automated | `tests/test_database.py`, `tests/test_health.py` |
| Health responses never leak a connection string or raw database error | Automated | `test_health_detail_never_leaks_connection_string`, `test_migration_status_failure_never_leaks_exception_detail` |
| Alert-ready aggregate metrics with documented thresholds | Automated + documented | `tests/test_alerts_and_analytics_domain.py`, [Monitoring and alerts](../runbooks/monitoring-and-alerts.md) |

## Pilot analytics

| Item | Status | Evidence |
| --- | --- | --- |
| Center-scoped aggregate funnel/conversion counts, admin-only | Automated | `test_analytics_snapshot_counts_funnel_stages_accurately` |
| Small-cell suppression (<5) for safety and provider-failure counts, documented threshold | Automated | `test_analytics_snapshot_applies_small_cell_suppression_to_safety_counts`, `domain/analytics.py` |
| No raw member export, free text, or message/reflection/screening/assessment content in analytics | Code review | `PilotAnalyticsSnapshot`'s fields are integers only |

## Accessibility

| Item | Status | Evidence |
| --- | --- | --- |
| Automated semantic/label/heading checks for critical Streamlit surfaces | Automated | `tests/test_accessibility.py` |
| Manual keyboard-only and screen-reader checklist, with contrast, zoom/reflow, error, and focus checks | Operator | [Accessibility checklist](../runbooks/accessibility-checklist.md) -- not yet performed |

## Security, configuration, and dependencies

| Item | Status | Evidence |
| --- | --- | --- |
| Encryption-at-rest language distinguishes application behavior from hosting/managed-Postgres evidence | Documented | [Authorization and data handling](authorization-and-data-handling.md#encryption-at-rest-application-behavior-vs-hosting-evidence) |
| Host encryption-at-rest evidence attached for the selected production database and its backups | Operator | Record in [Backup and restore](../runbooks/backup-and-restore.md) |
| Production configuration examples set `MATCHWELL_AUTO_MIGRATE = false` and document the separate privileged migration step and a DML-only application database role | Documented | `.streamlit/secrets.toml.example` |
| A DML-only PostgreSQL role is actually provisioned and used by the production application connection string | Operator | Not yet performed; local `.env` may remain development-friendly per README |
| `compose.yaml` explicitly documented as local-development-only, not a production recipe | Documented | `compose.yaml` header comment |
| `uv.lock` / `pyproject.toml` package index reviewed for public build reproducibility | Investigated | See "uv package index investigation" below |
| `docker build .` succeeds | Automated | GitHub Actions `container` job; validate locally before launch if Docker is available |
| Ruff lint + format, strict mypy, full pytest with ≥90% coverage | Automated | This session's validation run (see PR/session notes) |
| Package build (`uv build`) succeeds | Automated | Validate as part of final local validation |
| Streamlit and FastAPI health smoke checks pass | Automated | `tests/test_streamlit_app.py`, `tests/test_webhook_service.py` |
| Final focused security review | Operator | Perform before inviting real members |

### uv package index investigation

`pyproject.toml` pins `[[tool.uv.index]]` to
`https://packagefeedproxy.microsoft.io/pypi/simple/`. This was investigated
as a public-CI-reproducibility concern:

- The proxy responds successfully to an unauthenticated HTTPS request from
  this development environment.
- Regenerating `uv.lock` against public PyPI (`pypi.org` / `files.pythonhosted.org`)
  could not be validated end-to-end from this sandboxed environment: direct
  TLS connections to `files.pythonhosted.org` are blocked here (while
  `pypi.org` and the proxy are both reachable), so a full `uv lock`
  regeneration against public PyPI could not be completed and verified in
  this session.
- Per the instruction to not change a passing CI configuration blindly
  without being able to validate the alternative, **the index was left
  unchanged**. GitHub Actions' `ubuntu-latest` hosted runners have
  unrestricted public internet access and have historically built
  successfully against this index (see prior milestones' validation
  status), so this is not currently blocking CI.
- **Residual risk to track**: this index is a non-`pypi.org` mirror. If its
  public availability or access policy ever changes, the build would break
  for anyone outside whatever network currently reaches it. A follow-up
  task, run from an environment that can reach `files.pythonhosted.org`
  directly, should attempt `uv lock` against public PyPI, confirm `uv sync
  --frozen`, `uv build`, and the full test suite all still pass, and only
  then switch the default index.

## Sign-off

| Role | Name | Date | Signature/confirmation |
| --- | --- | --- | --- |
| Engineering owner | | | |
| Backup owner | | | |
| Accessibility reviewer | | | |
| Final security reviewer | | | |

**Status: Not launch-ready.** Every "Operator" row above must be completed
and evidenced before this checklist can be marked launch-ready. This
document must not be edited to claim completion of an operator action
that has not actually happened.
