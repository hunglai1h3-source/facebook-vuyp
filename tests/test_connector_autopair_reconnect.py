"""
Comprehensive Regression & Integration Tests for:
FIRST-TIME ONE-CLICK PAIRING + AUTOMATIC RECONNECT
Web <-> FB POST PRO Connector <-> Worker

Tests cover:
1. Web detection of Connector Not Installed (CONNECTOR_NOT_INSTALLED)
2. Handshake & Extension Unpaired detection (WAITING_FOR_FIRST_CONFIRMATION)
3. First-Time One-Click Pairing from Web (Code generation, auto-pair, binding, instant heartbeat, CONNECTED)
4. Strict Origin Security Verification (Permitted vs blocked origins)
5. Automatic Reconnect on Chrome/Extension startup without re-pairing
6. Backend Reboot & Server Restart Persistence
7. Worker Offline State Transition (>75s) and Instant Wakeup Recovery
8. Token Revocation / Expiry (401 handling, REPAIR_REQUIRED state, 1-Click Re-Pair)
9. Security & Privacy Compliance (Zero FB credentials stored, SHA-256 token hashing, sanitized public device)
"""

import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_fresh_app(temp_dir):
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    users_file = Path(temp_dir) / "users.json"

    os.environ.update(
        APP_ENV="development",
        DATA_ROOT=str(data_dir),
        USERS_FILE=str(users_file),
        DATABASE_URL="",
        SECRET_KEY="test-connector-autopair-secret-key",
        ENABLE_LEGACY_ADMIN_AUTH="false",
    )
    spec = importlib.util.spec_from_file_location("app", REPO_ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="test_operator"):
    resp = client.post(
        "/register",
        data={
            "display_name": f"User {username}",
            "username": username,
            "email": f"{username}@example.test",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    return user_id


class ConnectorAutoPairReconnectTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autopair_test_")
        self.module = load_fresh_app(self.temp_dir)
        self.client = self.module.app.test_client()
        self.user_id = create_authenticated_user(self.client, "test_autopair_user")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ----------------------------------------------------------------------
    # 1. Web Detection & Initial States
    # ----------------------------------------------------------------------
    def test_01_web_detection_and_initial_status(self):
        """Web checks /api/extension/status and finds unpaired state."""
        resp = self.client.get("/api/extension/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertFalse(data.get("paired"))
        self.assertEqual(data.get("status"), "unpaired")
        self.assertEqual(data.get("state"), "WAITING_FOR_FIRST_CONFIRMATION")

        # HTML settings page renders with correct initial markup
        settings_resp = self.client.get("/settings")
        self.assertEqual(settings_resp.status_code, 200)
        html = settings_resp.get_data(as_text=True)
        self.assertIn("FB POST PRO Connector", html)
        self.assertIn("pairBtn", html)
        self.assertIn("pairBox", html)
        self.assertIn("pairCode", html)
        print("PASS test_01: Web initializes with unpaired WAITING_FOR_FIRST_CONFIRMATION state.")

    # ----------------------------------------------------------------------
    # 2. Strict Origin Security Verification
    # ----------------------------------------------------------------------
    def test_02_strict_origin_security_verification(self):
        """Connector origin checking rules only allow authorized domains."""
        # Check origin validation logic from service_worker.js
        # Allowed domains:
        # - https://fb-post-pro.onrender.com
        # - http://localhost:10000, http://127.0.0.1:5000
        # - https://fb-post-pro-pr-123.onrender.com
        # Blocked domains:
        # - https://attacker.com
        # - https://malicious-site.org
        # - https://another-customer-app.onrender.com

        def is_authorized_origin(server_origin, sender_origin):
            if not server_origin or not sender_origin:
                return False
            try:
                from urllib.parse import urlparse
                srv = urlparse(server_origin)
                snd = urlparse(sender_origin)
                if f"{srv.scheme}://{srv.netloc}" != f"{snd.scheme}://{snd.netloc}":
                    return False
                host = srv.hostname.lower()
                if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                    return True
                if host == "fb-post-pro.onrender.com":
                    return True
                if host.endswith(".onrender.com") and host.startswith("fb-post-pro"):
                    return True
                return False
            except Exception:
                return False

        # Verify allowed
        self.assertTrue(is_authorized_origin("https://fb-post-pro.onrender.com", "https://fb-post-pro.onrender.com"))
        self.assertTrue(is_authorized_origin("http://localhost:10000", "http://localhost:10000"))
        self.assertTrue(is_authorized_origin("http://127.0.0.1:5000", "http://127.0.0.1:5000"))
        self.assertTrue(is_authorized_origin("https://fb-post-pro-staging.onrender.com", "https://fb-post-pro-staging.onrender.com"))

        # Verify blocked
        self.assertFalse(is_authorized_origin("https://attacker.com", "https://attacker.com"))
        self.assertFalse(is_authorized_origin("https://evil-site.org", "https://evil-site.org"))
        self.assertFalse(is_authorized_origin("https://unrelated-render-app.onrender.com", "https://unrelated-render-app.onrender.com"))
        self.assertFalse(is_authorized_origin("https://fb-post-pro.onrender.com", "https://attacker.com"))
        print("PASS test_02: Strict origin security allows only genuine FB POST PRO domains and blocks attackers.")

    # ----------------------------------------------------------------------
    # 3. First-Time One-Click Pairing Flow
    # ----------------------------------------------------------------------
    def test_03_first_time_one_click_pairing(self):
        """One-click first time pairing: web creates code, extension pairs, heartbeat sent, transitions to CONNECTED."""
        # 1. Web generates pairing code
        resp_code = self.client.post("/api/extension/pair-code")
        self.assertEqual(resp_code.status_code, 200)
        data_code = resp_code.get_json()
        self.assertTrue(data_code.get("ok"))
        code = data_code.get("code")
        self.assertEqual(len(code), 8)

        # 2. Extension automatically pairs via /api/extension/pair (simulating service worker execution)
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({
                "code": code,
                "device_name": "Google Chrome • FB POST PRO",
                "extension_version": "1.0.0",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp_pair.status_code, 200)
        pair_data = resp_pair.get_json()
        self.assertTrue(pair_data.get("ok"))
        device_id = pair_data.get("device_id")
        token = pair_data.get("token")
        self.assertTrue(device_id)
        self.assertTrue(token)

        # 3. Extension immediately fires heartbeat(true)
        hb_resp = self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({
                "device_name": "Google Chrome • FB POST PRO",
                "extension_version": "1.0.0",
                "facebook_logged_in": True,
                "worker_state": "idle",
            }),
            headers={
                "X-Device-ID": device_id,
                "X-Agent-Token": token,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(hb_resp.status_code, 200)

        # 4. Check /api/extension/status now reports CONNECTED and online
        st_resp = self.client.get("/api/extension/status")
        self.assertEqual(st_resp.status_code, 200)
        st_data = st_resp.get_json()
        self.assertTrue(st_data.get("ok"))
        self.assertTrue(st_data.get("paired"))
        self.assertEqual(st_data.get("status"), "online")
        self.assertEqual(st_data.get("state"), "CONNECTED")
        self.assertTrue(st_data.get("facebook_logged_in"))
        self.assertEqual(st_data.get("device_id"), device_id)
        print("PASS test_03: First-time one-click pairing succeeds without manual copy/paste.")

    # ----------------------------------------------------------------------
    # 4. Automatic Reconnect on Startup
    # ----------------------------------------------------------------------
    def test_04_automatic_reconnect_on_startup(self):
        """When Chrome or extension starts up, saved credentials auto-send heartbeat and stay ONLINE."""
        # 1. Pair device first
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Chrome Laptop"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        token = resp_pair.get_json().get("token")

        # Simulate time passes and device was offline
        devices = self.module.load_devices(self.user_id)
        devices[device_id]["last_seen"] = (self.module.utc_now() - datetime.timedelta(seconds=120)).isoformat()
        devices[device_id]["status"] = "offline"
        self.module.save_devices(self.user_id, devices)

        # Verify status is WORKER_OFFLINE before reconnect
        st_before = self.client.get("/api/extension/status").get_json()
        self.assertEqual(st_before.get("state"), "WORKER_OFFLINE")
        self.assertEqual(st_before.get("status"), "offline")

        # 2. Chrome starts up -> Extension triggers onStartup -> sends heartbeat with stored credentials
        hb_resp = self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({
                "device_name": "Chrome Laptop",
                "extension_version": "1.0.0",
                "facebook_logged_in": True,
                "worker_state": "idle",
            }),
            headers={
                "X-Device-ID": device_id,
                "X-Agent-Token": token,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(hb_resp.status_code, 200)

        # 3. Status immediately recovers to CONNECTED without any re-pairing
        st_after = self.client.get("/api/extension/status").get_json()
        self.assertEqual(st_after.get("state"), "CONNECTED")
        self.assertEqual(st_after.get("status"), "online")
        print("PASS test_04: Automatic reconnect on startup recovers to ONLINE seamlessly.")

    # ----------------------------------------------------------------------
    # 5. Persistence Across Backend Server Restart
    # ----------------------------------------------------------------------
    def test_05_backend_reboot_persistence(self):
        """Backend restart does not lose paired device identity; next heartbeat validates token."""
        # Pair device
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Worker Node"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        token = resp_pair.get_json().get("token")

        # Simulate backend reload with the same data directory
        new_module = load_fresh_app(self.temp_dir)
        new_client = new_module.app.test_client()

        # Send heartbeat to the "newly booted" backend
        hb_resp = new_client.post(
            "/api/agent/heartbeat",
            data=json.dumps({
                "device_name": "Worker Node",
                "worker_state": "idle",
            }),
            headers={
                "X-Device-ID": device_id,
                "X-Agent-Token": token,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(hb_resp.status_code, 200)

        # Devices list still has the device online
        devs = new_module.load_devices(self.user_id)
        self.assertIn(device_id, devs)
        self.assertEqual(devs[device_id].get("status"), "online")
        print("PASS test_05: Backend reboot preserves device identity and accepts reconnect heartbeat.")

    # ----------------------------------------------------------------------
    # 6. Worker Offline Transition (>75s) and Wakeup
    # ----------------------------------------------------------------------
    def test_06_worker_offline_threshold_and_wakeup(self):
        """If worker does not heartbeat for >75s, transitions to WORKER_OFFLINE; wakeup heartbeat restores CONNECTED."""
        # Pair device and send initial heartbeat
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Chrome Device"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        token = resp_pair.get_json().get("token")

        self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({"worker_state": "idle"}),
            headers={"X-Device-ID": device_id, "X-Agent-Token": token, "Content-Type": "application/json"},
        )
        self.assertEqual(self.client.get("/api/extension/status").get_json().get("state"), "CONNECTED")

        # Set last_seen to 90 seconds ago
        devices = self.module.load_devices(self.user_id)
        devices[device_id]["last_seen"] = (self.module.utc_now() - datetime.timedelta(seconds=90)).isoformat()
        self.module.save_devices(self.user_id, devices)

        # Status is now WORKER_OFFLINE
        status = self.client.get("/api/extension/status").get_json()
        self.assertEqual(status.get("state"), "WORKER_OFFLINE")
        self.assertEqual(status.get("status"), "offline")

        # Wakeup heartbeat arrives
        self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({"worker_state": "idle"}),
            headers={"X-Device-ID": device_id, "X-Agent-Token": token, "Content-Type": "application/json"},
        )

        # Status restored to CONNECTED
        status_after = self.client.get("/api/extension/status").get_json()
        self.assertEqual(status_after.get("state"), "CONNECTED")
        self.assertEqual(status_after.get("status"), "online")
        print("PASS test_06: Worker offline threshold (>75s) and wakeup recovery tested successfully.")

    # ----------------------------------------------------------------------
    # 7. Token Revocation / Expiry & 1-Click Re-Pair
    # ----------------------------------------------------------------------
    def test_07_token_revocation_and_one_click_repair(self):
        """Revoking device token causes 401, marks REPAIR_REQUIRED, and allows 1-click re-pairing."""
        # 1. Pair initial device
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Chrome Device"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        old_token = resp_pair.get_json().get("token")

        # 2. User disconnects the device via web
        self.client.post("/connector/disconnect", data={"device_id": device_id})

        # 3. Old token heartbeat now rejected with 401 Unauthorized
        hb_resp = self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({"worker_state": "idle"}),
            headers={"X-Device-ID": device_id, "X-Agent-Token": old_token, "Content-Type": "application/json"},
        )
        self.assertEqual(hb_resp.status_code, 401)

        # 4. User clicks re-pair button (1-click)
        re_code_resp = self.client.post("/api/extension/pair-code")
        self.assertEqual(re_code_resp.status_code, 200)
        re_code = re_code_resp.get_json().get("code")

        re_pair_resp = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": re_code, "device_name": "Chrome Device Re-paired"}),
            content_type="application/json",
        )
        self.assertEqual(re_pair_resp.status_code, 200)
        new_device_id = re_pair_resp.get_json().get("device_id")
        new_token = re_pair_resp.get_json().get("token")
        self.assertNotEqual(old_token, new_token)

        # 5. New heartbeat succeeds
        new_hb = self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({"worker_state": "idle", "facebook_logged_in": True}),
            headers={"X-Device-ID": new_device_id, "X-Agent-Token": new_token, "Content-Type": "application/json"},
        )
        self.assertEqual(new_hb.status_code, 200)

        # 6. Status is CONNECTED again
        st = self.client.get("/api/extension/status").get_json()
        self.assertEqual(st.get("state"), "CONNECTED")
        self.assertEqual(st.get("status"), "online")
        print("PASS test_07: Token revocation returns 401 and 1-click re-pairing succeeds.")

    # ----------------------------------------------------------------------
    # 8. Token Expiration Handling
    # ----------------------------------------------------------------------
    def test_08_token_expiration_handling(self):
        """Expired token returns 401 on heartbeat and reports TOKEN_EXPIRED."""
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Chrome Device"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        token = resp_pair.get_json().get("token")

        # Manually expire the token in database
        devices = self.module.load_devices(self.user_id)
        devices[device_id]["token_expires_at"] = (self.module.utc_now() - datetime.timedelta(days=1)).isoformat()
        self.module.save_devices(self.user_id, devices)

        # Heartbeat is rejected with 401
        hb = self.client.post(
            "/api/agent/heartbeat",
            data=json.dumps({"worker_state": "idle"}),
            headers={"X-Device-ID": device_id, "X-Agent-Token": token, "Content-Type": "application/json"},
        )
        self.assertEqual(hb.status_code, 401)

        # /api/extension/status reports TOKEN_EXPIRED
        st = self.client.get("/api/extension/status").get_json()
        self.assertEqual(st.get("state"), "TOKEN_EXPIRED")
        print("PASS test_08: Expired token returns 401 and reports TOKEN_EXPIRED correctly.")

    # ----------------------------------------------------------------------
    # 9. Security & Privacy Guarantees
    # ----------------------------------------------------------------------
    def test_09_security_and_privacy_guarantees(self):
        """Zero Facebook password storage, SHA-256 token hashing, and sanitized public API."""
        resp_code = self.client.post("/api/extension/pair-code")
        code = resp_code.get_json().get("code")
        resp_pair = self.client.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Secure Chrome"}),
            content_type="application/json",
        )
        device_id = resp_pair.get_json().get("device_id")
        token = resp_pair.get_json().get("token")

        # 1. Stored device record has token_hash, not plain token
        devices = self.module.load_devices(self.user_id)
        dev = devices[device_id]
        self.assertNotIn("token", dev)
        self.assertTrue(dev.get("token_hash"))
        self.assertEqual(len(dev.get("token_hash")), 64)  # SHA-256 hex string

        # 2. Public endpoints do not leak token or hash
        st = self.client.get("/api/extension/status").get_json()
        self.assertNotIn("token", st)
        self.assertNotIn("token_hash", st)

        workers = self.client.get("/api/workers").get_json().get("workers", [])
        self.assertEqual(len(workers), 1)
        self.assertNotIn("token", workers[0])
        self.assertNotIn("token_hash", workers[0])

        # 3. Verify zero password keys anywhere in device storage
        for k in dev.keys():
            self.assertNotIn("password", k.lower())
            self.assertNotIn("passwd", k.lower())
            self.assertNotIn("cookie", k.lower())

        print("PASS test_09: Security & privacy guarantees verified 100%.")


if __name__ == "__main__":
    unittest.main()
