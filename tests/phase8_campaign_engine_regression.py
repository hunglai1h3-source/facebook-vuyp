"""Phase 8 regression: persistent scheduling and account-bound task execution."""
import json
from datetime import timedelta
import tempfile
import threading

from phase6_worker_command_regression import load_app, pair, heartbeat
from phase7_bulk_groups_regression import register, allow_two_accounts


def save_content(client, name):
    response = client.post("/save-post", data={
        "campaign_name": name,
        "content": f"Persistent content for {name}",
        "min_delay": "0",
        "max_delay": "0",
    })
    assert response.status_code == 302


def login_user(client, name):
    response = client.post("/login", data={
        "login": f"phase7_{name}", "password": "Phase7Test123!",
    })
    assert response.status_code == 302


def report(client, headers, job, status, success=0, errors=0):
    return client.post("/api/agent/status", headers=headers, json={
        "job_id": job["job_id"],
        "status": status,
        "processed": success + errors,
        "success": success,
        "errors": errors,
        "message": f"task {status}",
    })


def setup_multi_account(module, client, user_id, count=100):
    allow_two_accounts(module, user_id)
    urls = [f"https://www.facebook.com/groups/phase8-{index:03d}" for index in range(count)]
    assert len(module.import_groups(user_id, urls)["added"]) == count
    account_a = module.create_facebook_account(user_id, "Account A", "81001")
    account_b = module.create_facebook_account(user_id, "Account B", "81002")
    assignments = module.evenly_assign_groups(
        urls, [account_a["account_id"], account_b["account_id"]]
    )
    module.save_group_assignments(user_id, assignments)
    device_a, headers_a = pair(client, "phase8-a")
    device_b, headers_b = pair(client, "phase8-b")
    module.bind_facebook_account_device(user_id, account_a["account_id"], device_a["device_id"])
    save_content(client, "Phase 8 multi account")
    return urls, account_a, account_b, device_a, device_b, headers_a, headers_b


