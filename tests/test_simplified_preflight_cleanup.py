"""
Test Suite for Simplified Preflight Validation Cleanup (Simple Single-Connector Flow).

Validates:
1. Compose UI has clean Vietnamese title 'Kiểm tra an toàn trước khi chạy' and no legacy technical labels.
2. Reassurance note refers to 'Connector' instead of 'Desktop Worker'.
3. Summary metrics include media indicator (quickPreviewMedia).
4. campaign-wizard.js groups checks into 4 campaign essentials:
   - Nội dung bài đăng
   - Danh sách nhóm
   - Lịch chạy
   - Hệ thống sẵn sàng (Unified System Ready)
5. Preflight API contract preserves all 7 internal checks for engine/background compatibility
   while providing the unified 'system_ready' summary with /settings direct action.
6. Safe blocking when system is not ready with user-friendly error message:
   "Chưa thể chạy chiến dịch vì hệ thống chưa sẵn sàng. Hãy kiểm tra Connector."
7. No technical jargon exposed to the user.
"""

import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_fresh_app(temp_dir, simple_mode="1"):
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    users_file = Path(temp_dir) / "users.json"

    os.environ.update(
        APP_ENV="development",
        SIMPLE_MODE=simple_mode,
        SECRET_KEY="test_key_preflight_clean",
        DATA_DIR=str(data_dir),
        USERS_FILE=str(users_file),
        TESTING="1",
        WTF_CSRF_ENABLED="0",
    )

    module_name = f"app_test_pf_{simple_mode}_{temp_dir.rsplit('_', 1)[-1]}"
    spec = importlib.util.spec_from_file_location(module_name, str(REPO_ROOT / "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="pf_user"):
    client.post(
        "/register",
        data={
            "display_name": f"User {username}",
            "username": username,
            "email": f"{username}@example.com",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
        follow_redirects=True,
    )
    with client.session_transaction() as sess:
        return sess["user_id"]


def pair_device(client, name="Test Connector Chrome"):
    code_res = client.post("/api/extension/pair-code")
    code = code_res.get_json()["code"]
    res = client.post("/api/extension/pair", json={"code": code, "name": name})
    data = res.get_json()
    return data["device_id"], data["token"]


def send_heartbeat(client, device_id, token, facebook_logged_in=True, facebook_user_id="100012345678"):
    return client.post(
        "/api/agent/heartbeat",
        headers={"X-Device-ID": device_id, "X-Agent-Token": token},
        json={
            "device_name": "Test Chrome",
            "worker_state": "idle",
            "extension_version": "1.0",
            "facebook_logged_in": facebook_logged_in,
            "facebook_user_id": facebook_user_id,
            "c_user": facebook_user_id,
        },
    )


class SimplifiedPreflightCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_pf_clean_")
        self.app_module = load_fresh_app(self.temp_dir, simple_mode="1")
        self.client = self.app_module.app.test_client()
        self.user_id = create_authenticated_user(self.client, "pf_cleaner")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_compose_template_contains_simplified_preflight_title_and_no_legacy_title(self):
        """1. /compose page displays 'Kiểm tra an toàn trước khi chạy' and no legacy preflight title."""
        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Legacy preflight title removed
        self.assertNotIn("Kiểm tra tiền kiểm tra (Preflight Validation)", html)

        # Clean Vietnamese title present
        self.assertIn("Kiểm tra an toàn trước khi chạy", html)

        # Legacy 'Desktop Worker của bạn' removed from reassurance note
        self.assertNotIn("Desktop Worker của bạn", html)
        self.assertIn("Connector", html)

        # Summary metrics include media preview
        self.assertIn("quickPreviewMedia", html)

    def test_02_campaign_wizard_js_defines_4_essential_cards_and_system_ready(self):
        """2. campaign-wizard.js groups preflight into 4 campaign essentials with System Ready."""
        js_path = REPO_ROOT / "static" / "js" / "campaign-wizard.js"
        self.assertTrue(js_path.exists())
        js_code = js_path.read_text(encoding="utf-8")

        # 4 Essential keys & labels present
        self.assertIn("Nội dung bài đăng", js_code)
        self.assertIn("Danh sách nhóm", js_code)
        self.assertIn("Lịch chạy", js_code)
        self.assertIn("Hệ thống sẵn sàng", js_code)

        # Action link to /settings for connector resolution
        self.assertIn("/settings", js_code)
        self.assertIn("Mở Cài đặt &amp; Connector", js_code)

        # Safety check in executeQuickCampaign
        self.assertIn("_lastPreflightSystemReady", js_code)
        self.assertIn("Chưa thể chạy chiến dịch vì hệ thống chưa sẵn sàng. Hãy kiểm tra Connector.", js_code)

    def test_03_preflight_api_returns_both_internal_checks_and_unified_system_ready(self):
        """3. /api/campaign/preflight preserves 7 checks and adds unified system_ready object."""
        resp = self.client.post("/api/campaign/preflight", json={"account_ids": [], "group_urls": []})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Contract compatibility: 7 checks preserved
        keys = {c["key"] for c in data["checks"]}
        expected_keys = {
            "worker_online", "account_ready", "profile_mapped",
            "session_verified", "groups_valid", "account_busy", "schedule_valid"
        }
        self.assertEqual(keys, expected_keys)

        # Unified system_ready object present
        self.assertIn("system_ready", data)
        sr = data["system_ready"]
        self.assertIn("status", sr)
        self.assertIn("message", sr)
        self.assertIn("can_run", sr)
        self.assertIn("action_url", sr)
        self.assertEqual(sr["action_url"], "/settings")

    def test_04_system_ready_status_fail_without_connector_and_pass_when_connected(self):
        """4. system_ready reports FAIL without connector, and PASS when connector is live."""
        # A. Initially without connector -> FAIL
        resp1 = self.client.post("/api/campaign/preflight", json={"group_urls": ["https://www.facebook.com/groups/simple1"]})
        data1 = resp1.get_json()
        self.assertFalse(data1["can_run"])
        self.assertEqual(data1["system_ready"]["status"], "FAIL")
        self.assertIn("chưa sẵn sàng", data1["system_ready"]["message"].lower())

        # B. Pair device & heartbeat online with FB login -> PASS
        dev_id, token = pair_device(self.client)
        send_heartbeat(self.client, dev_id, token, facebook_logged_in=True, facebook_user_id="100099887766")

        resp2 = self.client.post("/api/campaign/preflight", json={"group_urls": ["https://www.facebook.com/groups/simple1"]})
        data2 = resp2.get_json()
        self.assertTrue(data2["can_run"])
        self.assertEqual(data2["system_ready"]["status"], "PASS")
        self.assertIn("sẵn sàng", data2["system_ready"]["message"].lower())

    def test_05_run_campaign_in_simple_mode_without_connector_returns_clean_error(self):
        """5. /run-campaign blocks without connector with user-friendly message, no technical jargon."""
        resp = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "No Connector Test",
                "content": "Clean error test",
                "selected_groups": ["https://www.facebook.com/groups/simple1"],
            },
        )
        data = resp.get_json()
        self.assertFalse(data.get("success", True))
        err_msg = data.get("error", "")
        self.assertEqual(err_msg, "Chưa thể chạy chiến dịch vì hệ thống chưa sẵn sàng. Hãy kiểm tra Connector.")
        # Ensure no technical jargon
        for jargon in ("worker", "profile", "c_user", "uid", "token", "session_verified"):
            self.assertNotIn(jargon, err_msg.lower())


if __name__ == "__main__":
    unittest.main()
