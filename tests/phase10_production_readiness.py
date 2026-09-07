"""Phase 10 production-hardening checks. Never contacts or posts to Facebook."""
from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from phase6_worker_command_regression import load_app, register, pair, heartbeat


ROOT = Path(__file__).resolve().parents[1]


def run_checks(temp):
    module = load_app(temp)
    client = module.app.test_client()
    user_id = register(client, "phase10_owner")

    # Process and database readiness endpoints are public, light, and non-sensitive.
    health = client.get("/health", headers={"X-Request-ID": "phase10-request-001"})
    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "phase10-request-001"
    assert "DATA_ROOT" not in health.get_data(as_text=True)
    assert client.get("/ready").status_code == 200
    original_enabled, original_connect = module.postgres_enabled, module.postgres_connect
    module.postgres_enabled = lambda: True
    module.postgres_connect = lambda: (_ for _ in ()).throw(RuntimeError("database unavailable"))
    unavailable = client.get("/ready")
    assert unavailable.status_code == 503 and unavailable.get_json() == {"status": "not_ready", "database": "unavailable"}
    module.postgres_enabled, module.postgres_connect = original_enabled, original_connect
    print("PASS health/readiness: process check is safe and database outage returns 503")

    # New device credentials are stored only as a digest; wrong/cross-tenant tokens fail.
    paired, headers = pair(client, "phase10")
    stored = module.load_devices(user_id)[paired["device_id"]]
    assert stored.get("token_hash") == module.hash_device_token(paired["token"])
    assert "token" not in stored and paired["token"] not in str(stored)
    wrong = {"X-Device-ID": paired["device_id"], "X-Agent-Token": "wrong-token"}
    assert heartbeat(client, wrong).status_code == 401

    other = module.app.test_client()
    other_id = register(other, "phase10_other")
    other_pair, _ = pair(other, "phase10-other")
    cross = {"X-Device-ID": paired["device_id"], "X-Agent-Token": other_pair["token"]}
    assert heartbeat(client, cross).status_code == 401
    assert other_id != user_id

    # A valid legacy plaintext token is upgraded on first successful auth.
    devices = module.load_devices(user_id)
    devices[paired["device_id"]].pop("token_hash", None)
    devices[paired["device_id"]]["token"] = paired["token"]
    module.save_devices(user_id, devices)
    assert heartbeat(client, headers).status_code == 200
    migrated = module.load_devices(user_id)[paired["device_id"]]
    assert migrated.get("token_hash") and "token" not in migrated

    # Disconnect/re-pair is the supported revoke/rotation flow.
    assert client.post("/connector/disconnect").status_code == 302
    assert heartbeat(client, headers).status_code == 401
    print("PASS worker auth: hash-at-rest, wrong/cross-tenant rejection, legacy migration and revoke")

    # Worker/browser endpoints reject malformed JSON shapes and out-of-bound counters.
    code = client.post("/api/extension/pair-code").get_json()["code"]
    assert client.post("/api/extension/pair", data="[]", content_type="application/json").status_code == 400
    replacement = client.post("/api/extension/pair", json={"code": code, "device_name": "Replacement"}).get_json()
    replacement_headers = {"X-Device-ID": replacement["device_id"], "X-Agent-Token": replacement["token"]}
    assert client.post("/api/agent/heartbeat", headers=replacement_headers, data="[]", content_type="application/json").status_code == 400
    assert client.post("/api/agent/status", headers=replacement_headers, json={"status": "running", "processed": -1}).status_code == 400
    assert client.post("/api/agent/status", headers=replacement_headers, json={"status": "invented"}).status_code == 400
    bad_upload = client.post("/save-post", data={
        "content": "must roll back", "min_delay": "0", "max_delay": "0",
        "images": (BytesIO(b"<script>alert(1)</script>"), "renamed.png"),
    }, content_type="multipart/form-data")
    assert bad_upload.status_code == 302 and not module.load_settings(user_id).get("post_images")
    print("PASS input validation: malformed JSON, invalid status and counter bounds rejected")

    # Secret redaction covers structured keys, bearer values and PostgreSQL URLs.
    unsafe = "database=postgresql://alice:db-password@db.example/app Bearer abc.DEF-123 password=hunter2"
    safe = str(module._safe_log_value(unsafe))
    assert "db-password" not in safe and "abc.DEF-123" not in safe and "hunter2" not in safe
    assert safe.count("[REDACTED]") >= 3
    module.record_operational_log(user_id, "phase10_secret_test", "warning", unsafe)
    stored_log = module.load_operational_logs({"customer_id": user_id, "query": "phase10_secret_test"})
    # Query matches event_type; persisted message must remain redacted.
    assert stored_log[1] == 1 and "db-password" not in stored_log[0][0]["message"]
    print("PASS log security: database credentials, bearer tokens and passwords are redacted")

    # Retention touches only operational/audit logs, never campaign or task files.
    now = module.utc_now()
    old = (now - timedelta(days=500)).isoformat(timespec="seconds")
    recent = now.isoformat(timespec="seconds")
    module.write_json(module.OPERATIONAL_LOGS_FILE, [
        {"log_id": "old", "created_at": old}, {"log_id": "new", "created_at": recent},
    ])
    module.write_json(module.ADMIN_AUDIT_LOGS_FILE, [
        {"audit_id": "old", "created_at": old}, {"audit_id": "new", "created_at": recent},
    ])
    campaign_file = module.customer_engine_campaigns_file(user_id)
    task_file = module.customer_engine_tasks_file(user_id)
    module.write_json(campaign_file, [{"campaign_id": "keep-campaign"}])
    module.write_json(task_file, [{"task_id": "keep-task"}])
    before_campaigns, before_tasks = campaign_file.read_bytes(), task_file.read_bytes()
    cleanup = module.cleanup_expired_logs(now)
    assert cleanup == {"operational_deleted": 1, "audit_deleted": 1}
    assert campaign_file.read_bytes() == before_campaigns and task_file.read_bytes() == before_tasks
    print("PASS retention: expired logs removed; campaign/task persistence untouched")

    # Same-origin policy and response headers activate in production mode.
    original_production = module.IS_PRODUCTION
    module.IS_PRODUCTION = True
    module.LOG_CLEANUP_NEXT_AT = float("inf")
    blocked = client.post("/save-post", data={"content": "blocked"}, base_url="https://app.example")
    assert blocked.status_code == 403
    allowed = client.post(
        "/save-post", data={"content": "allowed", "min_delay": "0", "max_delay": "0"},
        base_url="https://app.example", headers={"Origin": "https://app.example"},
    )
    assert allowed.status_code == 302
    secured = client.get("/health", base_url="https://app.example")
    assert "max-age=" in secured.headers.get("Strict-Transport-Security", "")
    assert "frame-ancestors 'none'" in secured.headers.get("Content-Security-Policy", "")
    module.IS_PRODUCTION = original_production
    print("PASS web security: same-origin mutation guard, HSTS and CSP")

    # Production startup must fail closed when core secrets/storage are absent.
    base_env = os.environ.copy()
    base_env.update(APP_ENV="production", PYTHONPATH=str(ROOT), DATA_ROOT=str(Path(temp) / "prod-data"), ALLOWED_HOSTS="app.example")
    weak_env = dict(base_env, SECRET_KEY="weak", DATABASE_URL="")
    weak = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=weak_env, capture_output=True, text=True)
    assert weak.returncode != 0 and "strong SECRET_KEY" in (weak.stdout + weak.stderr)
    no_db_env = dict(base_env, SECRET_KEY="S" * 48, DATABASE_URL="")
    no_db = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=no_db_env, capture_output=True, text=True)
    assert no_db.returncode != 0 and "requires DATABASE_URL" in (no_db.stdout + no_db.stderr)
    weak_worker_env = dict(base_env, SECRET_KEY="S" * 48, DATABASE_URL="postgresql://localhost/unused", CLOUD_WORKER_TOKEN="short")
    weak_worker = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=weak_worker_env, capture_output=True, text=True)
    assert weak_worker.returncode != 0 and "CLOUD_WORKER_TOKEN" in (weak_worker.stdout + weak_worker.stderr)
    no_host_env = dict(base_env, SECRET_KEY="S" * 48, DATABASE_URL="postgresql://localhost/unused")
    no_host_env.pop("ALLOWED_HOSTS", None)
    no_host_env.pop("RENDER_EXTERNAL_HOSTNAME", None)
    no_host = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=no_host_env, capture_output=True, text=True)
    assert no_host.returncode != 0 and "ALLOWED_HOSTS" in (no_host.stdout + no_host.stderr)
    assert module.app.debug is False
    limiter = module.app.test_client()
    for _ in range(10):
        assert limiter.post("/login", data={"login": "missing", "password": "wrong"}).status_code == 200
    assert limiter.post("/login", data={"login": "missing", "password": "wrong"}).status_code == 429
    print("PASS production fail-safe: weak secrets, missing PostgreSQL/host allowlist rejected; debug disabled")

    # Deployment, migration and recovery artifacts encode the intended safety policy.
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    backup_source = (ROOT / "scripts" / "backup_postgres.py").read_text(encoding="utf-8")
    restore_source = (ROOT / "scripts" / "restore_verify.py").read_text(encoding="utf-8")
    assert "APP_ENV" in render and "value: production" in render
    assert "plan: 0.5c-512mb" in render and "autoDeployTrigger: off" in render
    assert "healthCheckPath: /ready" in render
    assert "CREATE TABLE IF NOT EXISTS fbpostpro_schema_migrations" in app_source
    assert "ON CONFLICT (migration_id) DO NOTHING" in app_source
    assert "DROP TABLE" not in app_source.upper() and "TRUNCATE" not in app_source.upper()
    assert "pg_dump" in backup_source and "DATABASE_URL" not in backup_source.split("subprocess.run", 1)[1].split("check=True", 1)[0]
    assert "ALLOW_TEST_RESTORE" in restore_source and "Refusing to restore into DATABASE_URL" in restore_source
    print("PASS config/migrations/backup: additive registry, manual deploy gate and guarded restore")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fbpp-phase10-") as temp:
        run_checks(temp)
