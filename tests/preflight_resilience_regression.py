"""Regression test suite for deploy_preflight resilience, diagnostics, and zero secret leaks."""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import deploy_preflight as deploy
from scripts import postgres_tools as pgtools


class PreflightResilienceRegression(unittest.TestCase):
    def test_sanitize_error_message_redacts_credentials_and_urls(self):
        raw_msg = (
            'connection to server at "postgres://admin:super_secret_password_123@dpg-abc1234-a.oregon:5432/medicare_db" '
            'failed: password="super_secret_password_123" and token="tok_live_998877" '
            'Bearer secret_jwt_token_payload'
        )
        sanitized = deploy.sanitize_error_message(raw_msg)
        self.assertNotIn("super_secret_password_123", sanitized)
        self.assertNotIn("tok_live_998877", sanitized)
        self.assertNotIn("secret_jwt_token_payload", sanitized)
        self.assertIn("[REDACTED]", sanitized)
        self.assertIn("medicare_db", sanitized)

    def test_classify_db_error_categories(self):
        # 1. DNS
        exc_dns = Exception('could not translate host name "dpg-test-a" to address: Name or service not known')
        cat, transient, diag = deploy.classify_db_error(exc_dns)
        self.assertEqual(cat, "dns_resolution_failed")
        self.assertTrue(transient)

        # 2. Refused
        exc_ref = Exception('connection to server at "10.0.0.1", port 5432 failed: Connection refused')
        cat, transient, diag = deploy.classify_db_error(exc_ref)
        self.assertEqual(cat, "connection_refused")
        self.assertTrue(transient)

        # 3. Timeout
        exc_to = Exception('connection to server at "10.0.0.1", port 5432 failed: timeout expired')
        cat, transient, diag = deploy.classify_db_error(exc_to)
        self.assertEqual(cat, "connection_timeout")
        self.assertTrue(transient)

        # 4. Network unreachable
        exc_net = Exception("connection failed: Network is unreachable")
        cat, transient, diag = deploy.classify_db_error(exc_net)
        self.assertEqual(cat, "network_unreachable")
        self.assertTrue(transient)

        # 5. SSL
        exc_ssl = Exception("server does not support SSL, but SSL was required")
        cat, transient, diag = deploy.classify_db_error(exc_ssl)
        self.assertEqual(cat, "ssl_handshake_failed")
        self.assertFalse(transient)

        # 6. Auth
        exc_auth = Exception('FATAL: password authentication failed for user "medicare_user"')
        cat, transient, diag = deploy.classify_db_error(exc_auth)
        self.assertEqual(cat, "authentication_failed")
        self.assertFalse(transient)

        # 7. Database not found
        exc_db = Exception('FATAL: database "nonexistent_db" does not exist')
        cat, transient, diag = deploy.classify_db_error(exc_db)
        self.assertEqual(cat, "database_not_found")
        self.assertFalse(transient)

    def test_transient_retry_recovers_successfully(self):
        import psycopg
        db_url = "postgresql://admin:secret@dpg-fake-a:5432/medicare_db"

        conn_mock = Mock()
        conn_mock.__enter__ = Mock(return_value=conn_mock)
        conn_mock.__exit__ = Mock(return_value=None)
        cur_mock = Mock()
        cur_mock.__enter__ = Mock(return_value=cur_mock)
        cur_mock.__exit__ = Mock(return_value=None)
        conn_mock.cursor.return_value = cur_mock

        attempts = [0]
        def mock_connect(*args, **kwargs):
            attempts[0] += 1
            if attempts[0] == 1:
                raise psycopg.OperationalError('could not translate host name "dpg-fake-a" to address: Name or service not known')
            return conn_mock

        with patch.dict(os.environ, {"PREFLIGHT_CONNECT_RETRIES": "3"}), \
             patch.object(deploy.time, "sleep") as mock_sleep, \
             patch.object(deploy, "check_connection", return_value={"status": "bootstrap", "pending": []}), \
             patch("psycopg.connect", side_effect=mock_connect):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                res = deploy.check_deploy(db_url)
            self.assertEqual(res["status"], "bootstrap")
            self.assertEqual(attempts[0], 2)
            self.assertEqual(mock_sleep.call_count, 1)
            self.assertIn("established on attempt 2/3", out.getvalue())
            self.assertNotIn("secret", out.getvalue())
            self.assertNotIn("secret", err.getvalue())

    def test_non_transient_auth_error_aborts_immediately_without_retrying(self):
        import psycopg
        db_url = "postgresql://admin:super_secret@dpg-fake-a:5432/medicare_db"

        attempts = [0]
        def mock_connect(*args, **kwargs):
            attempts[0] += 1
            raise psycopg.OperationalError('FATAL: password authentication failed for user "admin"')

        with patch.dict(os.environ, {"PREFLIGHT_CONNECT_RETRIES": "5"}), \
             patch.object(deploy.time, "sleep") as mock_sleep, \
             patch("psycopg.connect", side_effect=mock_connect):
            err = io.StringIO()
            with redirect_stderr(err):
                with self.assertRaises(deploy.DeployBlocked) as ctx:
                    deploy.check_deploy(db_url)
            self.assertEqual(attempts[0], 1)
            self.assertEqual(mock_sleep.call_count, 0)
            self.assertIn("authentication_failed", str(ctx.exception))
            self.assertNotIn("super_secret", str(ctx.exception))
            self.assertNotIn("super_secret", err.getvalue())

    def test_exhausted_retries_fail_with_clear_diagnostics_and_zero_leaks(self):
        import psycopg
        db_url = "postgresql://admin:super_secret@dpg-fake-a:5432/medicare_db"

        def mock_connect(*args, **kwargs):
            raise psycopg.OperationalError('connection to server at "10.0.0.2", port 5432 failed: timeout expired')

        with patch.dict(os.environ, {"PREFLIGHT_CONNECT_RETRIES": "3"}), \
             patch.object(deploy.time, "sleep") as mock_sleep, \
             patch("psycopg.connect", side_effect=mock_connect):
            err = io.StringIO()
            with redirect_stderr(err):
                with self.assertRaises(deploy.DeployBlocked) as ctx:
                    deploy.check_deploy(db_url)
            self.assertEqual(mock_sleep.call_count, 2)
            msg = str(ctx.exception)
            self.assertIn("connection_timeout", msg)
            self.assertIn("host=dpg-fake-a", msg)
            self.assertIn("dbname=medicare_db", msg)
            self.assertNotIn("super_secret", msg)

    def test_main_cli_output_and_exit_codes(self):
        db_url = "postgresql://admin:super_secret@dpg-fake-a:5432/medicare_db"

        # Case 1: Success
        with patch.dict(os.environ, {"DATABASE_URL": db_url}), \
             patch.object(deploy, "check_deploy", return_value={"status": "current", "pending": []}):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = deploy.main()
            self.assertEqual(code, 0)
            self.assertIn("deploy_preflight=current", out.getvalue())

        # Case 2: Blocked
        with patch.dict(os.environ, {"DATABASE_URL": db_url}), \
             patch.object(deploy, "check_deploy", side_effect=deploy.DeployBlocked("Unresolved migration issues")):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = deploy.main()
            self.assertEqual(code, 1)
            self.assertIn("deploy_preflight=blocked reason=Unresolved migration issues", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
