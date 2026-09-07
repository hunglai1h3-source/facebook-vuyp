# Backup and deployment safety

The deployment gate runs before app import or schema changes. Existing databases with unapplied migration IDs need a completed backup receipt for the latest pending release, created within the last 24 hours. `BACKUP_RECEIPT_MAX_AGE_HOURS` can be set from 1 to 168. An empty database can bootstrap without a prior backup; a database already at the current migration version can restart without another receipt. Unresolved migration issues block the gate even when migration markers exist.

From a protected operator environment with PostgreSQL client tools and the project requirements installed:

1. Set `DATABASE_URL` through the secret manager and `BACKUP_DIR` to durable restricted storage outside the application container.
2. Run `python scripts/backup_postgres.py` from the new release checkout. It defaults `BACKUP_MIGRATION_ID` to that release's migration ID.
3. Check exit status zero. The script validates the custom dump archive, computes SHA-256 incrementally, and records `fbpostpro_backup_receipts` only after the dump and checksum have been written. It creates only the small receipt table if upgrading an older database lacking that table.
4. Copy/encrypt the dump and checksum into the independent backup archive. Verify the copied checksum. A database receipt proves successful backup creation, not that an operator uploaded it to independent storage.
5. Run `python scripts/deploy_preflight.py`. It connects read-only, prints only a safe result and refuses stale/missing receipts or unresolved migration issues. It never imports the app or migrates customer data.
6. Deploy. Keep automatic deploy disabled. Confirm `/ready`, migration issue reports, task counts, and one worker heartbeat.

Backup files have microsecond timestamps plus random IDs. Both initial partial file and checksum use exclusive creation. Atomic publication refuses an existing target, so concurrent backup commands cannot overwrite each other. A failed receipt write does not delete a completed backup; fix connectivity/permissions and take a new backup. No script prunes existing dumps. Apply the documented daily/weekly/monthly retention policy through the independent archive's lifecycle policy after verifying at least one recoverable copy. `BACKUP_RETENTION_DAYS` from older releases is no longer an automatic delete switch.

## Restore drill

Create an **empty isolated** PostgreSQL database. Set `RESTORE_DATABASE_URL` to it and `ALLOW_TEST_RESTORE=yes`, then run:

```text
python scripts/restore_verify.py <backup.dump>
```

The script requires the SHA-256 sidecar, rejects the source database even when URL credentials/query differ, rejects an occupied target, and runs the restore in one transaction. It does not execute `--clean` or drop existing objects. An alias that happens to point to a populated production database is also rejected by the empty-target check. Use a new empty database for every restore drill; do not reset an old customer database to make a drill pass.

The script checks core table counts after restore. Also compare those counts to the backup-time operational record, inspect representative campaigns/tasks, and measure recovery time. Schedule a drill monthly. Real provider PITR, production schema/data validation, independent archive availability and actual Render deployment remain operator checks.

## Maintenance boundaries

- Keep `REQUIRED_MIGRATIONS` in `scripts/deploy_preflight.py` in sync with additive registry entries whenever a release changes required schema. Tests cover current registry alignment.
- Resolve legacy duplicate conflicts through an audited maintenance process preserving source records and campaign history. Never insert a successful migration marker or mark an issue resolved simply to bypass the gate.
- No automated receipt can guarantee a future restore or protect against a database superuser fabricating one. Restrict database roles and backup operator access; verify off-service storage separately.
- Restoring a backup also restores its historic migration registry. The next deployment evaluates the restored database and may correctly require a new backup for pending migrations.
