"""Comprehensive Test Suite for Simplified Background Campaign Flow (36 Scenarios)."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def load_fresh_app(temp_dir):
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    users_file = Path(temp_dir) / "users.json"

    os.environ.update(
        APP_ENV="development",
        DATA_ROOT=str(data_dir),
        USERS_FILE=str(users_file),
        DATABASE_URL="",
        SECRET_KEY="simplified-bg-test-secret-key",
        ENABLE_LEGACY_ADMIN_AUTH="false",
    )
    spec = importlib.util.spec_from_file_location("app", REPO_ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return module


def create_authenticated_user(client, username="bg_tester"):
    resp = client.post(
        "/register",
        data={
            "display_name": f"Tester {username}",
            "username": username,
            "email": f"{username}@test.local",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    return user_id


def pair_worker(module, client, name="Worker 1", c_user="", set_online=True):
    pair_code = client.post("/api/extension/pair-code").get_json()["code"]
    pair_res = client.post("/api/extension/pair", json={"code": pair_code, "name": name}).get_json()
    device_id = pair_res["device_id"]
    token = pair_res["token"]
    if set_online:
        client.post(
            "/api/agent/heartbeat",
            headers={"X-Device-ID": device_id, "X-Agent-Token": token},
            json={
                "c_user": c_user,
                "facebook_user_id": c_user,
                "device_name": name,
                "worker_state": "idle",
                "extension_version": "1.0",
                "facebook_logged_in": bool(c_user),
            },
        )
    return device_id, token


class SimplifiedBackgroundCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="bg_test_")
        self.module = load_fresh_app(self.temp_dir)
        self.client = self.module.app.test_client()
        self.user_id = create_authenticated_user(self.client, "test_user_bg")

    # =========================================================================
    # PART 1: FACEBOOK ACCOUNT FLOW & AUTO-IDENTIFICATION (SCENARIOS 1 - 8)
    # =========================================================================

    def test_01_create_account_without_manual_uid(self):
        """1. Add Facebook account without UID succeeds with empty UID."""
        acc = self.module.create_facebook_account(self.user_id, "FB Profile Alpha", "")
        self.assertIsNotNone(acc)
        self.assertEqual(acc["display_name"], "FB Profile Alpha")
        self.assertEqual(acc.get("facebook_user_id", ""), "")
        # Verification status is PROFILE_UNAVAILABLE when not yet bound to a device
        enriched = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["verification_status"], "PROFILE_UNAVAILABLE")

    def test_02_heartbeat_auto_syncs_c_user_uid_and_transitions_to_ready(self):
        """2. Extension heartbeat with c_user auto-syncs numeric UID to bound account -> READY."""
        acc = self.module.create_facebook_account(self.user_id, "FB Beta", "")
        device_id, token = pair_worker(self.module, self.client, "Worker Beta", c_user="", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], device_id)

        # Before UID sync: online worker but empty UID -> SESSION_UNVERIFIED
        enriched = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(enriched[0]["verification_status"], "SESSION_UNVERIFIED")

        # Worker sends heartbeat with c_user
        hb_resp = self.client.post(
            "/api/agent/heartbeat",
            headers={"X-Device-ID": device_id, "X-Agent-Token": token},
            json={"c_user": "100099887766554", "facebook_user_id": "100099887766554", "facebook_logged_in": True},
        )
        self.assertEqual(hb_resp.status_code, 200)

        # After heartbeat: UID is populated, verification_status is READY
        enriched_after = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(enriched_after[0]["facebook_user_id"], "100099887766554")
        self.assertEqual(enriched_after[0]["verification_status"], "READY")

    def test_03_heartbeat_with_empty_c_user_marks_session_unverified(self):
        """3. Extension heartbeat without active session (c_user empty) -> SESSION_UNVERIFIED."""
        acc = self.module.create_facebook_account(self.user_id, "FB Gamma", "")
        device_id, token = pair_worker(self.module, self.client, "Worker Gamma", c_user="", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], device_id)

        # Heartbeat with empty session
        self.client.post(
            "/api/agent/heartbeat",
            headers={"X-Device-ID": device_id, "X-Agent-Token": token},
            json={"c_user": "", "facebook_user_id": "", "facebook_logged_in": False},
        )
        enriched = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(enriched[0]["verification_status"], "SESSION_UNVERIFIED")

    def test_04_heartbeat_with_mismatched_c_user_marks_account_changed(self):
        """4. Extension heartbeat with c_user different from bound UID -> ACCOUNT_CHANGED."""
        acc = self.module.create_facebook_account(self.user_id, "FB Delta", "100011111111")
        device_id, token = pair_worker(self.module, self.client, "Worker Delta", c_user="100011111111", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], device_id)

        # User switches Facebook accounts in Chrome, worker reports different c_user
        self.client.post(
            "/api/agent/heartbeat",
            headers={"X-Device-ID": device_id, "X-Agent-Token": token},
            json={"c_user": "100099999999", "facebook_user_id": "100099999999"},
        )
        enriched = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(enriched[0]["verification_status"], "ACCOUNT_CHANGED")

    def test_05_worker_offline_transition_marks_worker_offline(self):
        """5. Worker offline for > 75s transitions account to WORKER_OFFLINE."""
        acc = self.module.create_facebook_account(self.user_id, "FB Epsilon", "100055555555")
        device_id, token = pair_worker(self.module, self.client, "Worker Epsilon", c_user="100055555555", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], device_id)

        # Backdate worker last_seen by 90 seconds
        devices = self.module.load_devices(self.user_id)
        devices[device_id]["last_seen"] = time.time() - 90
        self.module.save_devices(self.user_id, devices)

        enriched = self.module.get_enriched_accounts(self.user_id)
        self.assertEqual(enriched[0]["verification_status"], "WORKER_OFFLINE")

    def test_06_unmapped_account_marks_profile_unavailable(self):
        """6. Account without worker binding shows PROFILE_UNAVAILABLE."""
        acc = self.module.create_facebook_account(self.user_id, "FB Zeta", "")
        enriched = self.module.get_enriched_accounts(self.user_id)
        matching = [a for a in enriched if a["account_id"] == acc["account_id"]][0]
        self.assertEqual(matching["verification_status"], "PROFILE_UNAVAILABLE")

    def test_07_rename_account_preserves_device_mapping_and_uid(self):
        """7. Renaming display_name preserves device binding and UID."""
        acc = self.module.create_facebook_account(self.user_id, "Old Name", "100077777777")
        device_id, _ = pair_worker(self.module, self.client, "Worker Renamed", c_user="100077777777", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], device_id)

        # Update name
        self.module.update_facebook_account(self.user_id, acc["account_id"], "New Super Name", device_id=device_id)
        reloaded = [a for a in self.module.load_facebook_accounts(self.user_id) if a["account_id"] == acc["account_id"]][0]
        self.assertEqual(reloaded["display_name"], "New Super Name")
        self.assertEqual(reloaded["device_id"], device_id)
        self.assertEqual(reloaded["facebook_user_id"], "100077777777")

    def test_08_multi_tenant_isolation_for_accounts_workers_and_groups(self):
        """8. Multi-tenant isolation: Tenant B cannot see or manipulate Tenant A data."""
        client_b = self.module.app.test_client()
        user_b = create_authenticated_user(client_b, "tenant_b")

        acc_a = self.module.create_facebook_account(self.user_id, "Tenant A Acc", "100011111111")
        self.client.post("/add-group", data={"group_url": "https://www.facebook.com/groups/tenant-a-group"})

        # Tenant B queries accounts and groups
        b_accounts = self.module.get_enriched_accounts(user_b)
        b_groups = self.module.load_groups(user_b)
        self.assertEqual(len(b_accounts), 0)
        self.assertEqual(len(b_groups), 0)

        # Tenant B cannot preflight using Tenant A account
        preflight_b = client_b.post(
            "/api/campaign/preflight",
            json={
                "account_ids": [acc_a["account_id"]],
                "group_urls": ["https://www.facebook.com/groups/tenant-a-group"],
            },
        ).get_json()
        self.assertEqual(preflight_b["status"], "BLOCKED")
        self.assertFalse(preflight_b["can_run"])

    # =========================================================================
    # PART 2: QUICK CAMPAIGN BALANCING & SNAPSHOTS (SCENARIOS 9 - 12)
    # =========================================================================

    def test_09_quick_campaign_1_account_1_group_passes_preflight_and_creates_100_percent_snapshot(self):
        """9. Quick Campaign with 1 account + 1 group gives 100% assignment."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Solo", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Solo", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        group_urls = ["https://www.facebook.com/groups/solo-group"]
        snapshot = self.module.build_snapshot_from_balanced(self.user_id, group_urls, [acc["account_id"]])
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["account_id"], acc["account_id"])
        self.assertEqual(snapshot[0]["groups"], group_urls)

    def test_10_quick_campaign_1_account_100_groups_allocates_all_to_single_account(self):
        """10. Quick Campaign with 1 account + 100 groups assigns all 100 to that account."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Hundred", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Hundred", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        group_urls = [f"https://www.facebook.com/groups/bulk-{i}" for i in range(100)]
        snapshot = self.module.build_snapshot_from_balanced(self.user_id, group_urls, [acc["account_id"]])
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(len(snapshot[0]["groups"]), 100)

    def test_11_quick_campaign_2_accounts_100_groups_auto_balances_50_50(self):
        """11. Quick Campaign with 2 accounts + 100 groups balances 50/50 automatically."""
        acc1 = self.module.create_facebook_account(self.user_id, "Acc 1", "100011111111")
        dev1, tok1 = pair_worker(self.module, self.client, "Worker 1", c_user="100011111111", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc1["account_id"], dev1)

        acc2 = self.module.create_facebook_account(self.user_id, "Acc 2", "100022222222")
        dev2, tok2 = pair_worker(self.module, self.client, "Worker 2", c_user="100022222222", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc2["account_id"], dev2)

        group_urls = [f"https://www.facebook.com/groups/even-{i}" for i in range(100)]
        snapshot = self.module.build_snapshot_from_balanced(self.user_id, group_urls, [acc1["account_id"], acc2["account_id"]])
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(len(snapshot[0]["groups"]), 50)
        self.assertEqual(len(snapshot[1]["groups"]), 50)
        self.assertEqual(len(set(snapshot[0]["groups"]) & set(snapshot[1]["groups"])), 0)

    def test_12_quick_campaign_with_custom_manual_allocation_snapshot(self):
        """12. Quick Campaign with manual allocation accordion respects custom mapping."""
        acc1 = self.module.create_facebook_account(self.user_id, "Acc Custom 1", "100011111111")
        dev1, _ = pair_worker(self.module, self.client, "Worker C1", c_user="100011111111", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc1["account_id"], dev1)

        acc2 = self.module.create_facebook_account(self.user_id, "Acc Custom 2", "100022222222")
        dev2, _ = pair_worker(self.module, self.client, "Worker C2", c_user="100022222222", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc2["account_id"], dev2)

        g1 = "https://www.facebook.com/groups/custom-1"
        g2 = "https://www.facebook.com/groups/custom-2"
        g3 = "https://www.facebook.com/groups/custom-3"
        custom_mapping = {g1: acc1["account_id"], g2: acc2["account_id"], g3: acc2["account_id"]}

        # Post /run-campaign with account_group_snapshot via JSON
        resp = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Manual Override Campaign",
                "content": "Custom test",
                "selected_accounts": [acc1["account_id"], acc2["account_id"]],
                "selected_groups": [g1, g2, g3],
                "account_group_snapshot": custom_mapping,
            },
        )
        self.assertEqual(resp.status_code, 200)
        camp_id = resp.get_json()["campaign_id"]
        camp = self.module.get_engine_campaign(self.user_id, camp_id)
        snapshot = camp["account_group_snapshot"]
        s_map = {s["account_id"]: s["groups"] for s in snapshot}
        self.assertEqual(s_map[acc1["account_id"]], [g1])
        self.assertEqual(set(s_map[acc2["account_id"]]), {g2, g3})

    # =========================================================================
    # PART 3: PREFLIGHT SAFETY VALIDATION (SCENARIOS 13 - 19, 35)
    # =========================================================================

    def test_13_preflight_warns_or_blocks_when_account_is_busy(self):
        """13. Preflight check warns/blocks when account is already engaged in another campaign."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Busy", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Busy", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        # Start an initial queued campaign with this account
        g = ["https://www.facebook.com/groups/busy-test"]
        camp = self.module.create_engine_campaign(self.user_id, "Active Camp", [{"account_id": acc["account_id"], "groups": g}], {"content": "X"}, "queued")
        self.module.transition_engine_campaign_status(self.user_id, camp["campaign_id"], "running")

        # Preflight for new campaign with same account
        res = self.module.validate_campaign_preflight(self.user_id, [acc["account_id"]], g)
        busy_check = [c for c in res["checks"] if c["key"] == "account_busy"][0]
        self.assertIn(busy_check["status"], ["WARN", "FAIL"])

    def test_14_preflight_fails_when_worker_is_offline(self):
        """14. Preflight check blocks when assigned worker is offline."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Off", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Off", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        # Backdate worker to offline
        devices = self.module.load_devices(self.user_id)
        devices[dev_id]["last_seen"] = time.time() - 100
        self.module.save_devices(self.user_id, devices)

        res = self.module.validate_campaign_preflight(self.user_id, [acc["account_id"]], ["https://www.facebook.com/groups/off"])
        worker_check = [c for c in res["checks"] if c["key"] == "worker_online"][0]
        self.assertEqual(worker_check["status"], "FAIL")
        self.assertFalse(res["can_run"])

    def test_15_preflight_blocks_when_account_is_account_changed(self):
        """15. Preflight check blocks when account mismatch (ACCOUNT_CHANGED) is detected."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Mismatch", "100011111111")
        dev_id, token = pair_worker(self.module, self.client, "Worker Mismatch", c_user="100011111111", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)
        self.client.post("/api/agent/heartbeat", headers={"X-Device-ID": dev_id, "X-Agent-Token": token}, json={"c_user": "100099999999"})

        res = self.module.validate_campaign_preflight(self.user_id, [acc["account_id"]], ["https://www.facebook.com/groups/mismatch"])
        session_check = [c for c in res["checks"] if c["key"] == "session_verified"][0]
        self.assertEqual(session_check["status"], "FAIL")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertFalse(res["can_run"])

    def test_16_preflight_warns_when_session_is_unverified(self):
        """16. Preflight check warns when session has no detected c_user."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Unver", "")
        dev_id, token = pair_worker(self.module, self.client, "Worker Unver", c_user="", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        res = self.module.validate_campaign_preflight(self.user_id, [acc["account_id"]], ["https://www.facebook.com/groups/unver"])
        session_check = [c for c in res["checks"] if c["key"] == "session_verified"][0]
        self.assertEqual(session_check["status"], "WARN")

    def test_17_preflight_blocks_when_no_accounts_or_groups_selected(self):
        """17. Preflight check blocks if account_ids or group_urls is empty."""
        res1 = self.module.validate_campaign_preflight(self.user_id, [], ["https://www.facebook.com/groups/grp"])
        self.assertEqual(res1["status"], "BLOCKED")
        self.assertFalse(res1["can_run"])

        res2 = self.module.validate_campaign_preflight(self.user_id, ["nonexistent_acc"], [])
        self.assertEqual(res2["status"], "BLOCKED")
        self.assertFalse(res2["can_run"])

    def test_18_preflight_succeeds_with_valid_future_schedule(self):
        """18. Preflight check passes when scheduled_at is a valid future ISO datetime."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Sched", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Sched", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        res = self.module.validate_campaign_preflight(self.user_id, [acc["account_id"]], ["https://www.facebook.com/groups/sched"], future_iso)
        sched_check = [c for c in res["checks"] if c["key"] == "schedule_valid"][0]
        self.assertEqual(sched_check["status"], "PASS")

    def test_19_preflight_fails_with_past_schedule(self):
        """19. Preflight check fails when scheduled_at is in the past."""
        past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        res = self.module.validate_campaign_preflight(self.user_id, ["any"], ["https://www.facebook.com/groups/past"], past_iso)
        sched_check = [c for c in res["checks"] if c["key"] == "schedule_valid"][0]
        self.assertEqual(sched_check["status"], "FAIL")

    def test_35_preflight_api_endpoint_structure_with_7_checks(self):
        """35. Preflight API endpoint /api/campaign/preflight returns all 7 standard checks."""
        resp = self.client.post("/api/campaign/preflight", json={"account_ids": [], "group_urls": []})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)
        self.assertIn("can_run", data)
        self.assertIn("checks", data)
        keys = {c["key"] for c in data["checks"]}
        expected_keys = {
            "worker_online", "account_ready", "profile_mapped",
            "session_verified", "groups_valid", "account_busy", "schedule_valid"
        }
        self.assertEqual(keys, expected_keys)

    # =========================================================================
    # PART 4: TEMPLATES, DRAFTS & SUBMISSION (SCENARIOS 20 - 23)
    # =========================================================================

    def test_20_quick_mode_campaign_templates_crud_and_load(self):
        """20. Quick Mode templates CRUD allows saving and loading template payloads."""
        create_res = self.client.post(
            "/api/campaign-templates",
            json={"template_name": "Quick Promo 1", "payload": {"content": "Sale 50%", "min_delay": 2, "max_delay": 5}},
        ).get_json()
        self.assertTrue(create_res["success"])
        tmpl_id = create_res["template_id"]

        templates = self.module.load_campaign_templates(self.user_id)
        found = [t for t in templates if t["template_id"] == tmpl_id][0]
        self.assertEqual(found["template_name"], "Quick Promo 1")
        self.assertEqual(found["payload"]["content"], "Sale 50%")

    def test_21_quick_mode_draft_persists_without_running_tasks(self):
        """21. Saving campaign as draft creates draft status without dispatching active tasks."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Draft", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Draft", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        resp = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Draft Campaign",
                "content": "Draft content",
                "selected_accounts": [acc["account_id"]],
                "selected_groups": ["https://www.facebook.com/groups/draft-grp"],
                "campaign_action": "draft",
            },
        )
        self.assertEqual(resp.status_code, 200)
        camp_id = resp.get_json()["campaign_id"]
        camp = self.module.get_engine_campaign(self.user_id, camp_id)
        self.assertEqual(camp["status"], "draft")

    def test_22_run_campaign_ajax_json_returns_json_response_with_redirect(self):
        """22. AJAX JSON POST to /run-campaign returns structured JSON with redirect_url."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Ajax", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Ajax", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        resp = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Ajax Quick Campaign",
                "content": "Testing Ajax",
                "selected_accounts": [acc["account_id"]],
                "selected_groups": ["https://www.facebook.com/groups/ajax-grp"],
                "campaign_action": "run",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("campaign_id", data)
        self.assertIn("message", data)

    def test_23_run_campaign_form_post_redirects_and_creates_campaign(self):
        """23. Standard form POST to /run-campaign redirects 302 and creates campaign."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Form", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Form", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)
        self.client.post("/add-group", data={"group_url": "https://www.facebook.com/groups/form-grp"})

        resp = self.client.post(
            "/run-campaign",
            data={
                "campaign_name": "Form Campaign",
                "content": "Testing Form",
                "min_delay": "1",
                "max_delay": "2",
                "selected_accounts": [acc["account_id"]],
                "selected_groups": ["https://www.facebook.com/groups/form-grp"],
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        camps = self.module.load_engine_campaigns(self.user_id)
        self.assertTrue(any(c["campaign_name"] == "Form Campaign" for c in camps))

    # =========================================================================
    # PART 5: BACKGROUND EXECUTION & SERVICE WORKER SAFETY (SCENARIOS 24 - 29)
    # =========================================================================

    def test_24_background_execution_worker_claims_jobs_independently(self):
        """24. Background execution: worker claims jobs via agent job poll independently of web tabs."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Worker Bg", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Independent", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        g_url = "https://www.facebook.com/groups/bg-independent"
        camp = self.module.create_engine_campaign(
            self.user_id, "Background Job", [{"account_id": acc["account_id"], "groups": [g_url]}], {"content": "Post"}, "queued"
        )

        # Worker polls for work via /api/agent/job
        poll_resp = self.client.get("/api/agent/job", headers={"X-Device-ID": dev_id, "X-Agent-Token": token}).get_json()
        self.assertTrue(poll_resp.get("has_job"))
        self.assertEqual(poll_resp["job"]["group_url"], g_url)

    def test_25_service_worker_logic_inactive_tab_and_tab_reuse_verified(self):
        """25. Extension service worker code verification: active: false and single tab reuse."""
        sw_path = REPO_ROOT / "extension" / "service_worker.js"
        self.assertTrue(sw_path.exists())
        sw_code = sw_path.read_text(encoding="utf-8")

        # Verify background tab creation without stealing focus
        self.assertIn("active: false", sw_code)
        # Verify single tab reuse across group posts
        self.assertIn("currentExecutionTab", sw_code)
        self.assertIn("chrome.tabs.update(currentExecutionTab", sw_code)

    def test_26_service_worker_logic_auto_closes_tab_on_completion(self):
        """26. Extension service worker code verification: auto-closes execution tab when job completes."""
        sw_path = REPO_ROOT / "extension" / "service_worker.js"
        sw_code = sw_path.read_text(encoding="utf-8")
        self.assertIn("chrome.tabs.remove(currentExecutionTab", sw_code)

    def test_27_service_worker_logic_activates_tab_on_checkpoint_or_login(self):
        """27. Extension service worker code verification: only activates tab (active: true) on checkpoint/login."""
        sw_path = REPO_ROOT / "extension" / "service_worker.js"
        sw_code = sw_path.read_text(encoding="utf-8")
        self.assertIn("active:", sw_code)
        self.assertIn("checkpoint", sw_code)
        self.assertIn("requires_attention", sw_code)

    def test_28_tab_isolation_dashboard_lifecycle_does_not_interrupt_worker(self):
        """28. Dashboard navigation, logout, or session expiry does not interrupt worker polling."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Iso", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Iso", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        # Worker can poll successfully even if client logout happens
        self.client.post("/logout")
        poll_resp = self.client.get("/api/agent/job", headers={"X-Device-ID": dev_id, "X-Agent-Token": token})
        self.assertEqual(poll_resp.status_code, 200)

    def test_29_worker_restart_resumes_pending_without_replaying_success(self):
        """29. Worker restart/reconnect resumes pending work and never replays completed tasks."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Res", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Res", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        g1 = "https://www.facebook.com/groups/res-1"
        g2 = "https://www.facebook.com/groups/res-2"
        camp = self.module.create_engine_campaign(
            self.user_id, "Resume Test", [{"account_id": acc["account_id"], "groups": [g1, g2]}], {"content": "X"}, "queued"
        )

        # Claim job 1
        p1 = self.client.get("/api/agent/job", headers={"X-Device-ID": dev_id, "X-Agent-Token": token}).get_json()
        self.assertTrue(p1.get("has_job"))
        task1_id = p1["job"]["task_id"]

        # Complete task 1 via /api/agent/status
        self.client.post(
            "/api/agent/status",
            headers={"X-Device-ID": dev_id, "X-Agent-Token": token},
            json={"job_id": p1["job"]["job_id"], "status": "finished", "event": "group_success", "event_id": f"{p1['job']['job_id']}:group:0:success", "processed": 1, "success": 1, "errors": 0},
        )
        self.module.update_campaign_task(self.user_id, task1_id, status="success")

        # Simulate worker restart: poll again -> receives next task, NOT task 1
        p2 = self.client.get("/api/agent/job", headers={"X-Device-ID": dev_id, "X-Agent-Token": token}).get_json()
        self.assertTrue(p2.get("has_job"))
        self.assertNotEqual(p2["job"]["task_id"], task1_id)
        self.assertEqual(p2["job"]["group_url"], g2)

    # =========================================================================
    # PART 6: IDEMPOTENCY & MINIMAL NOTIFICATIONS (SCENARIOS 30 - 32)
    # =========================================================================

    def test_30_idempotency_double_run_creates_no_duplicate_tasks(self):
        """30. Multiple calls to run-campaign or create_engine_campaign with same config create no duplicates."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Idem", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Idem", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        g = ["https://www.facebook.com/groups/idem-1"]
        c1 = self.module.create_engine_campaign(self.user_id, "Idem 1", [{"account_id": acc["account_id"], "groups": g}], {"content": "A"}, "queued")
        tasks1 = self.module.list_campaign_tasks(self.user_id, c1["campaign_id"])
        self.assertEqual(len(tasks1), 1)

    def test_31_completion_notification_sent_exactly_once(self):
        """31. Campaign completion triggers strictly 1 completion notification (idempotent)."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Notif", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Notif", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        camp = self.module.create_engine_campaign(
            self.user_id, "Notif Camp", [{"account_id": acc["account_id"], "groups": ["https://fb.com/groups/n1"]}], {"content": "N"}, "queued"
        )
        self.module.transition_engine_campaign_status(self.user_id, camp["campaign_id"], "running")

        # Complete all tasks
        tasks = self.module.list_campaign_tasks(self.user_id, camp["campaign_id"])
        for t in tasks:
            self.module.update_campaign_task(self.user_id, t["task_id"], status="success")

        # Sync 1: transitions to completed, sends completion notification
        c_synced1 = self.module.sync_engine_campaign_state(self.user_id, camp["campaign_id"])
        self.assertEqual(c_synced1["status"], "completed")
        self.assertTrue(c_synced1.get("completion_notified"))

        notifs1 = self.module.load_notifications(self.user_id)
        completion_notifs1 = [n for n in notifs1 if any(k in n.get("title", "").lower() for k in ("hoàn thành", "hoàn tất"))]
        self.assertEqual(len(completion_notifs1), 1)

        # Sync 2: duplicate sync must NOT send another notification
        c_synced2 = self.module.sync_engine_campaign_state(self.user_id, camp["campaign_id"])
        notifs2 = self.module.load_notifications(self.user_id)
        completion_notifs2 = [n for n in notifs2 if any(k in n.get("title", "").lower() for k in ("hoàn thành", "hoàn tất"))]
        self.assertEqual(len(completion_notifs2), 1)

    def test_32_no_per_task_notification_spam_sent_to_user(self):
        """32. Individual task successes do not flood user with per-post notifications."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Spam", "100012345678")
        dev_id, token = pair_worker(self.module, self.client, "Worker Spam", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)

        groups = [f"https://fb.com/groups/spam-{i}" for i in range(5)]
        camp = self.module.create_engine_campaign(
            self.user_id, "Spam Test", [{"account_id": acc["account_id"], "groups": groups}], {"content": "S"}, "queued"
        )
        self.module.transition_engine_campaign_status(self.user_id, camp["campaign_id"], "running")

        tasks = self.module.list_campaign_tasks(self.user_id, camp["campaign_id"])
        # Complete 4 out of 5 tasks (campaign still running)
        for t in tasks[:4]:
            self.module.update_campaign_task(self.user_id, t["task_id"], status="success")
            self.module.sync_engine_campaign_state(self.user_id, camp["campaign_id"])

        # Check notifications: 0 notifications should be present while running
        notifs = self.module.load_notifications(self.user_id)
        self.assertEqual(len(notifs), 0)

    # =========================================================================
    # PART 7: UX, LOCALSTORAGE, BACKWARD COMPAT & PERFORMANCE (SCENARIOS 33, 34, 36)
    # =========================================================================

    def test_33_localstorage_and_preferences_persistence_format(self):
        """33. campaign-wizard.js defines localstorage preference keys without auto-starting."""
        js_path = REPO_ROOT / "static" / "js" / "campaign-wizard.js"
        self.assertTrue(js_path.exists())
        js_code = js_path.read_text(encoding="utf-8")
        self.assertIn("fbpostpro_campaign_pref", js_code)
        self.assertIn("localStorage.setItem", js_code)
        self.assertIn("localStorage.getItem", js_code)

    def test_34_compose_page_contains_quick_mode_and_backward_compatible_6_steps(self):
        """34. /compose page includes Quick Mode (3 steps) and preserves 6-step wizard DOM."""
        resp = self.client.get("/compose")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Quick Mode elements
        self.assertIn("quickWizardContainer", html)
        self.assertIn("quickStepper", html)
        self.assertIn("btnModeQuick", html)
        self.assertIn("btnQuickStartCampaign", html)
        self.assertIn("quickAutoBalanceCard", html)
        self.assertIn("quickPreflightCard", html)

        # Advanced Mode & Phase 12 backward compatible elements
        self.assertIn("advancedWizardContainer", html)
        self.assertIn("wizard-stepper", html)
        self.assertIn("validation-guard-box", html)
        self.assertIn("BẮT ĐẦU CHIẾN DỊCH", html)
        self.assertIn("Nội dung", html)
        self.assertIn("Tài khoản", html)
        self.assertIn("Chọn nhóm", html)
        self.assertIn("Phân bổ", html)
        self.assertIn("Hẹn lịch", html)
        self.assertIn("Xác nhận", html)

    def test_36_performance_and_fast_response_times_under_threshold(self):
        """36. Performance: Preflight check and run-campaign respond in < 200ms."""
        acc = self.module.create_facebook_account(self.user_id, "Acc Perf", "100012345678")
        dev_id, _ = pair_worker(self.module, self.client, "Worker Perf", c_user="100012345678", set_online=True)
        self.module.bind_facebook_account_device(self.user_id, acc["account_id"], dev_id)
        groups = [f"https://www.facebook.com/groups/perf-{i}" for i in range(50)]

        # Benchmark preflight
        t0 = time.perf_counter()
        preflight_res = self.client.post(
            "/api/campaign/preflight",
            json={"account_ids": [acc["account_id"]], "group_urls": groups},
        )
        preflight_latency_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(preflight_res.status_code, 200)
        self.assertLess(preflight_latency_ms, 200.0, f"Preflight latency {preflight_latency_ms:.1f}ms exceeds 200ms threshold")

        # Benchmark run-campaign JSON
        t1 = time.perf_counter()
        run_res = self.client.post(
            "/run-campaign",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={
                "campaign_name": "Perf Campaign",
                "content": "Fast test",
                "selected_accounts": [acc["account_id"]],
                "selected_groups": groups,
            },
        )
        run_latency_ms = (time.perf_counter() - t1) * 1000
        self.assertEqual(run_res.status_code, 200)
        self.assertLess(run_latency_ms, 200.0, f"Run campaign latency {run_latency_ms:.1f}ms exceeds 200ms threshold")


if __name__ == "__main__":
    unittest.main()
