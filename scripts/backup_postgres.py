"""Create a PostgreSQL custom-format backup without embedding credentials."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import os
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit


database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise SystemExit("DATABASE_URL is required.")


def libpq_environment(url):
    """Pass credentials through the child environment, never the process argument list."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise SystemExit("DATABASE_URL must be a valid PostgreSQL URL.")
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

backup_root = Path(os.environ.get("BACKUP_DIR", "backups")).resolve()
os.umask(0o077)
backup_root.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
target = backup_root / f"fbpostpro-{stamp}.dump"
partial = target.with_suffix(target.suffix + ".partial")

try:
    subprocess.run(
        ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(partial)],
        env=libpq_environment(database_url),
        check=True,
    )
    partial.replace(target)
except Exception:
    partial.unlink(missing_ok=True)
    raise
digest = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix(target.suffix + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")

try:
    retention_days = max(1, int(os.environ.get("BACKUP_RETENTION_DAYS", "30")))
except ValueError:
    retention_days = 30
cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
for dump in backup_root.glob("fbpostpro-*.dump"):
    modified = datetime.fromtimestamp(dump.stat().st_mtime, timezone.utc)
    if modified < cutoff:
        checksum = dump.with_suffix(dump.suffix + ".sha256")
        dump.unlink()
        if checksum.exists():
            checksum.unlink()

print(f"backup_created={target} sha256={digest} retention_days={retention_days}")
