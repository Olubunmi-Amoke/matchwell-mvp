# Backup and Restore Runbook

This runbook governs PostgreSQL backup and restore for the Matchwell pilot.
It is written for the pilot's actual delivery recipe -- a portable Docker
application backed by a single managed PostgreSQL instance -- not the
deferred target architecture.

## Ownership

- The pilot administrator who holds the hosting/database provider account is
  the backup owner. Exactly one named owner must be recorded here before
  inviting members:

  | Role | Name | Contact | Provider account |
  | --- | --- | --- | --- |
  | Backup owner | _(fill in before launch)_ | _(fill in)_ | _(fill in)_ |

- The backup owner is responsible for: confirming managed backups are
  enabled, running the quarterly restore drill below, and keeping this
  document's evidence table current.

## What actually backs up the pilot database today

Do not claim more than is true:

- **Managed-host backups** (primary control): whichever managed PostgreSQL
  host is selected for production (for example a managed Postgres add-on)
  performs its own automated backups. This is the primary recovery
  mechanism, not the scripts in this repository.
- **Scripts in `scripts/backup/`** (secondary, operator-run control): a
  `pg_dump`/`pg_restore` drill you can run on demand, plus the same drill
  automated in CI against synthetic data (see below). These scripts prove
  the *mechanics* of dump/restore/migrate/verify. They are not themselves a
  scheduled backup service -- nothing in this repository runs them on a
  timer against production.

Before launch, the backup owner must record in this file:

- The selected hosting/database provider.
- The provider's documented backup frequency and retention window.
- Whether backups are automatic or require manual enabling, and
  confirmation that they are enabled for this pilot's database.

## Encryption evidence (host responsibility)

Matchwell's application layer does not implement its own encryption-at-rest
for stored rows; see
[Authorization and data handling](../security/authorization-and-data-handling.md)
and the corrected note in
[System architecture](../architecture/system-architecture.md#security-and-privacy).
Encryption at rest for the database, and for any backup artifact the host
stores, is provided by the selected managed-hosting provider's storage
layer, not by this application.

Before launch, the backup owner must attach or link the provider's own
evidence that:

1. The production database's underlying storage is encrypted at rest.
2. Backup artifacts (snapshots/dumps) the provider retains are also
   encrypted at rest.

_(Evidence links / attachment: fill in before launch. Do not mark this
runbook "launch ready" without them.)_

## Retention

- Managed-host backups: retention window per the provider's plan (record
  the exact number of days/backups once the provider is selected).
- Operator-run `pg_dump` archives created via `scripts/backup/pg-dump.ps1` /
  `pg-dump.sh`: treat as short-lived drill artifacts. Do not accumulate
  them anywhere reachable by the application; store at most the two most
  recent drill archives, encrypted at rest by whatever storage holds them,
  and delete older ones.

## RPO / RTO assumptions (pilot scale)

These are *assumptions* for a 30-50 member closed pilot, not measured
service levels:

- **Recovery Point Objective (RPO):** bounded by the managed host's backup
  frequency (commonly as low as a few minutes for continuous/WAL-based
  backup, or up to 24 hours for daily snapshots -- record the provider's
  actual figure once selected). Manual `pg_dump` drills are point-in-time
  and are not a substitute for continuous backup.
- **Recovery Time Objective (RTO):** at pilot scale (dozens of members,
  small data volume), a `pg_restore` of a full dump plus `alembic upgrade
  head` is expected to complete in minutes, not hours. This has been
  exercised mechanically (see Evidence below) but has not been timed
  against a production-sized, production-hosted backup.

## Safe restore procedure (operator-run)

Never restore over the production/source database. Both scripts below
refuse to do so.

1. **Take or obtain a backup.**
   - PowerShell: `./scripts/backup/pg-dump.ps1 -OutputPath .\backups\matchwell-<date>.dump`
     (reads `DATABASE_URL` from the environment; never pass a password on
     the command line).
   - Shell: `DATABASE_URL=... ./scripts/backup/pg-dump.sh ./backups/matchwell-<date>.dump`
   - Or use the managed host's own backup export/download feature.
2. **Restore into a separate, explicitly named database.** The restore
   database's name must contain `restore` or `drill` (both scripts enforce
   this) and must not resolve to the same host+database as the source. For
   example: `matchwell_restore_drill_<date>`.
   Keep both connection URLs in environment variables; never put a
   password-bearing URL in a command argument or a shell history entry.
   - PowerShell: `./scripts/backup/pg-restore.ps1 -BackupPath .\backups\matchwell-<date>.dump -RestoreDatabaseUrl "postgresql://user:pass@host:5432/matchwell_restore_drill_<date>"`
   - Shell: `RESTORE_DATABASE_URL=... DATABASE_URL=... ./scripts/backup/pg-restore.sh ./backups/matchwell-<date>.dump`
3. The script automatically runs `alembic upgrade head` against the restore
   database and then `scripts/backup/verify_invariants.py`, which prints
   only table names and row counts -- never row content -- and fails the
   drill if core reference tables are empty. For a quiesced source or
   synthetic CI database, pass `-CompareToSource` (PowerShell) or
   `--compare-source` (shell) to compare stable reference-table counts.
   Do not enable comparison against a live source because ordinary writes
   after the point-in-time dump can legitimately change its counts.
4. **Record the drill.** Insert a row into `backup_drill_runs` on the
   *production* database once you have confirmed the restore was drawn from
   a real production backup (the restore scripts print the exact `INSERT`
   statement to run). The admin operations dashboard's "backup drill age"
   metric reads the most recent row here -- see
   [Monitoring and alerts](monitoring-and-alerts.md).
5. **Tear down the restore database** once verification is complete; do not
   leave a second copy of member data sitting around indefinitely.

## Rollback

If a production incident requires rolling back to a restored copy (rather
than just verifying a drill):

1. Stop the Streamlit app and the webhook service so nothing writes to the
   damaged database during the rollback window.
2. Restore the chosen backup into a **new** database using the same safe
   procedure above.
3. Reconcile any data written between the backup's timestamp and the
   incident (this is a manual, case-by-case review -- there is no
   automated reconciliation tool in this pilot).
4. Point `DATABASE_URL` at the new database, run `alembic upgrade head`
   once more as a final safety check, then restart the application.
5. Only decommission the damaged database after the rollback is confirmed
   healthy and the incident is closed.

## Evidence (fill in as each item is actually completed)

| Item | Status | Date | Notes |
| --- | --- | --- | --- |
| Managed-host automatic backups confirmed enabled | Not started | | |
| Host encryption-at-rest evidence attached (database) | Not started | | |
| Host encryption-at-rest evidence attached (backups) | Not started | | |
| `scripts/backup/*` dump/restore/migrate/verify drill run locally | Not started | | |
| GitHub Actions `backup-restore-drill` job passing | Not started | | See `.github/workflows/ci.yml`. Uses synthetic CI data only; this is a mechanics check, not proof of a real production restore. |
| First real production restore drill performed and recorded in `backup_drill_runs` | Not started | | Do not claim this happened until it truthfully has. |

**Do not mark this milestone "launch ready" in
[the pilot launch checklist](../security/pilot-launch-checklist.md) until every
row above that requires a real production action is truthfully complete.**
