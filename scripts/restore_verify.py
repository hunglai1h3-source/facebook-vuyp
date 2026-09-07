"""Restore a backup into an explicitly authorized non-production PostgreSQL database."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def libpq_environment(url):
    """Build libpq environment variables without exposing credentials in argv/output."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise SystemExit("RESTORE_DATABASE_URL must be a valid PostgreSQL URL.")
    child_env = os.environ.copy()
    child_env.pop("DATABASE_URL", None)
    child_env.pop("RESTORE_DATABASE_URL", None)
    values = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(parsed.path.lstrip("/")),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
    }
    child_env.update({key: value for key, value in values.items() if value})
    sslmode = (parse_qs(parsed.query).get("sslmode") or [""])[0]
    if sslmode:
        child_env["PGSSLMODE"] = sslmode
    return child_env


if os.environ.get("ALLOW_TEST_RESTORE", "").lower() != "yes":
    raise SystemExit("Set ALLOW_TEST_RESTORE=yes to confirm a destructive restore into the test database.")
target_url = os.environ.get("RESTORE_DATABASE_URL", "").strip()
production_url = os.environ.get("DATABASE_URL", "").strip()
if not target_url:
    raise SystemExit("RESTORE_DATABASE_URL is required.")
if production_url and target_url == production_url:
    raise SystemExit("Refusing to restore into DATABASE_URL. Use an isolated RESTORE_DATABASE_URL.")
if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/restore_verify.py <backup.dump>")
target_database = unquote(urlsplit(target_url).path.lstrip("/"))

backup = Path(sys.argv[1]).resolve()
if not backup.is_file():
    raise SystemExit(f"Backup not found: {backup}")
checksum_file = backup.with_suffix(backup.suffix + ".sha256")
if not checksum_file.is_file():
    raise SystemExit("Backup checksum file is required.")
expected = checksum_file.read_text(encoding="utf-8").split()[0]
actual = hashlib.sha256(backup.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit("Backup checksum mismatch.")

subprocess.run(
    ["pg_restore", "--exit-on-error", "--clean", "--if-exists", "--no-owner", "--no-acl", "--dbname", target_database, str(backup)],
    env=libpq_environment(target_url),
    check=True,
)
verification_sql = """
SELECT COUNT(*) FROM fbpostpro_users;
SELECT COUNT(*) FROM fbpostpro_campaign_engine;
SELECT COUNT(*) FROM fbpostpro_campaign_tasks;
SELECT COUNT(*) FROM fbpostpro_schema_migrations;
"""
subprocess.run(
    ["psql", "--set", "ON_ERROR_STOP=1", "--command", verification_sql],
    env=libpq_environment(target_url),
    check=True,
)
print("restore_verified=true target=RESTORE_DATABASE_URL")
