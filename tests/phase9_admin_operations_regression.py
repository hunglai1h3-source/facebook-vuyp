"""Phase 9 functional checks; never posts to Facebook."""
import json
import tempfile

from phase6_worker_command_regression import load_app, pair, heartbeat
from phase7_bulk_groups_regression import register


def promote_admin(module, user_id):
    users = module.load_users()
    users[user_id]["role"] = "admin"
    users[user_id]["max_facebook_accounts"] = 2
    module.save_users(users)


def run_checks(temp):
    module = load_app(temp)
    regular = module.app.test_client()
    regular_id = register(regular, "phase9_regular")
    admin = module.app.test_client()
    admin_id = register(admin, "phase9_admin")
    promote_admin(module, admin_id)

    assert regular.get("/admin").status_code == 403
    assert regular.get("/api/admin/dashboard").status_code == 403
    anonymous = module.app.test_client()
    assert anonymous.get("/admin").status_code == 302
    assert anonymous.get("/api/admin/dashboard").status_code == 401
    print("PASS 1-2: admin HTML/API reject regular users; anonymous API is 401")

    dashboard = admin.get("/admin")
    assert dashboard.status_code == 200 and "Operations overview" in dashboard.get_data(as_text=True)
    api_metrics = admin.get("/api/admin/dashboard").get_json()["metrics"]
    assert api_metrics["users"] == 2 and api_metrics["active_users"] == 2
    print("PASS 3: role-admin opens dashboard and receives aggregate metrics")

    users = module.load_users()
    users[regular_id].update({
        "max_facebook_accounts": 2, "max_groups": 10, "max_campaigns": 10,
        "max_devices": 2, "max_active_campaigns": 1, "max_tasks_per_campaign": 10,
    })
    module.save_users(users)
    group_urls = ["https://www.facebook.com/groups/phase9-a", "https://www.facebook.com/groups/phase9-b"]
    assert len(module.import_groups(regular_id, group_urls)["added"]) == 2
    account = module.create_facebook_account(regular_id, "Phase 9 Account", "99001")
    online_device, agent_headers = pair(regular, "phase9-online")
    offline_device, _ = pair(regular, "phase9-offline")
    module.bind_facebook_account_device(regular_id, account["account_id"], online_device["device_id"])
    module.save_group_assignments(regular_id, [
        {"group_url": url, "account_id": account["account_id"]} for url in group_urls
    ])
    assert heartbeat(regular, agent_headers).status_code == 200
    regular.post("/save-post", data={"campaign_name": "Phase 9 monitored", "content": "No real post", "min_delay": "0", "max_delay": "0"})
    assert regular.post("/run-campaign").status_code == 302
    campaign = module.load_engine_campaigns(regular_id)[0]
    claimed = regular.get("/api/agent/job", headers=agent_headers).get_json()["job"]

    metrics = admin.get("/api/admin/dashboard").get_json()["metrics"]
    assert metrics["accounts"] == 1 and metrics["groups"] == 2 and metrics["campaigns"] == 1
    assert metrics["devices_online"] == 1 and metrics["devices_offline"] == 1
    users_page = admin.get("/admin/users?q=phase9_regular").get_data(as_text=True)
    assert "phase9_regular" in users_page and "phase9_admin" not in users_page
    assert admin.get("/admin/accounts").status_code == 200
    assert admin.get("/admin/groups").status_code == 200
    campaigns_page = admin.get("/admin/campaigns").get_data(as_text=True)
    assert "Phase 9 monitored" in campaigns_page and "1 / 2" not in campaigns_page
    detail = admin.get(f"/admin/campaigns/{campaign['campaign_id']}").get_data(as_text=True)
    assert claimed["engine_task_id"] in detail and account["account_id"] in detail
    workers = admin.get("/admin/workers").get_data(as_text=True)
    assert "Phase 6 Worker" in workers and "Worker phase9-offline" in workers and "online" in workers and "offline" in workers
    assert agent_headers["X-Agent-Token"] not in workers
    print("PASS 4,7-8: user search, campaign/task progress and online/offline worker monitoring are correct")

    cancelled = admin.post(f"/api/admin/campaigns/{campaign['campaign_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.get_json()["status"] == "cancelled"
    assert module.get_engine_campaign(regular_id, campaign["campaign_id"])["lifecycle"] == "cancelled"
    assert all(task["status"] == "cancelled" for task in module.load_engine_tasks(regular_id, campaign["campaign_id"]))
    command = module.load_control(regular_id)[online_device["device_id"]]
    assert command["command_type"] == "stop" and command["stop_requested"] is True
    print("PASS: admin cancel uses the existing stop command flow before persistent cancellation")

    locked = admin.post(f"/api/admin/users/{regular_id}/status", json={"is_active": False})
    assert locked.status_code == 200 and module.find_user_by_id(regular_id)["is_active"] is False
    before = len(module.load_engine_campaigns(regular_id))
    blocked = regular.post("/run-campaign")
    assert blocked.status_code == 302 and "/login" in blocked.location
    assert len(module.load_engine_campaigns(regular_id)) == before
    print("PASS 5-6: lock persists without deleting data and blocks new campaign execution")

    assert admin.post(f"/api/admin/users/{regular_id}/status", json={"is_active": True}).status_code == 200
    assert regular.post("/login", data={"login": "phase7_phase9_regular", "password": "Phase7Test123!"}).status_code == 302
    quota = admin.post(f"/api/admin/users/{regular_id}/quota", json={
        "max_accounts": 1, "max_groups": 2, "max_campaigns": 1,
        "max_devices": 2, "max_active_campaigns": 1, "max_tasks_per_campaign": 1,
    })
    assert quota.status_code == 200
    assert module.find_user_by_id(regular_id)["max_facebook_accounts"] == 1
    assert admin.post(f"/api/admin/users/{regular_id}/quota", json={"max_groups": 0}).status_code == 400
    assert admin.post(f"/api/admin/users/{regular_id}/status", json=[]).status_code == 400
    try:
        module.create_facebook_account(regular_id, "Quota rejected")
        raise AssertionError("Facebook account quota was not enforced")
    except ValueError:
        pass
    assert not module.import_groups(regular_id, ["https://www.facebook.com/groups/phase9-over-quota"])["added"]
    pair_code = regular.post("/api/extension/pair-code").get_json()["code"]
    device_over = regular.post("/api/extension/pair", json={"code": pair_code, "device_name": "over quota"})
    assert device_over.status_code == 409
    regular.post("/save-post", data={"campaign_name": "Blocked by quota", "content": "No post", "min_delay": "0", "max_delay": "0"})
    quota_blocked = regular.post("/run-campaign", follow_redirects=True)
    assert quota_blocked.status_code == 200 and "quota 1 task" in quota_blocked.get_data(as_text=True)
    assert len(module.load_engine_campaigns(regular_id)) == 1
    print("PASS 11: account/group/device/campaign/task quotas are enforced in backend paths")

    module.record_operational_log(
        regular_id, "worker_error", "error", "Network timeout token=should-never-render",
        campaign_id=campaign["campaign_id"], task_id=claimed["engine_task_id"],
        account_id=account["account_id"], group_id="grp_test", device_id=online_device["device_id"],
    )
    logs = admin.get(f"/admin/logs?customer_id={regular_id}&campaign_id={campaign['campaign_id']}&severity=error&q=timeout").get_data(as_text=True)
    assert "worker_error" in logs and "[REDACTED]" in logs and "should-never-render" not in logs
    audits = admin.get("/admin/audit").get_data(as_text=True)
    assert all(action in audits for action in ("lock_user", "unlock_user", "change_quota", "cancel_campaign"))
    assert module.load_admin_audit_logs()[1] >= 4
    print("PASS 9-10: structured log filters and secret redaction work; admin actions are audited")

    other = module.app.test_client()
    other_id = register(other, "phase9_other")
    assert other.get(f"/admin/users/{regular_id}").status_code == 403
    assert other.post(f"/api/admin/users/{regular_id}/status", json={"is_active": False}).status_code == 403
    assert module.find_user_by_id(regular_id)["is_active"] is True
    assert other_id != regular_id
    print("PASS 12: a different tenant cannot read or mutate admin-owned resources")

    module = load_app(temp)
    assert module.find_user_by_id(regular_id)["max_tasks_per_campaign"] == 1
    assert module.load_operational_logs({"customer_id": regular_id})[1] > 0
    assert module.load_admin_audit_logs()[1] >= 4
    print("PASS persistence: quotas, operational logs and admin audit logs survive backend reload")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fbpp-phase9-") as temp:
        run_checks(temp)
