"""Regression test suite for Phase 12 - Product & UX Improvement."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_fresh_app(temp_dir):
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    users_file = Path(temp_dir) / "users.json"

    os.environ.update(
        APP_ENV="development",
        DATA_ROOT=str(data_dir),
        USERS_FILE=str(users_file),
        DATABASE_URL="",
        SECRET_KEY="phase12-test-secret-key",
        ENABLE_LEGACY_ADMIN_AUTH="false",
    )
    spec = importlib.util.spec_from_file_location("app", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="testuser12"):
    resp = client.post(
        "/register",
        data={
            "display_name": "Test User 12",
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


def provision_account_worker(module, client, user_id, suffix="1"):
    account = module.create_facebook_account(user_id, f"Acc UX {suffix}", "100088992211")
    pair_code = client.post("/api/extension/pair-code").get_json()["code"]
    pair_res = client.post("/api/extension/pair", json={"code": pair_code, "name": f"Worker {suffix}"}).get_json()
    device_id = pair_res["device_id"]
    module.bind_facebook_account_device(user_id, account["account_id"], device_id)
    return account, device_id


class Phase12ProductUxRegression(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase12_test_")
        self.module = load_fresh_app(self.temp_dir)
        self.client = self.module.app.test_client()
        self.user_id = create_authenticated_user(self.client, "ux_tester")

    def test_01_user_dashboard_8_kpi_metrics_and_controls(self):
        """Dashboard renders 8 KPI metrics cards, quick actions, and campaign card."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Verify all 8 KPI metrics exist in the UI
        self.assertIn("Tài khoản Facebook", html)
        self.assertIn("Tổng Groups", html)
        self.assertIn("Tổng chiến dịch", html)
        self.assertIn("Đang chạy", html)
        self.assertIn("Hẹn lịch", html)
        self.assertIn("Đã hoàn tất", html)
        self.assertIn("Sự cố / Lỗi", html)
        self.assertIn("Worker / Hôm nay", html)

        # Verify quick actions toolbar
        self.assertIn("Tạo chiến dịch mới", html)
        self.assertIn("Nhập nhóm mục tiêu", html)
        self.assertIn("Quản lý tài khoản", html)
        self.assertIn("Kết nối Worker", html)
        self.assertIn("Lịch sử chiến dịch", html)

        # Verify dashboard_data helper returns correct keys
        d_data = self.module.dashboard_data(self.user_id)
        for key in ["total_campaigns", "running_campaigns", "scheduled_campaigns", "completed_campaigns", "failed_campaigns", "success_rate", "online_devices"]:
            self.assertIn(key, d_data)
        print("PASS test_01: User Dashboard 8 KPI metrics and quick actions rendered.")

    def test_02_campaign_creation_wizard_and_submission(self):
        """6-step wizard renders with all steps, and campaign submission runs."""
        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Verify 6-step indicators
        self.assertIn("Nội dung", html)
        self.assertIn("Tài khoản", html)
        self.assertIn("Chọn nhóm", html)
        self.assertIn("Phân bổ", html)
        self.assertIn("Hẹn lịch", html)
        self.assertIn("Xác nhận", html)

        # Verify wizard container and controls
        self.assertIn("campaignWizardForm", html)
        self.assertIn("wizard-stepper", html)
        self.assertIn("validation-guard-box", html)
        self.assertIn("BẮT ĐẦU CHIẾN DỊCH", html)

        # Add account, paired worker, and group
        account, _ = provision_account_worker(self.module, self.client, self.user_id, "wiz")
        add_g = self.client.post("/add-group", data={"group_url": "https://www.facebook.com/groups/test-wizard-group"})
        self.assertIn(add_g.status_code, [200, 302])

        # Submit run-campaign directly through form
        run_resp = self.client.post(
            "/run-campaign",
            data={
                "campaign_name": "Wizard Campaign 1",
                "content": "Automated test content for wizard",
                "min_delay": "1",
                "max_delay": "3",
            },
            follow_redirects=False,
        )
        self.assertIn(run_resp.status_code, [302, 200])
        print("PASS test_02: 6-step Campaign Creation Wizard renders and submits properly.")

    def test_03_campaign_templates_crud_api(self):
        """Campaign templates can be created, retrieved, and deleted via API."""
        # 1. Initially empty
        resp = self.client.get("/api/campaign-templates")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["templates"]), 0)

        # 2. Create template
        post_resp = self.client.post(
            "/api/campaign-templates",
            json={
                "name": "Template Bán Hàng",
                "content": "Nội dung mẫu bán hàng chuyên nghiệp",
                "min_delay": 2,
                "max_delay": 5,
                "randomize_content": True,
            },
        )
        self.assertEqual(post_resp.status_code, 201)
        created = post_resp.get_json()["template"]
        self.assertEqual(created["name"], "Template Bán Hàng")
        template_id = created["template_id"]

        # 3. Retrieve list
        resp2 = self.client.get("/api/campaign-templates")
        self.assertEqual(resp2.status_code, 200)
        templates = resp2.get_json()["templates"]
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["template_id"], template_id)

        # 4. Delete template
        del_resp = self.client.delete(f"/api/campaign-templates/{template_id}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.get_json()["success"])

        # 5. Verify empty again
        resp3 = self.client.get("/api/campaign-templates")
        self.assertEqual(len(resp3.get_json()["templates"]), 0)
        print("PASS test_03: Campaign templates CRUD API works flawlessly.")

    def test_04_campaign_clone_creates_draft_without_auto_run(self):
        """Cloning a campaign creates a new draft without auto-executing tasks."""
        account, _ = provision_account_worker(self.module, self.client, self.user_id, "clone")
        groups = ["https://www.facebook.com/groups/clone-group-1", "https://www.facebook.com/groups/clone-group-2"]
        campaign = self.module.create_engine_campaign(
            self.user_id,
            "Original Campaign",
            [{"account_id": account["account_id"], "groups": groups}],
            {"content": "Original content", "min_delay": 1, "max_delay": 3},
            "queued",
        )
        orig_id = campaign["campaign_id"]
        self.module.set_engine_campaign_lifecycle(self.user_id, orig_id, "completed")

        # Clone via POST
        clone_resp = self.client.post(f"/campaigns/{orig_id}/clone", follow_redirects=True)
        self.assertEqual(clone_resp.status_code, 200)

        # Verify duplicated campaign exists
        campaigns_data = self.module.load_engine_campaigns(self.user_id)
        clones = [c for c in campaigns_data if "Bản sao" in c.get("campaign_name", "")]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        # Must be in draft or queued, not running or executing
        self.assertEqual(clone["lifecycle"], "draft")
        self.assertEqual(clone["payload"]["content"], "Original content")
        self.assertNotEqual(clone["campaign_id"], orig_id)
        print("PASS test_04: Campaign cloning creates persistent draft without auto-running.")

    def test_05_campaign_history_and_detail_view(self):
        """Campaign list with filters and detail view with task breakdown render correctly."""
        account, _ = provision_account_worker(self.module, self.client, self.user_id, "detail")
        groups = ["https://www.facebook.com/groups/detail-1"]
        campaign = self.module.create_engine_campaign(
            self.user_id,
            "Detail Campaign Test",
            [{"account_id": account["account_id"], "groups": groups}],
            {"content": "Test content", "min_delay": 1, "max_delay": 2},
            "queued",
        )
        c_id = campaign["campaign_id"]
        self.module.set_engine_campaign_lifecycle(self.user_id, c_id, "running")

        # 1. Campaign list view with filters
        resp = self.client.get(f"/campaigns?q=Detail&status=running&sort=desc")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Detail Campaign Test", html)
        self.assertIn("Đang chạy", html)

        # 2. Campaign detail view
        detail_resp = self.client.get(f"/campaigns/{c_id}")
        self.assertEqual(detail_resp.status_code, 200)
        d_html = detail_resp.get_data(as_text=True)
        self.assertIn("Chi tiết chiến dịch", d_html)
        self.assertIn("Danh sách bài đăng theo Group", d_html)
        self.assertIn(groups[0], d_html)
        self.assertIn("Acc UX detail", d_html)
        print("PASS test_05: Campaign history list with filters and detail view rendered.")

    def test_06_user_friendly_error_translation(self):
        """translate_user_friendly_error maps raw technical errors to Vietnamese."""
        fn = self.module.translate_user_friendly_error
        self.assertIn("cơ sở dữ liệu", fn("OperationalError: connection refused"))
        self.assertIn("ngoại tuyến", fn("Worker offline: device_offline"))
        self.assertIn("liên kết", fn("unmapped_account: device mapping mismatch"))
        self.assertIn("chiến dịch khác", fn("account_busy: session_locked"))
        self.assertIn("tồn tại", fn("duplicate key value violates unique constraint"))
        self.assertIn("quá nhanh", fn("Rate limit exceeded"))
        self.assertIn("quản trị viên", fn("approval_needed: pending_admin_approval"))
        self.assertIn("chưa tham gia", fn("not a member: join group first"))
        self.assertIn("hết hạn", fn("Facebook session expired / login required"))
        self.assertIn("giới hạn", fn("quota exceeded"))
        # Empty error
        self.assertEqual(fn(""), "Lỗi không xác định.")
        print("PASS test_06: User-friendly error translation helper verified.")

    def test_07_dedicated_accounts_and_workers_views(self):
        """Dedicated /accounts and /workers pages render with expected actions."""
        # 1. Accounts page
        acc_resp = self.client.get("/accounts")
        self.assertEqual(acc_resp.status_code, 200)
        a_html = acc_resp.get_data(as_text=True)
        self.assertIn("Tài khoản Facebook", a_html)
        self.assertIn("Thêm tài khoản Facebook", a_html)
        self.assertIn("Xác minh session", a_html)

        # 2. Workers page
        worker_resp = self.client.get("/workers")
        self.assertEqual(worker_resp.status_code, 200)
        w_html = worker_resp.get_data(as_text=True)
        self.assertIn("Máy trạm", w_html)
        self.assertIn("Ghép nối Chrome Extension", w_html)
        self.assertIn("Offline", w_html)
        print("PASS test_07: Dedicated /accounts and /workers pages render with 200 OK.")

    def test_08_mobile_remote_control_json_and_dynamic_redirect(self):
        """Pause/Resume/Stop actions support both JSON API and dynamic redirect."""
        account, _ = provision_account_worker(self.module, self.client, self.user_id, "ctrl")
        groups = ["https://www.facebook.com/groups/ctrl-group"]
        campaign = self.module.create_engine_campaign(
            self.user_id,
            "Control Campaign",
            [{"account_id": account["account_id"], "groups": groups}],
            {"content": "Content", "min_delay": 1, "max_delay": 2},
            "queued",
        )
        c_id = campaign["campaign_id"]
        self.module.set_engine_campaign_lifecycle(self.user_id, c_id, "running")

        # Pause with next redirect
        pause_resp = self.client.post("/pause-campaign", data={"next": f"/campaigns/{c_id}"})
        self.assertEqual(pause_resp.status_code, 302)
        self.assertIn(f"/campaigns/{c_id}", pause_resp.headers["Location"])

        # Resume with JSON header
        resume_resp = self.client.post("/resume-campaign", headers={"Accept": "application/json"})
        self.assertEqual(resume_resp.status_code, 200)
        self.assertTrue(resume_resp.get_json()["success"])

        # Stop with next redirect
        stop_resp = self.client.post("/stop-campaign", data={"next": "/"})
        self.assertEqual(stop_resp.status_code, 302)
        self.assertEqual(stop_resp.headers["Location"], "/")
        print("PASS test_08: Mobile remote control actions support dynamic redirect & JSON.")

    def test_09_in_app_notification_center(self):
        """Notification center stores notifications, retrieves unread, and marks read."""
        # 1. Initially empty
        resp = self.client.get("/api/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["unread_count"], 0)

        # 2. Create notifications
        n1 = self.module.create_notification(self.user_id, "info", "Chiến dịch bắt đầu", "Chiến dịch A đang chạy")
        n2 = self.module.create_notification(self.user_id, "success", "Hoàn tất chiến dịch", "Chiến dịch A đã hoàn tất")

        # 3. Fetch notifications
        resp2 = self.client.get("/api/notifications")
        self.assertEqual(resp2.status_code, 200)
        d2 = resp2.get_json()
        self.assertEqual(d2["unread_count"], 2)
        self.assertEqual(len(d2["notifications"]), 2)

        # 4. Mark one read
        mark_resp = self.client.post(f"/api/notifications/{n1}/read")
        self.assertEqual(mark_resp.status_code, 200)
        self.assertTrue(mark_resp.get_json()["success"])

        # 5. Check unread count is 1
        resp3 = self.client.get("/api/notifications")
        self.assertEqual(resp3.get_json()["unread_count"], 1)

        # 6. Mark all read
        mark_all_resp = self.client.post("/api/notifications/read-all")
        self.assertEqual(mark_all_resp.status_code, 200)
        self.assertTrue(mark_all_resp.get_json()["success"])

        # 7. Check unread count is 0
        resp4 = self.client.get("/api/notifications")
        self.assertEqual(resp4.get_json()["unread_count"], 0)
        print("PASS test_09: In-app Notification Center lifecycle verified.")

    def test_10_campaign_terminal_sync_triggers_notification(self):
        """When campaign transitions to completed/failed, a notification is generated."""
        account, _ = provision_account_worker(self.module, self.client, self.user_id, "term")
        groups = ["https://www.facebook.com/groups/term-1"]
        campaign = self.module.create_engine_campaign(
            self.user_id,
            "Terminal Notif Test",
            [{"account_id": account["account_id"], "groups": groups}],
            {"content": "Content", "min_delay": 1, "max_delay": 2},
            "queued",
        )
        c_id = campaign["campaign_id"]
        self.module.set_engine_campaign_lifecycle(self.user_id, c_id, "running")

        # Complete the task
        tasks = self.module.load_engine_tasks(self.user_id, c_id)
        self.assertEqual(len(tasks), 1)
        for t in tasks:
            t["status"] = "successful"
        self.module._save_local_engine(self.user_id, tasks=tasks)

        # Sync campaign state
        updated = self.module.sync_engine_campaign_state(self.user_id, c_id)
        self.assertEqual(updated["lifecycle"], "completed")

        # Verify a notification was created
        notifs = self.module.load_notifications(self.user_id)
        found = [n for n in notifs if "Terminal Notif Test" in n.get("title", "") or "hoàn tất" in n.get("title", "").lower() or "hoàn tất" in n.get("message", "").lower()]
        self.assertTrue(len(found) >= 1)
        print("PASS test_10: Campaign terminal sync triggers in-app notification.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
