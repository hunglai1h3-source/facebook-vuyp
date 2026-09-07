"""Read-only backup/migration gate. Safe to call BEFORE importing app."""
import os
import re
import sys

try:
    from .postgres_tools import database_parameters
except ImportError:
    from postgres_tools import database_parameters


REQUIRED_MIGRATIONS = (
    "account_system_v1", "phase07_bulk_groups_v1", "phase09_admin_operations_v1",
    "phase10_production_hardening_v1", "phase10_production_hardening_v2",
    "phase11_remaining_risk_remediation_v1",
)
LATEST_MIGRATION = REQUIRED_MIGRATIONS[-1]


class DeployBlocked(RuntimeError):
    """Safe operator-facing failure without connection strings or secrets."""


def check_connection(conn, max_age_hours=24):
    """Inspect only; never import app, execute DDL or apply migrations."""
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'fbpostpro_%'")
        tables = {row[0] for row in cur.fetchall()}
        # A backup tool may have created only its receipt table before first bootstrap.
        if not tables - {"fbpostpro_backup_receipts"}:
            return {"status": "bootstrap", "pending": list(REQUIRED_MIGRATIONS)}
        if "fbpostpro_migration_issues" in tables:
            cur.execute("SELECT COUNT(*) FROM public.fbpostpro_migration_issues WHERE resolved_at IS NULL")
            if cur.fetchone()[0]:
                raise DeployBlocked("Unresolved migration issues require operator review before deploy.")
        applied = set()
        if "fbpostpro_schema_migrations" in tables:
            cur.execute("SELECT migration_id FROM public.fbpostpro_schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        pending = [name for name in REQUIRED_MIGRATIONS if name not in applied]
        if not pending:
            return {"status": "current", "pending": []}
        if "fbpostpro_backup_receipts" not in tables:
            raise DeployBlocked("Pending migrations require a fresh verified backup receipt; run scripts/backup_postgres.py first.")
        # A backup for the latest pending release covers all preceding additive migrations.
        cur.execute(
            """SELECT receipt_id, backup_name, sha256 FROM public.fbpostpro_backup_receipts
               WHERE migration_id=%s AND created_at <= NOW()
                 AND created_at >= NOW() - (%s * INTERVAL '1 hour')
               ORDER BY created_at DESC LIMIT 1""", (pending[-1], max_age_hours),
        )
        receipt = cur.fetchone()
        if not receipt or not re.fullmatch(r"[a-f0-9]{64}", receipt[2] or ""):
            raise DeployBlocked("No fresh verified backup receipt for the pending release; deployment blocked.")
        return {"status": "backup_verified", "pending": pending, "receipt_id": receipt[0]}


def check_deploy(database_url):
    try:
        import psycopg
        max_age = int(os.environ.get("BACKUP_RECEIPT_MAX_AGE_HOURS", "24"))
        if not 1 <= max_age <= 168:
            raise ValueError
        with psycopg.connect(**database_parameters(database_url)) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '10s'")
            return check_connection(conn, max_age)
    except DeployBlocked:
        raise
    except Exception as exc:
        raise DeployBlocked(f"Deployment preflight unavailable ({type(exc).__name__}); verify database/configuration. No migration was run.") from None


def main():
    try:
        result = check_deploy(os.environ.get("DATABASE_URL", ""))
    except DeployBlocked as exc:
        print(f"deploy_preflight=blocked reason={exc}", file=sys.stderr)
        return 1
    print(f"deploy_preflight={result['status']} pending_count={len(result['pending'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
