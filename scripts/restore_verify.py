"""Restore a backup into an explicitly authorized non-production PostgreSQL database."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys


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

backup = Path(sys.argv[1]).resolve()
if not backup.is_file():
    raise SystemExit(f"Backup not found: {backup}")
checksum_file = backup.with_suffix(backup.suffix + ".sha256")
if checksum_file.exists():
    expected = checksum_file.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(backup.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit("Backup checksum mismatch.")

subprocess.run(
    ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl", "--dbname", target_url, str(backup)],
    check=True,
)
verification_sql = """
SELECT COUNT(*) FROM fbpostpro_users;
SELECT COUNT(*) FROM fbpostpro_campaign_engine;
SELECT COUNT(*) FROM fbpostpro_campaign_tasks;
SELECT COUNT(*) FROM fbpostpro_schema_migrations;
"""
subprocess.run(["psql", target_url, "--set", "ON_ERROR_STOP=1", "--command", verification_sql], check=True)
print("restore_verified=true target=RESTORE_DATABASE_URL")
