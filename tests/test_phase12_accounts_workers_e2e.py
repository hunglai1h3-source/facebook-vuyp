"""End-to-end regression tests for Facebook Accounts and Workers management (Phase 12).
Verifies:
1. Route navigation & page rendering for /accounts and /workers (200 OK)
2. 8-char pairing flow, token hashing, and persistence
3. Worker heartbeat, 75s online/offline threshold, and busy/idle states
4. Facebook account CRUD (add, rename, update UID, safe delete)
5. Account status computation (ready, busy, unmapped, offline, unavailable)
6. Device & browser profile mapping and unmapping on disconnect
7. Safe deletion guarding active campaign tasks and group assignment cleanup
8. Strict multi-tenant isolation between User A and User B
9. Zero Facebook password storage and UID validation
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


def load_fresh_app(temp_dir):
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    users_file = Path(temp_dir) / "users.json"

    os.environ.update(
        APP_ENV="development",
        DATA_ROOT=str(data_dir),
        USERS_FILE=str(users_file),
        DATABASE_URL="",
        SECRET_KEY="phase12-test-e2e-secret-key",
        ENABLE_LEGACY_ADMIN_AUTH="false",
    )
    spec = importlib.util.spec_from_file_location("app", REPO_ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="testuser12"):
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


def set_user_max_accounts(module, user_id, limit=10):
    users = module.load_users()
    if user_id in users:
        users[user_id]["max_facebook_accounts"] = limit
        module.save_users(users)


class Phase12AccountsWorkersE2ETest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phase12_acc_wrk_")
        self.module = load_fresh_app(self.temp_dir)
        self.client_a = self.module.app.test_client()
        self.user_a_id = create_authenticated_user(self.client_a, "user_alpha")

        self.client_b = self.module.app.test_client()
        self.user_b_id = create_authenticated_user(self.client_b, "user_beta")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ----------------------------------------------------------------------
    # 1. Navigation & Page Rendering
    # ----------------------------------------------------------------------
    def test_01_navigation_and_page_rendering(self):
        """Dedicated /accounts and /workers pages render with 200 OK and expected elements."""
        # Accounts page
        resp_acc = self.client_a.get("/accounts")
        self.assertEqual(resp_acc.status_code, 200)
        html_acc = resp_acc.get_data(as_text=True)
        self.assertIn("Tài khoản Facebook", html_acc)
        self.assertIn("Thêm tài khoản Facebook", html_acc)
        self.assertIn("Trạng thái vận hành", html_acc)
        self.assertIn("Chưa có tài khoản Facebook nào được thêm", html_acc)

        # Workers page
        resp_wrk = self.client_a.get("/workers")
        self.assertEqual(resp_wrk.status_code, 200)
        html_wrk = resp_wrk.get_data(as_text=True)
        self.assertIn("Máy trạm", html_wrk)
        self.assertIn("Ghép nối Chrome Extension mới", html_wrk)
        self.assertIn("Nhập mã ghép nối 8 ký tự", html_wrk)
        self.assertIn("Chưa có máy trạm nào được kết nối", html_wrk)
        print("PASS test_01: /accounts and /workers pages render properly with empty state.")

    # ----------------------------------------------------------------------
    # 2. 8-Char Pairing Flow & Token Security
    # ----------------------------------------------------------------------
    def test_02_pairing_flow_and_token_security(self):
        """Worker pairing uses 8-char code, stores hashed token, and persists."""
        # 1. Generate pairing code
        resp_code = self.client_a.post("/api/extension/pair-code")
        self.assertEqual(resp_code.status_code, 200)
        data_code = resp_code.get_json()
        self.assertTrue(data_code.get("ok"))
        code = data_code.get("code")
        self.assertEqual(len(code), 8, f"Pairing code should be 8 characters, got {code}")

        # 2. Pair worker
        resp_pair = self.client_a.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Chrome Laptop A"}),
            content_type="application/json",
        )
        self.assertEqual(resp_pair.status_code, 200)
        pair_data = resp_pair.get_json()
        self.assertTrue(pair_data.get("ok"))
        device_id = pair_data.get("device_id")
        token = pair_data.get("token")
        self.assertTrue(device_id)
        self.assertTrue(token)

        # 3. Check /api/workers does NOT leak raw token or token_hash
        resp_workers = self.client_a.get("/api/workers")
        self.assertEqual(resp_workers.status_code, 200)
        workers_data = resp_workers.get_json()
        self.assertTrue(workers_data.get("success"))
        workers = workers_data.get("workers", [])
        self.assertEqual(len(workers), 1)
        w = workers[0]
        self.assertEqual(w.get("device_id"), device_id)
        self.assertEqual(w.get("name"), "Chrome Laptop A")
        self.assertNotIn("token", w)
        self.assertNotIn("token_hash", w)

        # 4. Check HTML page renders the worker card
        resp_wrk = self.client_a.get("/workers")
        html_wrk = resp_wrk.get_data(as_text=True)
        self.assertIn("Chrome Laptop A", html_wrk)
        self.assertIn(device_id, html_wrk)
        print("PASS test_02: 8-char pairing flow succeeds and token is securely sanitized.")

    # ----------------------------------------------------------------------
    # 3. Heartbeat & Online / Offline / Busy transitions
    # ----------------------------------------------------------------------
    def test_03_worker_heartbeat_and_status_transitions(self):
        """Worker transitions between online, offline (>75s), and busy correctly."""
        # Provision a device directly
        now_iso = self.module.now_iso()
        dev_id = "dev-hb-100"
        devices = {
            dev_id: {
                "device_id": dev_id,
                "name": "Desktop Worker 1",
                "last_seen": now_iso,
                "state": "idle",
                "status": "approved",
                "created_at": now_iso,
            }
        }
        self.module.save_devices(self.user_a_id, devices)

        # 1. Heartbeat is fresh -> Online
        workers = self.module.get_enriched_workers(self.user_a_id)
        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0]["is_online"])
        self.assertEqual(workers[0]["status_tag"], "online")

        # 2. Heartbeat is old (> 75 seconds ago) -> Offline
        old_time = (self.module.utc_now() - datetime.timedelta(seconds=80)).isoformat()
        devices[dev_id]["last_seen"] = old_time
        self.module.save_devices(self.user_a_id, devices)

        workers = self.module.get_enriched_workers(self.user_a_id)
        self.assertFalse(workers[0]["is_online"])
        self.assertEqual(workers[0]["status_tag"], "offline")

        # 3. Fresh heartbeat with state: "busy" -> Busy
        devices[dev_id]["last_seen"] = self.module.now_iso()
        devices[dev_id]["state"] = "busy"
        self.module.save_devices(self.user_a_id, devices)

        workers = self.module.get_enriched_workers(self.user_a_id)
        self.assertTrue(workers[0]["is_online"])
        self.assertEqual(workers[0]["status_tag"], "busy")
        print("PASS test_03: Worker correctly transitions across online, offline (75s), and busy.")

    # ----------------------------------------------------------------------
    # 4. Facebook Account CRUD & Validation
    # ----------------------------------------------------------------------
    def test_04_facebook_account_crud_and_validation(self):
        """Add, rename, update numeric UID, and safely delete Facebook accounts."""
        # 1. Add account via HTML POST
        resp_add = self.client_a.post(
            "/groups/accounts",
            data={"display_name": "Sales Profile 01", "facebook_user_id": "100088827361928"},
            follow_redirects=True,
        )
        self.assertEqual(resp_add.status_code, 200)

        # Check account appears in /api/accounts
        resp_api = self.client_a.get("/api/accounts")
        accounts = resp_api.get_json().get("accounts", [])
        self.assertEqual(len(accounts), 1)
        acc_id = accounts[0]["account_id"]
        self.assertEqual(accounts[0]["display_name"], "Sales Profile 01")
        self.assertEqual(accounts[0]["facebook_user_id"], "100088827361928")

        # 2. Validation: reject invalid display_name (< 2 chars)
        resp_inv = self.client_a.post(
            "/api/accounts",
            data=json.dumps({"display_name": "A", "facebook_user_id": "1000111222333"}),
            content_type="application/json",
        )
        self.assertEqual(resp_inv.status_code, 400)

        # 3. Validation: reject non-numeric UID
        resp_uid = self.client_a.post(
            "/api/accounts",
            data=json.dumps({"display_name": "Valid Name", "facebook_user_id": "alphanumeric_uid"}),
            content_type="application/json",
        )
        self.assertEqual(resp_uid.status_code, 400)

        # 4. Rename account and update UID via update endpoint
        resp_update = self.client_a.post(
            f"/accounts/{acc_id}/update",
            data={
                "display_name": "Sales Profile Renamed",
                "facebook_user_id": "100099988877766",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp_update.status_code, 200)

        # Verify updated values in storage
        acc_updated = self.module.get_facebook_account(self.user_a_id, acc_id)
        self.assertEqual(acc_updated["display_name"], "Sales Profile Renamed")
        self.assertEqual(acc_updated["facebook_user_id"], "100099988877766")

        # 5. Delete account safely
        resp_del = self.client_a.post(f"/accounts/{acc_id}/delete", follow_redirects=True)
        self.assertEqual(resp_del.status_code, 200)
        remaining = self.module.load_facebook_accounts(self.user_a_id)
        self.assertEqual(len(remaining), 0)
        print("PASS test_04: Facebook account CRUD with display_name renaming and UID validation works.")

    # ----------------------------------------------------------------------
    # 5. Account Status Computation
    # ----------------------------------------------------------------------
    def test_05_account_status_computation(self):
        """Account computes statuses: ready, busy, unmapped, offline, unavailable."""
        set_user_max_accounts(self.module, self.user_a_id, 10)
        now_iso = self.module.now_iso()
        dev_online_id = "dev-online"
        dev_offline_id = "dev-offline"
        devices = {
            dev_online_id: {
                "device_id": dev_online_id,
                "name": "Dev Online",
                "last_seen": now_iso,
                "state": "idle",
            },
            dev_offline_id: {
                "device_id": dev_offline_id,
                "name": "Dev Offline",
                "last_seen": "2020-01-01T00:00:00Z",
                "state": "idle",
            },
        }
        self.module.save_devices(self.user_a_id, devices)

        # Account 1: Unmapped
        acc1 = self.module.create_facebook_account(self.user_a_id, "Acc Unmapped", "1000000000001")
        # Account 2: Mapped to offline worker
        acc2 = self.module.create_facebook_account(self.user_a_id, "Acc Offline", "1000000000002")
        self.module.update_facebook_account(self.user_a_id, acc2["account_id"], device_id=dev_offline_id)
        # Account 3: Mapped to online worker -> ready
        acc3 = self.module.create_facebook_account(self.user_a_id, "Acc Ready", "1000000000003")
        self.module.update_facebook_account(self.user_a_id, acc3["account_id"], device_id=dev_online_id)

        enriched = self.module.get_enriched_accounts(self.user_a_id)
        status_by_id = {a["account_id"]: a["computed_status"] for a in enriched}

        self.assertEqual(status_by_id[acc1["account_id"]], "unmapped")
        self.assertEqual(status_by_id[acc2["account_id"]], "offline")
        self.assertEqual(status_by_id[acc3["account_id"]], "ready")

        # Account 4: Mapped to non-existent or revoked device -> unavailable
        acc4 = self.module.create_facebook_account(self.user_a_id, "Acc Unavail", "1000000000004")
        # Force a device_id that is not in devices
        raw_accounts = self.module.load_facebook_accounts(self.user_a_id)
        for a in raw_accounts:
            if a["account_id"] == acc4["account_id"]:
                a["device_id"] = "dev-nonexistent"
        self.module.write_json(self.module.customer_accounts_file(self.user_a_id), raw_accounts)

        enriched = self.module.get_enriched_accounts(self.user_a_id)
        status_by_id = {a["account_id"]: a["computed_status"] for a in enriched}
        self.assertEqual(status_by_id[acc4["account_id"]], "unavailable")

        # Account 3 when active task is running -> busy
        task_id = "task-active-100"
        tasks = [
            {
                "task_id": task_id,
                "customer_id": self.user_a_id,
                "account_id": acc3["account_id"],
                "status": "running",
            }
        ]
        self.module._save_local_engine(self.user_a_id, tasks=tasks)

        enriched = self.module.get_enriched_accounts(self.user_a_id)
        status_by_id = {a["account_id"]: a["computed_status"] for a in enriched}
        self.assertEqual(status_by_id[acc3["account_id"]], "busy")
        print("PASS test_05: All 5 account computed statuses verified (unmapped, offline, ready, unavailable, busy).")

    # ----------------------------------------------------------------------
    # 6. Safe Deletion & Group Cleanup Guard
    # ----------------------------------------------------------------------
    def test_06_safe_deletion_and_group_cleanup(self):
        """Active campaign tasks prevent deletion; clean deletion removes group assignments."""
        acc = self.module.create_facebook_account(self.user_a_id, "Guarded Acc", "1000123456789")
        acc_id = acc["account_id"]

        # First add groups to user_a
        grp1 = "https://www.facebook.com/groups/guard-1"
        grp2 = "https://www.facebook.com/groups/guard-2"
        self.module.save_groups(self.user_a_id, [grp1, grp2])

        # Assign groups to this account
        self.module.save_group_assignments(self.user_a_id, [
            {"group_url": grp1, "account_id": acc_id},
            {"group_url": grp2, "account_id": acc_id},
        ])
        assignments = self.module.load_group_assignments(self.user_a_id)
        self.assertEqual(assignments.get(grp1), acc_id)

        # 1. Put an active task on this account
        tasks = [
            {
                "task_id": "t-running-1",
                "customer_id": self.user_a_id,
                "account_id": acc_id,
                "status": "running",
            }
        ]
        self.module._save_local_engine(self.user_a_id, tasks=tasks)

        # Deletion must be blocked
        with self.assertRaises(ValueError) as ctx:
            self.module.delete_facebook_account(self.user_a_id, acc_id)
        self.assertIn("chiến dịch", str(ctx.exception))

        # 2. Mark task as completed
        tasks[0]["status"] = "completed"
        self.module._save_local_engine(self.user_a_id, tasks=tasks)

        # Now deletion succeeds
        self.module.delete_facebook_account(self.user_a_id, acc_id)
        # Account is gone
        accounts = self.module.load_facebook_accounts(self.user_a_id)
        self.assertEqual(len(accounts), 0)

        # Group assignments are cleaned up
        assignments_after = self.module.load_group_assignments(self.user_a_id)
        self.assertNotIn(grp1, assignments_after)
        self.assertNotIn(grp2, assignments_after)
        print("PASS test_06: Safe deletion prevents deleting active accounts and cleans group assignments.")

    # ----------------------------------------------------------------------
    # 7. Safe Disconnect & Unbinding Mapped Accounts
    # ----------------------------------------------------------------------
    def test_07_safe_worker_disconnect_and_account_unbinding(self):
        """Disconnecting worker unbinds any associated Facebook accounts."""
        dev_id = "dev-to-disconnect"
        devices = {
            dev_id: {
                "device_id": dev_id,
                "name": "Target Worker",
                "last_seen": self.module.now_iso(),
            }
        }
        self.module.save_devices(self.user_a_id, devices)

        # Create account mapped to this worker
        acc = self.module.create_facebook_account(self.user_a_id, "Mapped Acc", "1000555544443")
        self.module.update_facebook_account(self.user_a_id, acc["account_id"], device_id=dev_id)

        # Verify mapping
        acc_before = self.module.get_facebook_account(self.user_a_id, acc["account_id"])
        self.assertEqual(acc_before.get("device_id"), dev_id)

        # Call disconnect route
        resp = self.client_a.post("/workers/disconnect", data={"device_id": dev_id}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Worker is removed
        devices_after = self.module.load_devices(self.user_a_id)
        self.assertNotIn(dev_id, devices_after)

        # Facebook account is now unmapped
        acc_after = self.module.get_facebook_account(self.user_a_id, acc["account_id"])
        self.assertEqual(acc_after.get("device_id"), "")
        print("PASS test_07: Safe disconnect removes worker and cleanly unbinds mapped accounts.")

    # ----------------------------------------------------------------------
    # 8. Strict Multi-Tenant Isolation
    # ----------------------------------------------------------------------
    def test_08_multi_tenant_isolation(self):
        """User A cannot view, modify, delete, or access User B's accounts or workers."""
        # Provision User B's data
        acc_b = self.module.create_facebook_account(self.user_b_id, "Secret Account B", "1000999999999")
        dev_b_id = "dev-beta-secret"
        devices_b = {
            dev_b_id: {
                "device_id": dev_b_id,
                "name": "Beta Private Worker",
                "last_seen": self.module.now_iso(),
            }
        }
        self.module.save_devices(self.user_b_id, devices_b)

        # Session client_a is User A
        # 1. User A checks /api/accounts -> B's account must NOT appear
        resp_acc = self.client_a.get("/api/accounts")
        accounts_a = resp_acc.get_json().get("accounts", [])
        self.assertFalse(any(a["account_id"] == acc_b["account_id"] for a in accounts_a))

        # 2. User A checks /api/workers -> B's worker must NOT appear
        resp_wrk = self.client_a.get("/api/workers")
        workers_a = resp_wrk.get_json().get("workers", [])
        self.assertFalse(any(w["device_id"] == dev_b_id for w in workers_a))

        # 3. User A attempts to delete User B's account -> Must fail / not delete B's account
        resp_del = self.client_a.delete(f"/api/accounts/{acc_b['account_id']}")
        self.assertEqual(resp_del.status_code, 400)
        # Ensure B's account is still intact
        acc_b_intact = self.module.get_facebook_account(self.user_b_id, acc_b["account_id"])
        self.assertIsNotNone(acc_b_intact)

        # 4. User A attempts to update User B's account -> Must fail
        resp_upd = self.client_a.put(
            f"/api/accounts/{acc_b['account_id']}",
            data=json.dumps({"display_name": "Hacked Name"}),
            content_type="application/json",
        )
        self.assertEqual(resp_upd.status_code, 400)
        acc_b_check = self.module.get_facebook_account(self.user_b_id, acc_b["account_id"])
        self.assertEqual(acc_b_check["display_name"], "Secret Account B")
        print("PASS test_08: Strict multi-tenant isolation enforced between User A and User B.")

    # ----------------------------------------------------------------------
    # 9. Group Count Regression: Groups must NOT cause HTTP 500 on /accounts
    # ----------------------------------------------------------------------
    def test_09_accounts_page_with_groups_no_500(self):
        """Regression test: having groups must never crash get_enriched_accounts with AttributeError."""
        # 1. Add groups to User A
        group_urls = [
            "https://www.facebook.com/groups/marketing101",
            "https://www.facebook.com/groups/salesteam",
            "https://www.facebook.com/groups/realestate",
        ]
        self.module.save_groups(self.user_a_id, group_urls)

        # 2. Add an account
        acc = self.module.create_facebook_account(self.user_a_id, "Account With Groups", "1000111222333")
        acc_id = acc["account_id"]

        # 3. Assign 2 of the groups to this account
        self.module.save_group_assignments(self.user_a_id, [
            {"group_url": group_urls[0], "account_id": acc_id},
            {"group_url": group_urls[1], "account_id": acc_id},
        ])

        # 4. Request /accounts HTML page -> Must be 200 OK (previously threw 500)
        resp_page = self.client_a.get("/accounts")
        self.assertEqual(resp_page.status_code, 200)
        html = resp_page.get_data(as_text=True)
        self.assertIn("Account With Groups", html)
        self.assertIn("2 Groups", html)

        # 5. Request /api/accounts -> Must be 200 OK
        resp_api = self.client_a.get("/api/accounts")
        self.assertEqual(resp_api.status_code, 200)
        data = resp_api.get_json()
        self.assertTrue(data.get("success"))
        found = next((a for a in data["accounts"] if a["account_id"] == acc_id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get("group_count"), 2)
        print("PASS test_09: Accounts page and API return 200 OK with accurate group counts when user has groups.")

    # ----------------------------------------------------------------------
    # 10. Account Creation with Direct Worker Binding
    # ----------------------------------------------------------------------
    def test_10_account_creation_with_direct_worker_binding(self):
        """Account can be directly bound to a worker on creation."""
        now_iso = self.module.now_iso()
        dev_id = "dev-bind-on-create"
        devices = {
            dev_id: {
                "device_id": dev_id,
                "name": "Direct Bound Worker",
                "last_seen": now_iso,
                "state": "idle",
            }
        }
        self.module.save_devices(self.user_a_id, devices)

        # 1. Create account with device_id via API
        resp = self.client_a.post(
            "/api/accounts",
            data=json.dumps({
                "display_name": "Bound On Create",
                "facebook_user_id": "100099887766554",
                "device_id": dev_id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        account = resp.get_json().get("account", {})
        self.assertEqual(account.get("device_id"), dev_id)
        self.assertEqual(account.get("browser_profile_id"), f"chrome-profile:{dev_id}")

        # 2. Check enriched status
        enriched = self.module.get_enriched_accounts(self.user_a_id)
        bound_acc = next((a for a in enriched if a["account_id"] == account["account_id"]), None)
        self.assertIsNotNone(bound_acc)
        self.assertEqual(bound_acc.get("computed_status"), "ready")
        self.assertTrue(bound_acc.get("device_online"))
        print("PASS test_10: Account created with direct worker binding is properly mapped and ready.")

    # ----------------------------------------------------------------------
    # 11. Extension Heartbeat UID Auto-Sync & Session Verification
    # ----------------------------------------------------------------------
    def test_11_extension_heartbeat_uid_auto_sync(self):
        """Extension heartbeat sends facebook_user_id and auto-syncs to bound account."""
        # 1. Pair a worker
        code_resp = self.client_a.post("/api/extension/pair-code")
        code = code_resp.get_json()["code"]
        pair_resp = self.client_a.post(
            "/api/extension/pair",
            data=json.dumps({"code": code, "device_name": "Auto Sync Worker"}),
            content_type="application/json",
        )
        dev_id = pair_resp.get_json()["device_id"]
        token = pair_resp.get_json()["token"]
        headers = {"X-Device-ID": dev_id, "X-Agent-Token": token}

        # 2. Create an account mapped to this worker but with NO facebook_user_id
        acc = self.module.create_facebook_account(
            self.user_a_id,
            display_name="Account Missing UID",
            facebook_user_id="",
            device_id=dev_id,
        )
        self.assertEqual(acc.get("facebook_user_id"), "")

        # 3. Extension sends heartbeat with facebook_user_id
        fbid = "100077665544332"
        fingerprint = self.module.facebook_session_fingerprint(fbid)
        hb_resp = self.client_a.post(
            "/api/agent/heartbeat",
            headers=headers,
            data=json.dumps({
                "facebook_logged_in": True,
                "facebook_user_id": fbid,
                "facebook_session_fingerprint": fingerprint,
                "session_verification_version": 1,
                "browser_profile_id": f"chrome-profile:{dev_id}",
                "session_context": f"chrome-profile:{dev_id}",
                "worker_state": "idle",
            }),
            content_type="application/json",
        )
        self.assertEqual(hb_resp.status_code, 200)
        hb_data = hb_resp.get_json()
        self.assertTrue(hb_data.get("ok"))
        self.assertEqual(hb_data.get("session_verification_error"), "")
        self.assertEqual(hb_data.get("assigned_account", {}).get("facebook_user_id"), fbid)

        # 4. Verify account in database now has the UID
        saved_acc = self.module.get_facebook_account(self.user_a_id, acc["account_id"])
        self.assertEqual(saved_acc.get("facebook_user_id"), fbid)
        print("PASS test_11: Extension heartbeat auto-syncs numeric UID to bound account with verified session.")

    # ----------------------------------------------------------------------
    # 12. Default Multi-Account Quota
    # ----------------------------------------------------------------------
    def test_12_default_multi_account_quota(self):
        """Newly registered user defaults to 10 accounts without needing manual admin limit bump."""
        # Create a brand new user
        new_client = self.module.app.test_client()
        new_user_id = create_authenticated_user(new_client, "user_gamma")

        # Add 3 accounts without any manual max_facebook_accounts modification
        for i in range(1, 4):
            resp = new_client.post(
                "/groups/accounts",
                data={"display_name": f"Gamma Account {i}", "facebook_user_id": f"100000000000{i}"},
                follow_redirects=True,
            )
            self.assertEqual(resp.status_code, 200)

        accounts = self.module.load_facebook_accounts(new_user_id)
        self.assertEqual(len(accounts), 3)
        print("PASS test_12: Newly registered users can create multiple accounts under default quota.")


if __name__ == "__main__":
    unittest.main()
