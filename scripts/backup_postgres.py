"""Create a PostgreSQL custom-format backup without embedding credentials."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import os
import subprocess
import sys


database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise SystemExit("DATABASE_URL is required.")

backup_root = Path(os.environ.get("BACKUP_DIR", "backups")).resolve()
backup_root.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
target = backup_root / f"fbpostpro-{stamp}.dump"

subprocess.run(
    ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(target), database_url],
    check=True,
)
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
