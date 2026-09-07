"""Create and verify a unique PostgreSQL dump, then record a deploy receipt."""
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import sys
import uuid

try:
    from .deploy_preflight import LATEST_MIGRATION
    from .postgres_tools import database_parameters, file_sha256, libpq_environment
except ImportError:
    from deploy_preflight import LATEST_MIGRATION
    from postgres_tools import database_parameters, file_sha256, libpq_environment


def record_receipt(database_url, target, digest, migration_id):
    import psycopg
    receipt_id = uuid.uuid4().hex
    with psycopg.connect(**database_parameters(database_url)) as conn:
        with conn.cursor() as cur:
            # This small additive table also supports upgrades from pre-receipt releases.
            cur.execute("""CREATE TABLE IF NOT EXISTS public.fbpostpro_backup_receipts (
                receipt_id VARCHAR(80) PRIMARY KEY,
                migration_id VARCHAR(100) NOT NULL DEFAULT 'routine',
                backup_name VARCHAR(255) NOT NULL, sha256 VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            cur.execute("""INSERT INTO public.fbpostpro_backup_receipts
                (receipt_id, migration_id, backup_name, sha256) VALUES (%s, %s, %s, %s)""",
                (receipt_id, migration_id, target.name, digest))
    return receipt_id


def create_backup(database_url, backup_root, migration_id=LATEST_MIGRATION):
    child_env = libpq_environment(database_url)
    if not migration_id or len(migration_id) > 100:
        raise ValueError("BACKUP_MIGRATION_ID must contain 1 to 100 characters.")
    backup_root = Path(backup_root).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_root / f"fbpostpro-{stamp}-{uuid.uuid4().hex}.dump"
    partial = target.with_suffix(target.suffix + ".partial")
    # Exclusive creation prevents even a name collision from truncating an old backup.
    with partial.open("xb"):
        pass
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(partial)],
            env=child_env, check=True, capture_output=True,
        )
        if not partial.stat().st_size:
            raise RuntimeError("pg_dump created an empty backup.")
        subprocess.run(["pg_restore", "--list", str(partial)], env=child_env, check=True, capture_output=True)
        digest = file_sha256(partial)
        with partial.open("r+b") as stream:
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and fails when target already exists; no replace().
        os.link(partial, target)
        checksum = target.with_suffix(target.suffix + ".sha256")
        with checksum.open("x", encoding="utf-8") as stream:
            stream.write(f"{digest}  {target.name}\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        partial.unlink(missing_ok=True)
    receipt_id = record_receipt(database_url, target, digest, migration_id)
    return target, digest, receipt_id


def main():
    os.umask(0o077)
    try:
        target, digest, receipt = create_backup(
            os.environ.get("DATABASE_URL", "").strip(), os.environ.get("BACKUP_DIR", "backups"),
            os.environ.get("BACKUP_MIGRATION_ID", LATEST_MIGRATION),
        )
    except Exception as exc:
        # libpq/subprocess exception text can contain connection information. Keep it private.
        print(f"backup_created=false error={type(exc).__name__}; deployment remains blocked; completed files are preserved", file=sys.stderr)
        return 1
    print(f"backup_created={target} sha256={digest} receipt_id={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
