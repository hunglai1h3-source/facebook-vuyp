"""End-to-end regression checks for Phone/Web -> Server -> Desktop Worker."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def load_app(temp):
    os.environ.update(
        DATA_ROOT=str(Path(temp) / "data"),
        USERS_FILE=str(Path(temp) / "users.json"),
        DATABASE_URL="",
        SECRET_KEY="phase6-test-secret",
    )
    spec = importlib.util.spec_from_file_location("app", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    return module


def register(client, suffix):
    response = client.post("/register", data={
        "display_name": f"Phase 6 {suffix}",
        "username": f"phase6_{suffix}",
        "email": f"phase6_{suffix}@example.test",
        "password": "Phase6Test123!",
        "confirm_password": "Phase6Test123!",
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        user_id = session["user_id"]
    client.post("/save-post", data={
        "campaign_name": f"Campaign {suffix}", "content": f"Content {suffix}",
        "min_delay": "0", "max_delay": "0",
    })
    client.post("/add-group", data={
        "group_url": f"https://www.facebook.com/groups/phase6{suffix}"
    })
    return user_id


def login(client, suffix):
    response = client.post("/login", data={
        "login": f"phase6_{suffix}", "password": "Phase6Test123!"
    })
    assert response.status_code == 302


def pair(client, suffix):
    code = client.post("/api/extension/pair-code").get_json()["code"]
    result = client.post("/api/extension/pair", json={
        "code": code, "device_name": f"Worker {suffix}", "extension_version": "1.1.0"
    })
    assert result.status_code == 200
    data = result.get_json()
    return data, {"X-Device-ID": data["device_id"], "X-Agent-Token": data["token"]}


def heartbeat(client, header, state="idle", job_id=""):
    return client.post("/api/agent/heartbeat", headers=header, json={
        "facebook_logged_in": True,
        "device_name": "Phase 6 Worker",
        "worker_state": state,
        "current_job_id": job_id,
    })


def run_checks(module):
    alice = module.app.test_client()
    bob = module.app.test_client()
    alice_id = register(alice, "alice")
    bob_id = register(bob, "bob")
    alice_pair, alice_agent = pair(alice, "alice")
    assert heartbeat(alice, alice_agent).status_code == 200

    # 1) An online worker gets exactly one job even when two web clients start together.
    alice_second = module.app.test_client()
    login(alice_second, "alice")
    barrier = threading.Barrier(2)
    results = []
    def start(client):
        barrier.wait()
        results.append(client.post("/run-campaign").status_code)
    threads = [threading.Thread(target=start, args=(client,)) for client in (alice, alice_second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert results == [302, 302]
    assert len(module.load_campaign_records(alice_id)) == 1
    claimed = alice.get("/api/agent/job", headers=alice_agent).get_json()
    assert claimed["has_job"] is True
    alice_job = claimed["job"]
    assert alice_job["device_id"] == alice_pair["device_id"]
    assert alice.get("/api/agent/job", headers=alice_agent).get_json()["has_job"] is False
    print("PASS 1: worker online -> Start -> correct worker claims one idempotent job")

    # 2) Pairing alone is offline. Start is durable but not claimable until explicit Resume.
    bob_pair, bob_agent = pair(bob, "bob")
    assert bob.post("/run-campaign").status_code == 302
    bob_state = bob.get("/campaign-status").get_json()
    assert bob_state["status"] == "waiting_worker"
    assert bob_state["display_status"] == "worker_offline"
    assert bob.get("/api/agent/job", headers=bob_agent).get_json()["has_job"] is False
    assert bob.post("/resume-campaign").status_code == 302
    assert module.load_jobs(bob_id)[bob_pair["device_id"]]["status"] == "waiting_worker"
    print("PASS 2: worker offline -> Start preserves a non-claimable waiting job")

    # 3) Every agent token resolves its own tenant; Bob cannot claim or update Alice's job.
    assert bob.get("/api/agent/job", headers=bob_agent).get_json()["has_job"] is False
    cross = bob.post("/api/agent/status", headers=bob_agent, json={
        "job_id": alice_job["job_id"], "status": "finished",
        "processed": 1, "success": 1, "errors": 0,
    })
    assert cross.status_code == 409
    assert module.get_campaign_state(alice_id)["job_id"] == alice_job["job_id"]
    print("PASS 3: User B cannot claim or update User A campaign/worker state")

    # 4) Campaign state is server-persisted and survives a phone page refresh/request.
    running = alice.post("/api/agent/status", headers=alice_agent, json={
        "job_id": alice_job["job_id"], "status": "running",
        "processed": 0, "success": 0, "errors": 0, "message": "Running",
    })
    assert running.status_code == 200
    first = alice.get("/campaign-status").get_json()
    second = alice.get("/campaign-status").get_json()
    assert first["job_id"] == second["job_id"] == alice_job["job_id"]
    assert second["display_status"] == "running"
    print("PASS 4: phone refresh preserves campaign ID, counters and running state")

    # Pause/resume use command IDs and preserve job ownership.
    assert alice.post("/pause-campaign").status_code == 302
    pause = alice.get("/api/agent/control", headers=alice_agent).get_json()
    assert pause["command_id"].startswith("cmd_") and pause["pause_requested"] is True
    assert alice.post("/api/agent/control/ack", headers=alice_agent, json={
        "command_id": pause["command_id"], "pause_ack": True
    }).status_code == 200
    pause_log = [item for item in module.load_command_log(alice_id) if item["command_id"] == pause["command_id"]]
    assert len(pause_log) == 1 and pause_log[0]["command_status"] == "acknowledged"
    assert alice.post("/api/agent/status", headers=alice_agent, json={
        "job_id": alice_job["job_id"], "status": "paused",
        "processed": 0, "success": 0, "errors": 0,
    }).status_code == 200
    assert alice.post("/resume-campaign").status_code == 302
    resume = alice.get("/api/agent/control", headers=alice_agent).get_json()
    assert resume["command_type"] == "resume" and resume["job_id"] == alice_job["job_id"]

    # 5) Completion is terminal; reconnect and duplicate report cannot execute/recount it.
    done = alice.post("/api/agent/status", headers=alice_agent, json={
        "job_id": alice_job["job_id"], "status": "finished",
        "processed": 1, "success": 1, "errors": 0, "message": "Done",
    })
    assert done.status_code == 200
    duplicate = alice.post("/api/agent/status", headers=alice_agent, json={
        "job_id": alice_job["job_id"], "status": "finished",
        "processed": 1, "success": 1, "errors": 0, "message": "Done",
    })
    assert duplicate.status_code == 200 and duplicate.get_json()["duplicate"] is True
    assert heartbeat(alice, alice_agent).status_code == 200
    assert alice.get("/api/agent/job", headers=alice_agent).get_json()["has_job"] is False
    print("PASS 5: worker reconnect and duplicate terminal report do not replay a completed job")

    # 6) Offline job becomes claimable only after heartbeat + explicit resume, then completes.
    assert heartbeat(bob, bob_agent).status_code == 200
    assert bob.post("/resume-campaign").status_code == 302
    bob_claim = bob.get("/api/agent/job", headers=bob_agent).get_json()
    assert bob_claim["has_job"] is True
    bob_job = bob_claim["job"]
    assert bob.post("/api/agent/status", headers=bob_agent, json={
        "job_id": bob_job["job_id"], "status": "finished",
        "processed": 1, "success": 1, "errors": 0, "message": "Completed",
    }).status_code == 200
    completed = bob.get("/campaign-status").get_json()
    assert completed["display_status"] == "completed" and completed["success"] == 1
    print("PASS 6: campaign completion is visible to phone/web with correct counters")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fbpp-phase6-") as temp:
        run_checks(load_app(temp))
