"""Read-only backup/migration gate. Safe to call BEFORE importing app."""
import os
import re
import sys
import time

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


def sanitize_error_message(message):
    """Strip passwords, credentials, URLs, and secrets from diagnostic messages."""
    text = str(message or "")
    text = re.sub(r"postgres(?:ql)?://([^:@\s]+):([^@\s]+)@", r"postgresql://\1:[REDACTED]@", text, flags=re.IGNORECASE)
    text = re.sub(r"postgres(?:ql)?://([^\s@]+)@", r"postgresql://[REDACTED]@", text, flags=re.IGNORECASE)
    text = re.sub(r"(password\s*[:=]\s*)(['\"]?)[^\s'\"]+\2", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(token\s*[:=]\s*)(['\"]?)[^\s'\"]+\2", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(bearer\s+)[A-Za-z0-9_.-]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text[:250]


def classify_db_error(exc):
    """Categorize psycopg/db error into diagnostic category, transient status, and summary."""
    msg = str(exc or "").lower()
    exc_name = type(exc).__name__

    if any(m in msg for m in ("could not translate host name", "name or service not known", "getaddrinfo failed", "nodename nor servname provided")):
        return "dns_resolution_failed", True, "DNS resolution failed for database host"

    if any(m in msg for m in ("connection refused", "errno 111", "could not connect to server")):
        return "connection_refused", True, "Database server connection refused"

    if any(m in msg for m in ("timeout expired", "timed out", "connection timed out")):
        return "connection_timeout", True, "Database connection timed out"

    if any(m in msg for m in ("network is unreachable", "no route to host", "network unreachable")):
        return "network_unreachable", True, "Network route to database unreachable"

    if any(m in msg for m in ("the database system is starting up", "the database system is in recovery mode", "remaining connection slots are reserved")):
        return "server_starting_up", True, "Database server is starting up or slots full"

    if any(m in msg for m in ("server does not support ssl", "ssl error", "ssl connection has been closed", "ssl syscall", "certificate verify failed")):
        return "ssl_handshake_failed", False, "SSL/TLS handshake failed"

    if any(m in msg for m in ("password authentication failed", "no pg_hba.conf entry", "authentication failed")):
        return "authentication_failed", False, "Database password authentication failed"

    if "does not exist" in msg and "database" in msg:
        return "database_not_found", False, "Target PostgreSQL database does not exist"

    is_transient = exc_name in {"OperationalError", "InterfaceError"}
    return exc_name.lower(), is_transient, f"Database error ({exc_name})"


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
               ORDER BY created_at DESC LIMIT 1""", (LATEST_MIGRATION, max_age_hours),
        )
        receipt = cur.fetchone()
        if not receipt or not re.fullmatch(r"[a-f0-9]{64}", receipt[2] or ""):
            raise DeployBlocked("No fresh verified backup receipt for the pending release; deployment blocked.")
        return {"status": "backup_verified", "pending": pending, "receipt_id": receipt[0]}


def check_deploy(database_url):
    try:
        import psycopg
    except ImportError:
        raise DeployBlocked("Deployment preflight unavailable (missing psycopg); run pip install -r requirements.txt.") from None

    try:
        max_age = int(os.environ.get("BACKUP_RECEIPT_MAX_AGE_HOURS", "24"))
        if not 1 <= max_age <= 168:
            raise ValueError("BACKUP_RECEIPT_MAX_AGE_HOURS must be between 1 and 168.")
    except ValueError as val_err:
        raise DeployBlocked(f"Deployment preflight configuration error: {val_err}") from None

    try:
        params = database_parameters(database_url)
    except ValueError as val_err:
        raise DeployBlocked(f"Deployment preflight configuration error: {val_err}") from None

    host = params.get("host", "unknown")
    port = params.get("port", 5432)
    dbname = params.get("dbname", "unknown")
    sslmode = params.get("sslmode", "unspecified")
    timeout = params.get("connect_timeout", 15)

    print(
        f"deploy_preflight: inspecting database target host={host} port={port} dbname={dbname} sslmode={sslmode} connect_timeout={timeout}s",
        flush=True,
    )

    max_attempts = max(1, min(10, int(os.environ.get("PREFLIGHT_CONNECT_RETRIES", "5"))))
    retry_delays = [1.5, 2.5, 4.0, 6.0, 8.0]

    last_category = None
    last_sanitized = None

    for attempt in range(1, max_attempts + 1):
        try:
            with psycopg.connect(**params) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '10s'")
                if attempt > 1:
                    print(
                        f"deploy_preflight: database connection established on attempt {attempt}/{max_attempts} after transient delay.",
                        flush=True,
                    )
                return check_connection(conn, max_age)
        except DeployBlocked:
            # Policy failures from check_connection: never retry, fail immediately
            raise
        except Exception as exc:
            category, is_transient, diagnostic = classify_db_error(exc)
            sanitized_msg = sanitize_error_message(str(exc))
            last_category = category
            last_sanitized = f"{diagnostic} ({sanitized_msg})" if sanitized_msg else diagnostic

            if not is_transient or attempt >= max_attempts:
                break

            delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
            print(
                f"deploy_preflight: connection attempt {attempt}/{max_attempts} failed [{category}]: {sanitized_msg}. Retrying in {delay}s...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

    raise DeployBlocked(
        f"Deployment preflight unavailable [{last_category}]: {last_sanitized}; "
        f"target host={host} port={port} dbname={dbname} sslmode={sslmode}. Verify database/configuration. No migration was run."
    ) from None


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
