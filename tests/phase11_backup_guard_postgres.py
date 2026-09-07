"""Real PostgreSQL backup gate checks, restricted to an empty localhost test DB."""
from pathlib import Path
import os
import sys
import tempfile
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backup_postgres import create_backup
from scripts.deploy_preflight import check_deploy, DeployBlocked, REQUIRED_MIGRATIONS, LATEST_MIGRATION


def blocked(url, message):
    try:
        check_deploy(url)
    except DeployBlocked:
        print(f"PASS {message}")
    else:
        raise AssertionError(f"Guard unexpectedly passed: {message}")


def run(url):
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not parsed.path.lstrip("/").startswith("fbpostpro_phase11_guard"):
        raise SystemExit("Only a localhost fbpostpro_phase11_guard* isolated database is allowed.")
    with psycopg.connect(url) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
        if count:
            raise SystemExit("This test requires a new empty isolated database; it never resets an existing one.")
    assert check_deploy(url)["status"] == "bootstrap"
    print("PASS real PostgreSQL empty database bootstrap gate")

    with psycopg.connect(url) as conn:
        conn.execute("CREATE TABLE fbpostpro_users (user_id text PRIMARY KEY, display_name text)")
        conn.execute("INSERT INTO fbpostpro_users VALUES ('guard-user', 'Preserve customer fixture')")
        conn.execute("CREATE TABLE fbpostpro_schema_migrations (migration_id text PRIMARY KEY)")
        for marker in REQUIRED_MIGRATIONS[:-1]:
            conn.execute("INSERT INTO fbpostpro_schema_migrations VALUES (%s)", (marker,))
    blocked(url, "existing database missing backup receipt blocked before migration")

    with tempfile.TemporaryDirectory(prefix="fbpp-guard-pg-") as temp:
        path, digest, receipt = create_backup(url, Path(temp))
        assert path.is_file() and path.with_suffix(".dump.sha256").read_text().startswith(digest)
        result = check_deploy(url)
        assert result["status"] == "backup_verified" and result["receipt_id"] == receipt
        print("PASS real pg_dump archive verification, checksum, receipt bootstrap and fresh guard")

        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_backup_receipts SET created_at=NOW()-INTERVAL '25 hours' WHERE receipt_id=%s", (receipt,))
        blocked(url, "stale receipt rejected by PostgreSQL timestamp comparison")
        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_backup_receipts SET created_at=NOW()+INTERVAL '1 hour' WHERE receipt_id=%s", (receipt,))
        blocked(url, "future receipt rejected")
        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_backup_receipts SET created_at=NOW(), migration_id='unrelated-release' WHERE receipt_id=%s", (receipt,))
        blocked(url, "receipt for unrelated migration rejected")
        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_backup_receipts SET migration_id=%s WHERE receipt_id=%s", (LATEST_MIGRATION, receipt))
            conn.execute("INSERT INTO fbpostpro_schema_migrations VALUES (%s)", (LATEST_MIGRATION,))
            # Simulate a historical incomplete registry while the latest marker exists.
            # Renaming preserves the original marker fixture; no customer record is deleted.
            conn.execute("UPDATE fbpostpro_schema_migrations SET migration_id='incomplete-historical-marker' WHERE migration_id=%s",
                         (REQUIRED_MIGRATIONS[1],))
        result = check_deploy(url)
        assert result["status"] == "backup_verified" and result["pending"] == [REQUIRED_MIGRATIONS[1]]
        print("PASS latest-release backup covers a missing older registry marker")
        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_schema_migrations SET migration_id=%s WHERE migration_id='incomplete-historical-marker'",
                         (REQUIRED_MIGRATIONS[1],))
            conn.execute("UPDATE fbpostpro_backup_receipts SET created_at=NOW()-INTERVAL '25 hours' WHERE receipt_id=%s", (receipt,))
        assert check_deploy(url)["status"] == "current"
        print("PASS fully migrated backend restart does not require another backup")

        with psycopg.connect(url) as conn:
            conn.execute("CREATE TABLE fbpostpro_migration_issues (issue_key text PRIMARY KEY, resolved_at timestamptz)")
            conn.execute("INSERT INTO fbpostpro_migration_issues VALUES ('duplicate-fixture', NULL)")
        blocked(url, "unresolved legacy issue blocks deploy despite complete registry")
        with psycopg.connect(url) as conn:
            conn.execute("UPDATE fbpostpro_migration_issues SET resolved_at=NOW() WHERE issue_key='duplicate-fixture'")
            assert conn.execute("SELECT display_name FROM fbpostpro_users WHERE user_id='guard-user'").fetchone()[0] == "Preserve customer fixture"
        assert check_deploy(url)["status"] == "current"
        print("PASS resolved issue permits restart and customer fixture remains intact")


if __name__ == "__main__":
    value = os.environ.get("PHASE11_GUARD_TEST_URL", "")
    if not value:
        raise SystemExit("PHASE11_GUARD_TEST_URL is required.")
    run(value)
