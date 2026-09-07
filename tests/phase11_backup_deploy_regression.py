"""Backup/deploy safety checks; no customer database and no Facebook activity."""
import contextlib
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import backup_postgres as backup
from scripts import deploy_preflight as deploy
from scripts import postgres_tools as pgtools
from scripts import restore_verify as restore


class FakeCursor:
    def __init__(self, tables, applied=(), receipt=None, issues=0):
        self.tables, self.applied, self.receipt, self.issues = tables, applied, receipt, issues
        self.statements, self.rows = [], []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "pg_tables" in sql:
            self.rows = [(name,) for name in self.tables]
        elif "migration_issues" in sql:
            self.rows = [(self.issues,)]
        elif "SELECT migration_id" in sql:
            self.rows = [(name,) for name in self.applied]
        elif "SELECT receipt_id" in sql:
            self.rows = [self.receipt] if self.receipt else []
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class BackupDeployRegression(unittest.TestCase):
    source_url = "postgresql://operator:sensitive-password@localhost:5432/source"

    def fake_dump(self, args, **kwargs):
        self.assertNotIn("sensitive-password", " ".join(args))
        self.assertNotIn("DATABASE_URL", kwargs["env"])
        if args[0] == "pg_dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"PGDMP-test-backup")
        return subprocess.CompletedProcess(args, 0)

    def connection(self, *args, **kwargs):
        cursor = FakeCursor(*args, **kwargs)
        return Mock(cursor=Mock(return_value=cursor)), cursor

    def test_preflight_is_read_only_and_distinguishes_bootstrap_current_upgrade(self):
        conn, cur = self.connection(set())
        self.assertEqual(deploy.check_connection(conn)["status"], "bootstrap")
        tables = {"fbpostpro_users", "fbpostpro_schema_migrations", "fbpostpro_backup_receipts"}
        conn, cur = self.connection(tables, deploy.REQUIRED_MIGRATIONS)
        self.assertEqual(deploy.check_connection(conn)["status"], "current")
        conn, cur = self.connection(tables, deploy.REQUIRED_MIGRATIONS[:-1])
        with self.assertRaises(deploy.DeployBlocked):
            deploy.check_connection(conn)
        conn, cur = self.connection(tables, deploy.REQUIRED_MIGRATIONS[:-1], ("receipt", "unique.dump", "a" * 64))
        self.assertEqual(deploy.check_connection(conn)["status"], "backup_verified")
        self.assertTrue(all(sql.lstrip().startswith("SELECT") for sql, _ in cur.statements))
        receipt_sql, receipt_params = cur.statements[-1]
        self.assertIn("created_at <= NOW()", receipt_sql)
        self.assertIn("created_at >= NOW()", receipt_sql)
        self.assertEqual(receipt_params, (deploy.LATEST_MIGRATION, 24))

    def test_unresolved_legacy_issue_blocks_even_when_markers_exist(self):
        conn, _ = self.connection({"fbpostpro_users", "fbpostpro_migration_issues", "fbpostpro_schema_migrations"},
                                  deploy.REQUIRED_MIGRATIONS, issues=1)
        with self.assertRaisesRegex(deploy.DeployBlocked, "Unresolved"):
            deploy.check_connection(conn)

    def test_secret_environment_and_identity(self):
        with patch.dict(os.environ, {"PGSERVICE": "attacker", "PGHOSTADDR": "wrong", "PGOPTIONS": "unsafe", "DATABASE_URL": self.source_url}):
            child = pgtools.libpq_environment(self.source_url + "?sslmode=verify-full")
        self.assertEqual(child["PGSSLMODE"], "verify-full")
        self.assertEqual(child["PGPASSWORD"], "sensitive-password")
        self.assertNotIn("PGSERVICE", child)
        self.assertNotIn("PGHOSTADDR", child)
        self.assertNotIn("PGOPTIONS", child)
        self.assertNotIn("DATABASE_URL", child)
        self.assertEqual(pgtools.database_identity(self.source_url),
                         pgtools.database_identity("postgres://different:another@LOCALHOST:5432/source?sslmode=require"))

    def test_backup_collision_preserves_previous_dump_and_receipt_order(self):
        with tempfile.TemporaryDirectory(prefix="fbpp-backup-safety-") as temp:
            root = Path(temp)
            old = root / "fbpostpro-old.dump"
            old.write_bytes(b"old customer backup")
            clock = Mock()
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with patch.object(backup, "datetime", clock), patch.object(backup.uuid, "uuid4", return_value=Mock(hex="fixed")), \
                 patch.object(backup.subprocess, "run", side_effect=self.fake_dump), \
                 patch.object(backup, "record_receipt", return_value="receipt") as receipt:
                path, digest, _ = backup.create_backup(self.source_url, root)
                self.assertEqual(pgtools.file_sha256(path), digest)
                self.assertTrue(path.with_suffix(".dump.sha256").exists())
                with self.assertRaises(FileExistsError):
                    backup.create_backup(self.source_url, root)
                self.assertEqual(receipt.call_count, 1)
                self.assertEqual(path.read_bytes(), b"PGDMP-test-backup")
                self.assertEqual(old.read_bytes(), b"old customer backup")
                self.assertFalse(list(root.glob("*.partial")))

    def test_failed_dump_records_no_receipt_and_no_secret(self):
        with tempfile.TemporaryDirectory(prefix="fbpp-failed-backup-") as temp, \
             patch.dict(os.environ, DATABASE_URL=self.source_url, BACKUP_DIR=temp), \
             patch.object(backup.subprocess, "run", side_effect=RuntimeError(self.source_url)), \
             patch.object(backup, "record_receipt") as receipt:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(backup.main(), 1)
            receipt.assert_not_called()
            self.assertNotIn("sensitive-password", output.getvalue())
            self.assertFalse(list(Path(temp).glob("*.dump*")))

    def test_restore_rejects_equivalent_source_url_before_any_mutation(self):
        with patch.object(restore.subprocess, "run") as command:
            with self.assertRaisesRegex(ValueError, "Refusing to restore into DATABASE_URL"):
                restore.restore_backup("irrelevant.dump", "postgres://another:secret@LOCALHOST/source?sslmode=require", self.source_url)
            command.assert_not_called()

    def test_restore_checksum_and_nonempty_target_guard(self):
        import psycopg
        with tempfile.TemporaryDirectory(prefix="fbpp-restore-safety-") as temp:
            path = Path(temp) / "restore.dump"
            path.write_bytes(b"PGDMP-example")
            checksum = path.with_suffix(".dump.sha256")
            checksum.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid backup checksum"):
                restore.restore_backup(path, "postgresql://localhost/isolated")
            checksum.write_text(pgtools.file_sha256(path), encoding="utf-8")
            conn = Mock()
            conn.__enter__ = Mock(return_value=conn)
            conn.__exit__ = Mock(return_value=None)
            cur = Mock()
            cur.__enter__ = Mock(return_value=cur)
            cur.__exit__ = Mock(return_value=None)
            conn.cursor.return_value = cur
            cur.fetchone.return_value = (1,)
            with patch.object(psycopg, "connect", return_value=conn), patch.object(restore.subprocess, "run") as command:
                with self.assertRaisesRegex(ValueError, "not empty"):
                    restore.restore_backup(path, "postgresql://localhost/isolated")
                command.assert_not_called()
                cur.fetchone.return_value = (0,)
                restore.restore_backup(path, "postgresql://localhost/isolated")
                args = command.call_args_list[0].args[0]
                self.assertNotIn("--clean", args)
                self.assertIn("--single-transaction", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
