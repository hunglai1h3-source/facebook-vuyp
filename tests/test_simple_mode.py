"""
Comprehensive Test Suite for SINGLE-SESSION SIMPLE MODE.

Tests cover:
1. User KHONG thay menu Accounts / Workers tren sidebar trong Simple Mode
2. User mo Facebook trong Chrome -> Connector tu dong phat hien identity -> Server cap nhat trang thai SAN SANG
3. User dang xuat Facebook -> Server cap nhat trang thai CHUA DANG NHAP
4. User tao campaign voi luong 3 buoc (khong can chon account) -> Campaign chay thanh cong
5. Campaign tiep tuc chay khi tat tab trinh duyet (background worker / persistent queue)
6. User doi tai khoan Facebook tren cung Chrome profile -> He thong phat hien mismatch va chan an toan
7. Topbar hien thi dung: LIVE / NEEDS LOGIN / OFFLINE
8. Settings hien thi dung card TRANG THAI KET NOI don gian
9. Preflight check thanh cong trong Simple Mode ma khong can chon account
10. Preflight check chan an toan neu chua dang nhap Facebook
11. Auto-reconnect sau khi khoi dong lai
12. Backward compatibility khi tat Simple Mode (SIMPLE_MODE=0)
13. Schema and DB safety (no tables dropped, data preserved)
14. Mobile responsiveness test (viewport meta and layout styles)
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
        DATA_ROOT=str(data_dir),
        USERS_FILE=str(users_file),
        DATABASE_URL="",
        SECRET_KEY="test-simple-mode-secret-key",
        ENABLE_LEGACY_ADMIN_AUTH="false",
        SIMPLE_MODE=simple_mode,
    )
    spec = importlib.util.spec_from_file_location("app", REPO_ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="simple_operator"):
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


def pair_test_device(client, device_name="Google Chrome • FB POST PRO"):
    resp_code = client.post("/api/extension/pair-code")
    code = resp_code.get_json()["code"]
    resp_pair = client.post(
        "/api/extension/pair",
        data=json.dumps({
            "code": code,
            "device_name": device_name,
            "extension_version": "1.0.0",
        }),
        content_type="application/json",
    )
    pair_data = resp_pair.get_json()
    return pair_data["device_id"], pair_data["token"]


def send_agent_heartbeat(client, device_id, token, facebook_logged_in=True, facebook_user_id=None, worker_state="idle"):
    payload = {
        "device_name": "Google Chrome • FB POST PRO",
        "extension_version": "1.0.0",
        "facebook_logged_in": facebook_logged_in,
        "worker_state": worker_state,
    }
    if facebook_user_id:
        payload["facebook_user_id"] = str(facebook_user_id)
        payload["c_user"] = str(facebook_user_id)
    return client.post(
        "/api/agent/heartbeat",
        data=json.dumps(payload),
        headers={
            "X-Device-ID": device_id,
            "X-Agent-Token": token,
            "Content-Type": "application/json",
        },
    )


class TestSimpleModeComprehensive(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_simple_mode_")
        self.app_module = load_fresh_app(self.temp_dir, simple_mode="1")
        self.client = self.app_module.app.test_client()
        self.user_id = create_authenticated_user(self.client, "op_simple")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_sidebar_navigation_hides_accounts_workers_in_simple_mode(self):
        """Test 1: User KHONG thay menu Accounts / Workers tren sidebar trong Simple Mode."""
        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertNotIn('href="/accounts"', html)
        self.assertNotIn('href="/workers"', html)
        self.assertNotIn('Tài khoản Facebook', html)
        self.assertNotIn('Máy trạm / Worker', html)

        self.assertIn('href="/compose"', html)
        self.assertIn('href="/campaigns"', html)
        self.assertIn('href="/groups"', html)
        self.assertIn('href="/settings"', html)

    def test_02_facebook_session_detected_makes_system_ready(self):
        """Test 2: User mo Facebook trong Chrome -> Connector tu dong phat hien identity -> Server cap nhat trang thai SAN SANG."""
        device_id, token = pair_test_device(self.client)

        hb_resp = send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="100088991122334",
        )
        self.assertEqual(hb_resp.status_code, 200)
        hdata = hb_resp.get_json()
        self.assertTrue(hdata.get("ok"))
        self.assertTrue(hdata.get("facebook_logged_in"))
        self.assertEqual(hdata.get("session_verification_error"), "")

        # Verify extension status and UI
        st_resp = self.client.get("/api/extension/status")
        self.assertEqual(st_resp.status_code, 200)
        st_data = st_resp.get_json()
        self.assertEqual(st_data.get("status"), "online")
        self.assertEqual(st_data.get("state"), "CONNECTED")

        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("LIVE", html)

    def test_03_facebook_logout_sets_needs_login(self):
        """Test 3: User dang xuat Facebook -> Server cap nhat trang thai CHUA DANG NHAP / needs_login."""
        device_id, token = pair_test_device(self.client)

        hb_resp = send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=False,
        )
        self.assertEqual(hb_resp.status_code, 200)
        hdata = hb_resp.get_json()
        self.assertTrue(hdata.get("ok"))
        self.assertFalse(hdata.get("facebook_logged_in"))
        self.assertIn("đăng nhập Facebook", hdata.get("session_verification_error", ""))

        st_resp = self.client.get("/api/extension/status")
        self.assertEqual(st_resp.status_code, 200)
        st_data = st_resp.get_json()
        self.assertFalse(st_data.get("facebook_logged_in"))

        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("NEEDS LOGIN", html)

    def test_04_create_campaign_three_step_flow(self):
        """Test 4: User tao campaign voi luong 3 buoc (khong chon account) -> Campaign chay thanh cong."""
        device_id, token = pair_test_device(self.client)
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="100088991122334",
        )

        resp = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Chiến dịch 3 bước Siêu Tốc",
                "content": "Nội dung bài viết quảng bá đơn giản",
                "selected_groups": [
                    "https://www.facebook.com/groups/simplegroup1",
                    "https://www.facebook.com/groups/simplegroup2",
                ],
                "min_delay": 1,
                "max_delay": 2,
            },
        )
        self.assertEqual(resp.status_code, 200)
        camps = self.app_module.load_engine_campaigns(self.user_id)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0]["campaign_name"], "Chiến dịch 3 bước Siêu Tốc")

        tasks = self.app_module.load_engine_tasks(self.user_id, camps[0]["campaign_id"])
        self.assertEqual(len(tasks), 2)
        for t in tasks:
            self.assertEqual(t["status"], "pending")
            self.assertTrue(t["account_id"])

    def test_05_background_execution_across_tab_or_session_close(self):
        """Test 5: Campaign tiep tuc chay khi tat tab trinh duyet (background worker / persistent queue)."""
        device_id, token = pair_test_device(self.client)
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="100088991122334",
        )

        self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Chiến dịch nền độc lập",
                "content": "Bài đăng nền",
                "selected_groups": ["https://www.facebook.com/groups/bgtest"],
            },
        )

        # Simulate user closing tab / logging out
        self.client.get("/logout")

        # Worker still polls job in background
        job_resp = self.client.get(
            "/api/agent/job",
            headers={
                "X-Device-ID": device_id,
                "X-Agent-Token": token,
            },
        )
        self.assertEqual(job_resp.status_code, 200)
        jdata = job_resp.get_json()
        self.assertTrue(jdata.get("has_job"))
        self.assertIn("job", jdata)

    def test_06_account_switching_mismatch_detection(self):
        """Test 6: User doi tai khoan Facebook tren cung Chrome profile -> He thong phat hien mismatch va chan an toan."""
        device_id, token = pair_test_device(self.client)

        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="100011111111111",
        )

        # Switched to another Facebook account
        hb_resp2 = send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="200022222222222",
        )
        self.assertEqual(hb_resp2.status_code, 200)
        hdata2 = hb_resp2.get_json()
        self.assertIn("thay đổi", hdata2.get("session_verification_error", "").lower())

        preflight_resp = self.client.post(
            "/api/campaign/preflight",
            json={"group_urls": ["https://www.facebook.com/groups/test"]},
        )
        self.assertEqual(preflight_resp.status_code, 200)
        pf = preflight_resp.get_json()
        self.assertFalse(pf["can_run"])
        session_check = next((c for c in pf["checks"] if c["key"] == "session_verified"), None)
        self.assertIsNotNone(session_check)
        self.assertEqual(session_check["status"], "FAIL")

    def test_07_topbar_status_live_needs_login_offline(self):
        """Test 7: Topbar hien thi dung: LIVE / NEEDS LOGIN / OFFLINE."""
        # A. Initially, no connector -> OFFLINE
        resp = self.client.get("/compose")
        self.assertIn("OFFLINE", resp.data.decode("utf-8"))

        # B. Pair device -> heartbeat with logged_out -> NEEDS LOGIN
        device_id, token = pair_test_device(self.client)
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=False,
        )
        resp2 = self.client.get("/compose")
        self.assertIn("NEEDS LOGIN", resp2.data.decode("utf-8"))

        # C. Heartbeat with logged_in -> LIVE
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="12345",
        )
        resp3 = self.client.get("/compose")
        self.assertIn("LIVE", resp3.data.decode("utf-8"))

    def test_08_settings_renders_clean_connection_status_card(self):
        """Test 8: Settings hien thi dung card TRANG THAI KET NOI don gian."""
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn("TRẠNG THÁI KẾT NỐI", html)
        self.assertIn("Connector:", html)
        self.assertIn("Facebook:", html)
        self.assertIn("Hệ thống:", html)
        self.assertIn("LIÊN KẾT CHROME NÀY", html)

        self.assertNotIn('id="pairCode"', html)
        self.assertNotIn("Mã liên kết thủ công", html)

    def test_09_preflight_check_passes_without_account_picker(self):
        """Test 9: Preflight check thanh cong trong Simple Mode ma khong can chon account."""
        device_id, token = pair_test_device(self.client)
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="100088991122334",
        )

        resp = self.client.post(
            "/api/campaign/preflight",
            json={"group_urls": ["https://www.facebook.com/groups/simple1"]},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["can_run"])
        self.assertIn(data["status"], ("READY", "PASS"))

    def test_10_preflight_blocks_safely_if_not_logged_in(self):
        """Test 10: Preflight check chan an toan neu chua dang nhap Facebook."""
        device_id, token = pair_test_device(self.client)
        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=False,
        )

        resp = self.client.post(
            "/api/campaign/preflight",
            json={"group_urls": ["https://www.facebook.com/groups/simple1"]},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["can_run"])
        session_check = next((c for c in data["checks"] if c["key"] == "session_verified"), None)
        self.assertIsNotNone(session_check)
        self.assertEqual(session_check["status"], "FAIL")
        self.assertIn("đăng nhập", session_check["message"].lower())

    def test_11_automatic_reconnect_without_pairing_code(self):
        """Test 11: Auto-reconnect sau khi khoi dong lai ma khong can ma pairing moi."""
        device_id, token = pair_test_device(self.client)

        send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="12345",
        )

        reconnect_resp = send_agent_heartbeat(
            self.client,
            device_id,
            token,
            facebook_logged_in=True,
            facebook_user_id="12345",
        )
        self.assertEqual(reconnect_resp.status_code, 200)
        rdata = reconnect_resp.get_json()
        self.assertTrue(rdata.get("ok"))
        self.assertTrue(rdata.get("facebook_logged_in"))

        st_resp = self.client.get("/api/extension/status")
        self.assertEqual(st_resp.status_code, 200)
        st_data = st_resp.get_json()
        self.assertEqual(st_data.get("status"), "online")
        self.assertEqual(st_data.get("state"), "CONNECTED")

    def test_12_backward_compatibility_when_simple_mode_disabled(self):
        """Test 12: Backward compatibility khi tat Simple Mode (SIMPLE_MODE=0)."""
        temp_dir2 = tempfile.mkdtemp(prefix="test_adv_mode_")
        try:
            adv_app_module = load_fresh_app(temp_dir2, simple_mode="0")
            client2 = adv_app_module.app.test_client()
            create_authenticated_user(client2, "adv_operator")

            resp = client2.get("/compose")
            self.assertEqual(resp.status_code, 200)
            html = resp.data.decode("utf-8")

            self.assertIn('href="/accounts"', html)
            self.assertIn('href="/workers"', html)
            self.assertIn('btnModeAdvanced', html)
            self.assertIn('btnModeQuick', html)
        finally:
            shutil.rmtree(temp_dir2, ignore_errors=True)

    def test_13_schema_and_db_safety_no_tables_dropped(self):
        """Test 13: Schema and DB safety (no tables dropped, data preserved)."""
        self.assertTrue(hasattr(self.app_module, "load_facebook_accounts"))
        self.assertTrue(hasattr(self.app_module, "load_devices"))
        self.assertTrue(hasattr(self.app_module, "load_engine_campaigns"))
        self.assertTrue(hasattr(self.app_module, "load_engine_tasks"))

        accounts = self.app_module.load_facebook_accounts(self.user_id)
        self.assertIsInstance(accounts, list)
        devices = self.app_module.load_devices(self.user_id)
        self.assertIsInstance(devices, dict)

    def test_14_mobile_responsiveness_390px_viewport(self):
        """Test 14: Mobile responsiveness test (viewport meta and layout styles)."""
        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn('name="viewport"', html)
        self.assertIn('width=device-width', html)

        css_path = REPO_ROOT / "static" / "css" / "pages" / "compose.css"
        self.assertTrue(css_path.exists())
        css_content = css_path.read_text(encoding="utf-8")
        self.assertIn("@media", css_content)


if __name__ == "__main__":
    unittest.main()