def run_main_engine_checks(temp):
    module = load_app(temp)
    client = module.app.test_client()
    user_id = register(client, "engine")
    (urls, account_a, account_b, device_a, device_b,
     headers_a, headers_b) = setup_multi_account(module, client, user_id)

    assert heartbeat(client, headers_a).status_code == 200
    offline = client.post("/api/agent/heartbeat", headers=headers_b, json={
        "facebook_logged_in": False,
        "worker_state": "idle",
        "current_job_id": "",
        "device_name": "Phase 8 offline worker",
    })
    assert offline.status_code == 200

    # An assigned account without a worker/profile binding must block atomically.
    blocked = client.post("/run-campaign", follow_redirects=True)
    assert blocked.status_code == 200
    assert not module.load_engine_campaigns(user_id)
    assert "chưa được gắn" in blocked.get_data(as_text=True)
    module.bind_facebook_account_device(user_id, account_b["account_id"], device_b["device_id"])
    print("PASS 4: account without worker/profile mapping is blocked with a clear UI error")

    intruder = module.app.test_client()
    intruder_id = register(intruder, "intruder")
    intruder_device, _ = pair(intruder, "phase8-intruder")
    assert intruder.post(
        f"/groups/accounts/{account_a['account_id']}/bind",
        data={"device_id": intruder_device["device_id"]},
    ).status_code == 302
    owner_account = next(
        item for item in module.load_facebook_accounts(user_id)
        if item["account_id"] == account_a["account_id"]
    )
    assert owner_account["device_id"] == device_a["device_id"]
    assert not module.load_facebook_accounts(intruder_id)
    print("PASS ownership: another tenant cannot bind an account or worker it does not own")

    # Two simultaneous Start requests serialize into one campaign/task set.
    second = module.app.test_client()
    login_user(second, "engine")
    barrier = threading.Barrier(2)
    statuses = []

    def start(test_client):
        barrier.wait()
        statuses.append(test_client.post("/run-campaign").status_code)

    threads = [threading.Thread(target=start, args=(item,)) for item in (client, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [302, 302]
    campaigns = module.load_engine_campaigns(user_id)
    assert len(campaigns) == 1
    campaign = campaigns[0]
    tasks = module.load_engine_tasks(user_id, campaign["campaign_id"])
    assert len(tasks) == 100 and len({task["idempotency_key"] for task in tasks}) == 100
    assert len({task["task_id"] for task in tasks}) == 100
    assert all(task["group_id"].startswith("grp_") for task in tasks)
    assert all(task["browser_profile_id"].startswith("chrome-profile:ext_") for task in tasks)
    print("PASS 2/8: 100 Groups create exactly 100 persistent tasks despite double Start")

    expected_a = {item["group_url"] for item in tasks if item["account_id"] == account_a["account_id"]}
    expected_b = {item["group_url"] for item in tasks if item["account_id"] == account_b["account_id"]}
    assert len(expected_a) == len(expected_b) == 50 and expected_a.isdisjoint(expected_b)
    assert expected_a | expected_b == set(urls)

    claim_a = client.get("/api/agent/job", headers=headers_a).get_json()
    assert claim_a["has_job"] is True
    job_a1 = claim_a["job"]
    assert job_a1["account_id"] == account_a["account_id"]
    assert set(job_a1["groups"]) <= expected_a
    assert client.get("/api/agent/job", headers=headers_a).get_json()["has_job"] is False
    assert client.get("/api/agent/job", headers=headers_b).get_json()["has_job"] is False
    assert heartbeat(client, headers_b).status_code == 200
    job_b1 = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert job_b1["account_id"] == account_b["account_id"]
    assert set(job_b1["groups"]) <= expected_b
    print("PASS 3/9: each worker receives only its bound account Groups and one active task")

    assert report(client, headers_a, job_a1, "finished", success=1).status_code == 200
    duplicate = report(client, headers_a, job_a1, "finished", success=1)
    assert duplicate.status_code == 200 and duplicate.get_json()["duplicate"] is True
    assert sum(task["status"] == "successful" for task in module.load_engine_tasks(user_id, campaign["campaign_id"])) == 1

    # Backend reload keeps the terminal task and does not rematerialize it.
    module = load_app(temp)
    client = module.app.test_client()
    login_user(client, "engine")
    tasks_after_restart = module.load_engine_tasks(user_id, campaign["campaign_id"])
    assert len(tasks_after_restart) == 100
    assert sum(task["status"] == "successful" for task in tasks_after_restart) == 1
    assert client.get("/api/agent/job", headers=headers_a).get_json()["has_job"] is True
    job_a2 = module.load_jobs(user_id)[device_a["device_id"]]
    assert job_a2["groups"][0] != job_a1["groups"][0]
    assert client.get("/api/agent/job", headers=headers_a).get_json()["has_job"] is False
    print("PASS 6/7: backend restart preserves tasks and never reissues the successful task")

    # A worker restart makes the in-flight result uncertain. It is recorded as
    # failed rather than replayed, while later pending tasks continue.
    interrupted_group = job_a2["groups"][0]
    assert heartbeat(client, headers_a, state="idle", job_id="").status_code == 200
    job_a3 = client.get("/api/agent/job", headers=headers_a).get_json()["job"]
    assert job_a3["groups"][0] not in {job_a1["groups"][0], interrupted_group}
    task_by_group = {
        task["group_url"]: task
        for task in module.load_engine_tasks(user_id, campaign["campaign_id"])
    }
    assert task_by_group[job_a1["groups"][0]]["status"] == "successful"
    assert task_by_group[interrupted_group]["status"] == "failed"
    assert report(client, headers_a, job_a3, "finished", success=1).status_code == 200
    print("PASS 5: worker restart resumes later pending work without replaying success/uncertain post")

    # One account failure remains isolated; the other account's progress remains.
    assert report(client, headers_b, job_b1, "error", errors=1).status_code == 200
    job_b2 = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert job_b2["account_id"] == account_b["account_id"]
    progress = module.sync_engine_campaign(user_id, campaign["campaign_id"])["progress"]
    assert progress["successful"] == 2 and progress["failed"] == 2
    assert progress["pending"] == 95 and progress["running"] == 1
    print("PASS 10: one account failure is persistent and does not erase/stop the other account")

    assert report(client, headers_b, job_b2, "finished_with_errors", errors=1).status_code == 200
    retry_task = next(
        task for task in module.load_engine_tasks(user_id, campaign["campaign_id"])
        if task["task_id"] == job_b2["engine_task_id"]
    )
    assert retry_task["status"] == "retry_wait"
    assert retry_task["retry_count"] == 1 and retry_task["last_error"]
    assert retry_task["next_retry_at"]
    job_b3 = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert job_b3["engine_task_id"] != retry_task["task_id"]
    assert report(client, headers_b, job_b3, "finished", success=1).status_code == 200
    module._update_engine_task(
        user_id, retry_task["task_id"],
        next_retry_at=(module.utc_now() - timedelta(seconds=1)).isoformat(timespec="seconds"),
    )
    retry_job = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert retry_job["engine_task_id"] == retry_task["task_id"]
    assert retry_job["job_id"] == job_b2["job_id"]
    assert report(client, headers_b, retry_job, "finished_with_errors", errors=1).status_code == 200
    final_retry = next(
        task for task in module.load_engine_tasks(user_id, campaign["campaign_id"])
        if task["task_id"] == retry_task["task_id"]
    )
    assert final_retry["status"] == "failed" and final_retry["retry_count"] == 1
    assert len(module.load_engine_tasks(user_id, campaign["campaign_id"])) == 100
    print("PASS retry: bounded retry persists retry_count/error/time and reuses the same task ID")

    # The active account cannot be put into a second nonterminal campaign.
    try:
        module.create_engine_campaign(
            user_id, "Conflicting campaign", campaign["account_group_snapshot"],
            campaign["payload"], "queued",
        )
        raise AssertionError("conflicting account campaign was accepted")
    except ValueError:
        pass
    assert len(module.load_engine_campaigns(user_id)) == 1
    print("PASS 9: account/session busy lock rejects conflicting campaign execution")

    leased_job = client.get("/api/agent/job", headers=headers_a).get_json()["job"]
    module._update_engine_task(
        user_id, leased_job["engine_task_id"],
        lease_expires_at=(module.utc_now() - timedelta(seconds=1)).isoformat(timespec="seconds"),
    )
    next_after_lease = client.get("/api/agent/job", headers=headers_a).get_json()["job"]
    assert next_after_lease["engine_task_id"] != leased_job["engine_task_id"]
    expired = next(
        task for task in module.load_engine_tasks(user_id, campaign["campaign_id"])
        if task["task_id"] == leased_job["engine_task_id"]
    )
    assert expired["status"] == "failed" and "lease expired" in expired["last_error"]
    print("PASS recovery: expired worker lease releases account safely without replaying uncertain task")


def run_scheduler_checks(temp):
    module = load_app(temp)
    client = module.app.test_client()
    user_id = register(client, "schedule")
    allow_two_accounts(module, user_id)
    group_url = "https://www.facebook.com/groups/phase8-scheduled"
    module.import_groups(user_id, [group_url])
    account = module.create_facebook_account(user_id, "Scheduled Account", "82001")
    module.save_group_assignments(user_id, [{"group_url": group_url, "account_id": account["account_id"]}])
    device, headers = pair(client, "phase8-scheduler")
    module.bind_facebook_account_device(user_id, account["account_id"], device["device_id"])
    assert heartbeat(client, headers).status_code == 200
    save_content(client, "Persistent schedule")
    future = (module.utc_now() + timedelta(hours=2)).isoformat(timespec="seconds")
    assert client.post("/run-campaign", data={
        "campaign_action": "schedule", "scheduled_at": future,
    }).status_code == 302
    campaign = module.load_engine_campaigns(user_id)[0]
    original_task_ids = [task["task_id"] for task in module.load_engine_tasks(user_id, campaign["campaign_id"])]
    assert campaign["lifecycle"] == "scheduled"
    assert client.get("/api/agent/job", headers=headers).get_json()["has_job"] is False

    module = load_app(temp)
    client = module.app.test_client()
    login_user(client, "schedule")
    restored = module.get_engine_campaign(user_id, campaign["campaign_id"])
    assert restored["lifecycle"] == "scheduled" and restored["scheduled_at"]
    assert [task["task_id"] for task in module.load_engine_tasks(user_id, campaign["campaign_id"])] == original_task_ids
    print("PASS 1/6: scheduled campaign and stable task IDs survive backend restart")

    # Overdue policy: atomically queue on the next status/poll tick, then wait for
    # the mapped online worker; repeated ticks cannot duplicate tasks.
    campaigns = module.load_engine_campaigns(user_id)
    campaigns[0]["scheduled_at"] = (module.utc_now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    module._save_local_engine(user_id, campaigns=campaigns)
    first = client.get("/api/agent/job", headers=headers).get_json()
    assert first["has_job"] is True
    assert first["job"]["engine_campaign_id"] == campaign["campaign_id"]
    assert client.get("/api/agent/job", headers=headers).get_json()["has_job"] is False
    assert len(module.load_engine_tasks(user_id, campaign["campaign_id"])) == 1
    print("PASS scheduler: overdue campaign queues on worker poll and remains idempotent")


def run_lifecycle_checks(temp):
    module = load_app(temp)
    client = module.app.test_client()
    user_id = register(client, "lifecycle")
    allow_two_accounts(module, user_id)
    urls = [
        "https://www.facebook.com/groups/phase8-life-a",
        "https://www.facebook.com/groups/phase8-life-b",
    ]
    module.import_groups(user_id, urls)
    account_a = module.create_facebook_account(user_id, "Life A", "83001")
    account_b = module.create_facebook_account(user_id, "Life B", "83002")
    module.save_group_assignments(user_id, [
        {"group_url": urls[0], "account_id": account_a["account_id"]},
        {"group_url": urls[1], "account_id": account_b["account_id"]},
    ])
    device_a, headers_a = pair(client, "phase8-life-a")
    device_b, headers_b = pair(client, "phase8-life-b")
    module.bind_facebook_account_device(user_id, account_a["account_id"], device_a["device_id"])
    module.bind_facebook_account_device(user_id, account_b["account_id"], device_b["device_id"])
    assert heartbeat(client, headers_a).status_code == 200
    assert heartbeat(client, headers_b).status_code == 200
    save_content(client, "Lifecycle")

    assert client.post("/run-campaign").status_code == 302
    job_a = client.get("/api/agent/job", headers=headers_a).get_json()["job"]
    job_b = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert report(client, headers_a, job_a, "finished", success=1).status_code == 200
    assert report(client, headers_b, job_b, "error", errors=1).status_code == 200
    partial = module.load_engine_campaigns(user_id)[0]
    assert module.sync_engine_campaign(user_id, partial["campaign_id"])["lifecycle"] == "partial_failed"

    assert client.post("/run-campaign").status_code == 302
    running_campaign = module.load_engine_campaigns(user_id)[-1]
    job_a = client.get("/api/agent/job", headers=headers_a).get_json()["job"]
    job_b = client.get("/api/agent/job", headers=headers_b).get_json()["job"]
    assert client.post("/pause-campaign").status_code == 302
    assert module.get_engine_campaign(user_id, running_campaign["campaign_id"])["lifecycle"] == "paused"
    assert report(client, headers_a, job_a, "paused").status_code == 200
    assert report(client, headers_b, job_b, "paused").status_code == 200
    assert client.post("/resume-campaign").status_code == 302
    assert report(client, headers_a, job_a, "running").status_code == 200
    assert report(client, headers_b, job_b, "running").status_code == 200
    assert report(client, headers_a, job_a, "finished", success=1).status_code == 200
    assert report(client, headers_b, job_b, "finished", success=1).status_code == 200
    completed = module.sync_engine_campaign(user_id, running_campaign["campaign_id"])
    assert completed["lifecycle"] == "completed", (completed, module.load_engine_tasks(user_id, running_campaign["campaign_id"]))

    snapshot = module.build_account_group_snapshot(user_id, urls)
    draft = module.create_engine_campaign(
        user_id, "Draft", snapshot, {"content": "draft", "images": [], "min_delay": 0, "max_delay": 0}, "draft"
    )
    assert draft["lifecycle"] == "draft"
    assert all(task["status"] == "draft" for task in module.load_engine_tasks(user_id, draft["campaign_id"]))
    module.cancel_engine_campaign(user_id, draft["campaign_id"])
    assert module.get_engine_campaign(user_id, draft["campaign_id"])["lifecycle"] == "cancelled"
    print("PASS lifecycle: draft/queued/running/paused/completed/partial_failed/cancelled persist correctly")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fbpp-phase8-") as temp:
        run_main_engine_checks(temp)
    with tempfile.TemporaryDirectory(prefix="fbpp-phase8-schedule-") as temp:
        run_scheduler_checks(temp)
    with tempfile.TemporaryDirectory(prefix="fbpp-phase8-lifecycle-") as temp:
        run_lifecycle_checks(temp)
