# FB POST PRO production runbook

## Durable data map

Production requires `APP_ENV=production` and `DATABASE_URL`. The application refuses to start without a strong `SECRET_KEY` and PostgreSQL in this mode.

| Data | PostgreSQL storage |
| --- | --- |
| Users, role, lock state, quota | `fbpostpro_users` |
| Facebook accounts and worker/profile mapping | `fbpostpro_accounts` |
| Groups and assignment | `fbpostpro_groups`, `fbpostpro_group_assignments` |
| Campaigns and scheduler state | `fbpostpro_campaign_engine`, legacy `fbpostpro_campaigns` |
| Tasks, retry, lease and progress | `fbpostpro_campaign_tasks` |
| Device, jobs, commands and settings | `fbpostpro_customer_data` JSONB records |
| Images | `fbpostpro_images` |
| Pairing/system state | `fbpostpro_system_data` |
| Operational and audit logs | `fbpostpro_operational_logs`, `fbpostpro_admin_audit_logs` |
| Applied schema versions | `fbpostpro_schema_migrations` |

`DATA_ROOT=/tmp/fbpostpro` on Render is transient cache and development/recovery storage only. No durable production state may depend on it. JSON fallback is enabled only outside production.

## Required environment

- `APP_ENV=production`
- `DATABASE_URL` supplied by the PostgreSQL provider
- `SECRET_KEY`: random value of at least 32 characters
- `TRUST_PROXY_HEADERS=true` behind Render's trusted proxy
- `SESSION_LIFETIME_HOURS=12`
- `ENABLE_LEGACY_ADMIN_AUTH=false`
- `OPERATIONAL_LOG_RETENTION_DAYS=30`
- `AUDIT_LOG_RETENTION_DAYS=365`
- `LOG_CLEANUP_BATCH_SIZE=10000`

Render's `RENDER_EXTERNAL_HOSTNAME` is trusted automatically. Set `ALLOWED_HOSTS` to a comma-separated list when custom domains are added; production refuses to start when neither value exists. Create the first role-based admin by promoting an existing verified user in PostgreSQL:

```sql
UPDATE fbpostpro_users SET role='admin' WHERE email='owner@example.com';
```

Run this with a unique, verified email and record the change in the operational change log. Legacy `ADMIN_PASSWORD` is disabled by default in production and is retained only for controlled migration compatibility.

## Migration procedure

1. Confirm `/ready` is healthy on the current release.
2. Run a pre-migration backup and copy its SHA-256 file to storage outside Render.
3. Deploy one instance first. Startup executes only idempotent `CREATE TABLE IF NOT EXISTS`, `ALTER ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and migration-marker inserts in a transaction.
4. Verify `fbpostpro_schema_migrations`, `/ready`, admin totals, one scheduled campaign and one worker heartbeat.
5. Roll out remaining instances. Re-running the migration is safe.

`render.yaml` uses an always-on instance and disables auto-deploy. Run the backup first, then start the Render deploy manually. If legacy production rows violate a new uniqueness invariant, startup preserves those rows, skips that specific unique index and emits `schema_constraint_skipped`; resolve the duplicate rows through an audited maintenance change before redeploying.

Never reset, truncate or seed over the production database.

## Backup policy

- Continuous provider-managed PITR on a paid Render Postgres instance is the primary recovery layer. Confirm the database is not on the Free compute plan and enable the longest recovery window available to the workspace.
- Run `python scripts/backup_postgres.py` before every schema deployment and at least daily from a protected scheduled environment.
- Store encrypted backup objects outside the application service and outside the primary database account.
- Default script retention is 30 days. Keep weekly copies for 12 weeks and monthly copies for 12 months when customer retention policy permits.
- Restrict backup access and rotate database credentials after suspected disclosure.

The script reads `DATABASE_URL`; it never contains or prints the database password and does not place the URL in child-process arguments. It writes a restricted `.partial` file, atomically renames a completed dump, and creates a SHA-256 sidecar. Set `BACKUP_DIR` and `BACKUP_RETENTION_DAYS` as needed.

## Restore drill

1. Create an empty isolated PostgreSQL test database.
2. Set `RESTORE_DATABASE_URL` to that test database. Do not set it equal to production.
3. Set `ALLOW_TEST_RESTORE=yes`.
4. Run `python scripts/restore_verify.py backups/<file>.dump`.
5. Verify user/campaign/task/migration counts, `/ready`, admin totals and a worker authentication check against the restored environment.
6. Perform this drill monthly and record recovery time and recovery point.

The restore script deliberately refuses to target `DATABASE_URL`, requires the backup checksum, and requires the explicit `ALLOW_TEST_RESTORE=yes` guard. The tested operating target is an empty isolated database; production restoration remains an operator/provider procedure after traffic is stopped and a recovery decision is recorded.

Target an RPO no greater than 24 hours with daily logical backups plus provider PITR, and measure RTO during the monthly restore drill. Escalate a failed backup or failed checksum immediately; do not proceed with a schema deploy.

## Runtime and recovery behavior

- Gunicorn runs with debug disabled. `/health` is a cheap process check; `/ready` checks PostgreSQL.
- Scheduled campaigns remain in PostgreSQL. A due campaign becomes dispatchable on the next worker poll. Delay therefore equals worker reconnect/poll delay; no duplicate tasks are materialized.
- A lost lease or worker restart marks the in-flight result as uncertain in `last_error` and does not replay it. Operators review that task instead of risking a duplicate Facebook post.
- Worker tokens are stored as SHA-256 digests. Legacy plaintext tokens migrate on their next successful authentication. Disconnecting a device revokes it by removing the server-side record.
- Token rotation uses disconnect followed by a new pairing code. The new raw token is returned once and is never shown in admin pages. Because the current extension uses a bearer token, HTTPS and prompt revoke/re-pair remain required after suspected interception.
- Operational logs expire after 30 days by default; admin audit logs expire after 365 days. Cleanup runs at most every six hours in bounded batches, skips `/health` and `/ready`, and deletes only the two log tables.

Role-based admin accounts are the primary mechanism. Keep `ENABLE_LEGACY_ADMIN_AUTH=false`; the compatibility password path is scheduled for removal after every operator has a verified role-admin account and one recovery drill has confirmed access.

## Incident checks

- Database unavailable: `/ready` returns 503; do not deploy traffic until it recovers.
- Worker offline or extension disconnected: campaign/task data remains queued; inspect `/admin/workers` and `/admin/logs`.
- Facebook logout/checkpoint: the worker reports the state and the user resolves it manually. Do not retry blindly or bypass platform controls.
- Suspected token leak: disconnect/re-pair the device, rotate `SECRET_KEY` with a planned user-session invalidation, and rotate database/worker credentials at their providers.
