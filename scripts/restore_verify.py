"""Verify a backup by restoring only into an explicitly authorized empty database."""
from pathlib import Path
import os
import re
import subprocess
import sys

try:
    from .postgres_tools import database_identity, database_parameters, file_sha256, libpq_environment
except ImportError:
    from postgres_tools import database_identity, database_parameters, file_sha256, libpq_environment


def restore_backup(backup, target_url, production_url=""):
    if production_url and database_identity(target_url) == database_identity(production_url):
        raise ValueError("Refusing to restore into DATABASE_URL. Use an isolated RESTORE_DATABASE_URL.")
    backup = Path(backup).resolve()
    checksum_file = backup.with_suffix(backup.suffix + ".sha256")
    if not backup.is_file() or not checksum_file.is_file():
        raise ValueError("Backup and SHA-256 checksum file are required.")
    fields = checksum_file.read_text(encoding="utf-8").split()
    if not fields or not re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]):
        raise ValueError("Invalid backup checksum file.")
    if file_sha256(backup) != fields[0].lower():
        raise ValueError("Backup checksum mismatch.")
    import psycopg
    with psycopg.connect(**database_parameters(target_url)) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp_%%'
                AND c.relkind IN ('r','p','v','m','S','f')""")
            if cur.fetchone()[0]:
                raise ValueError("Restore target is not empty. Existing data will not be overwritten or deleted.")
    child_env = libpq_environment(target_url)
    # No --clean: even host aliases or concurrent target writers cannot cause data deletion.
    subprocess.run(
        ["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
         "--dbname", database_parameters(target_url)["dbname"], str(backup)],
        env=child_env, check=True, capture_output=True,
    )
    verification_sql = """
    SELECT COUNT(*) FROM fbpostpro_users;
    SELECT COUNT(*) FROM fbpostpro_campaign_engine;
    SELECT COUNT(*) FROM fbpostpro_campaign_tasks;
    SELECT COUNT(*) FROM fbpostpro_schema_migrations;
    """
    subprocess.run(["psql", "--set", "ON_ERROR_STOP=1", "--command", verification_sql],
                   env=child_env, check=True, capture_output=True)


def main():
    if os.environ.get("ALLOW_TEST_RESTORE", "").lower() != "yes":
        print("Set ALLOW_TEST_RESTORE=yes to authorize restoring into an empty test database.", file=sys.stderr)
        return 1
    if len(sys.argv) != 2:
        print("Usage: python scripts/restore_verify.py <backup.dump>", file=sys.stderr)
        return 1
    try:
        restore_backup(sys.argv[1], os.environ.get("RESTORE_DATABASE_URL", "").strip(),
                       os.environ.get("DATABASE_URL", "").strip())
    except ValueError as exc:
        print(f"restore_verified=false reason={exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"restore_verified=false error={type(exc).__name__}; inspect the isolated test environment", file=sys.stderr)
        return 1
    print("restore_verified=true target=RESTORE_DATABASE_URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
