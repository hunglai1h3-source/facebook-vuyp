"""Destructive only to explicitly named localhost Phase 10 test databases."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_postgres_app(data_root, database_url, app_env="development"):
    os.environ.update(
        APP_ENV=app_env,
        SECRET_KEY="phase10-postgres-integration-secret-that-is-long-enough",
        DATABASE_URL=database_url,
        DATA_ROOT=str(data_root),
        USERS_FILE=str(Path(data_root) / "users.json"),
        ENABLE_LEGACY_ADMIN_AUTH="false",
        ENABLE_SCHEDULER="false",
        ALLOWED_HOSTS="app.example" if app_env == "production" else "",
    )
    spec = importlib.util.spec_from_file_location("app", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    return module


def register(client, suffix):
    response = client.post("/register", data={
        "display_name": f"Postgres {suffix}",
        "username": f"pg_{suffix}",
        "email": f"pg_{suffix}@example.test",
        "password": "Phase10Postgres!",
        "confirm_password": "Phase10Postgres!",
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session["user_id"]


def pair(client, suffix):
    code = client.post("/api/extension/pair-code").get_json()["code"]
    response = client.post("/api/extension/pair", json={"code": code, "device_name": f"PG {suffix}"})
    assert response.status_code == 200
    data = response.get_json()
    return data, {"X-Device-ID": data["device_id"], "X-Agent-Token": data["token"]}


def run(database_url, restore_url):
    parsed = urlsplit(database_url)
    restore_parsed = urlsplit(restore_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or restore_parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing to run destructive integration checks outside localhost.")
    if not parsed.path.lstrip("/").startswith("fbpostpro_phase10") or not restore_parsed.path.lstrip("/").startswith("fbpostpro_restore"):
        raise SystemExit("Test database names must use fbpostpro_phase10*/fbpostpro_restore*.")

    with tempfile.TemporaryDirectory(prefix="fbpp-pg-app-") as temp:
        module = load_postgres_app(Path(temp), database_url)
        module.PERSISTENCE_TABLES_READY = False
        module.init_persistence_tables()
        module.PERSISTENCE_TABLES_READY = False
        module.init_persistence_tables()
        module.init_groups_table()
        with module.postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT migration_id FROM fbpostpro_schema_migrations ORDER BY migration_id")
                migrations = {row["migration_id"] for row in cur.fetchall()}
                cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename LIKE 'fbpostpro_%'")
                indexes = {row["indexname"] for row in cur.fetchall()}
        assert {"account_system_v1", "phase07_bulk_groups_v1", "phase09_admin_operations_v1", "phase10_production_hardening_v2"} <= migrations
        assert {"idx_fbpostpro_tasks_scheduler", "idx_fbpostpro_tasks_group", "idx_fbpostpro_logs_campaign_created", "idx_fbpostpro_devices_lookup"} <= indexes
        print("PASS PostgreSQL migration: repeated additive migration and registry/index verification")

        owner = module.app.test_client()
        owner_id = register(owner, "owner")
        admin = module.app.test_client()
        admin_id = register(admin, "admin")
        users = module.load_users()
        users[admin_id]["role"] = "admin"
        users[owner_id]["max_facebook_accounts"] = 2
        users[owner_id]["max_groups"] = 500
        module.save_users(users)

        urls = [f"https://www.facebook.com/groups/pg-{index:03d}" for index in range(120)]
        result = module.import_groups(owner_id, urls)
        assert len(result["added"]) == 120
        account = module.create_facebook_account(owner_id, "PG Account", "pg-facebook-1")
        device, headers = pair(owner, "worker")
        module.bind_facebook_account_device(owner_id, account["account_id"], device["device_id"])
        module.save_group_assignments(owner_id, [
            {"group_url": url, "account_id": account["account_id"]} for url in urls
        ])
        heartbeat = owner.post("/api/agent/heartbeat", headers=headers, json={
            "facebook_logged_in": True, "worker_state": "idle", "current_job_id": "",
        })
        assert heartbeat.status_code == 200
        owner.post("/save-post", data={"campaign_name": "PG campaign", "content": "No Facebook post", "min_delay": "0", "max_delay": "0"})
        assert owner.post("/run-campaign").status_code == 302
        campaign = module.load_engine_campaigns(owner_id)[0]
        tasks = module.load_engine_tasks(owner_id, campaign["campaign_id"])
        assert len(tasks) == 120

        # Exercise every PostgreSQL-backed paginated admin query added in Phase 10.
        for path in (
            "/admin", "/admin/users?q=pg_owner", "/admin/accounts",
            "/admin/groups?page=2", "/admin/campaigns",
            f"/admin/campaigns/{campaign['campaign_id']}", "/admin/workers", "/admin/logs", "/admin/audit",
        ):
            response = admin.get(path)
            assert response.status_code == 200, (path, response.status_code, response.get_data(as_text=True)[:300])
        assert admin.get("/ready").status_code == 200
        assert owner.get("/api/admin/dashboard").status_code == 403
        assert owner.post(f"/api/admin/users/{admin_id}/status", json={"is_active": False}).status_code == 403
        print("PASS PostgreSQL admin/IDOR: paginated pages and readiness work; tenant admin APIs remain forbidden")

        # Reload against the same database: schedule/tasks/device digest survive and are not duplicated.
        before_task_ids = {item["task_id"] for item in tasks}
        module = load_postgres_app(Path(temp) / "restart", database_url)
        after = module.load_engine_tasks(owner_id, campaign["campaign_id"])
        assert {item["task_id"] for item in after} == before_task_ids
        stored_device = module.load_devices(owner_id)[device["device_id"]]
        assert stored_device.get("token_hash") and "token" not in stored_device
        print("PASS PostgreSQL restart: campaign/task/device state persists with stable task IDs")

        production = load_postgres_app(Path(temp) / "production", database_url, app_env="production")
        assert production.app.config["SESSION_COOKIE_SECURE"] is True
        assert production.app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert production.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert production.app.config["SESSION_REFRESH_EACH_REQUEST"] is False
        assert int(production.app.permanent_session_lifetime.total_seconds()) == 12 * 3600
        assert production.LEGACY_ADMIN_AUTH_ENABLED is False and production.app.debug is False
        prod_client = production.app.test_client()
        assert prod_client.get("/ready", base_url="https://app.example").status_code == 200
        assert prod_client.get("/ready", base_url="https://host-header-attacker.example").status_code == 400
        assert prod_client.post("/login", data={}, base_url="https://app.example").status_code == 403
        print("PASS production runtime: secure cookie policy, absolute expiry, role admin and same-origin guard")

        # Safe-scale query check: a few thousand terminal tasks/logs remain paginated.
        with module.postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO fbpostpro_campaign_tasks (
                         task_id, campaign_id, customer_id, account_id, device_id,
                         browser_profile_id, session_context, group_id, group_url,
                         status, idempotency_key, retry_count, max_retries, finished_at
                       )
                       SELECT 'load_task_' || n, %s, %s, %s, %s, '', '',
                              'load_group_' || n, 'https://www.facebook.com/groups/load-' || n,
                              'successful', 'load_idempotency_' || n, 0, 1, NOW()
                       FROM generate_series(1, 2000) n""",
                    (campaign["campaign_id"], owner_id, account["account_id"], device["device_id"]),
                )
                cur.execute(
                    """UPDATE fbpostpro_campaign_engine
                       SET total=total+2000, successful=successful+2000, updated_at=NOW()
                       WHERE campaign_id=%s""",
                    (campaign["campaign_id"],),
                )
                cur.execute(
                    """INSERT INTO fbpostpro_operational_logs
                         (log_id, customer_id, campaign_id, event_type, severity, message, created_at)
                       SELECT 'load_log_' || n, %s, %s, 'load_test', 'info', 'safe load row ' || n, NOW()
                       FROM generate_series(1, 5000) n""",
                    (owner_id, campaign["campaign_id"]),
                )
                cur.execute(
                    """INSERT INTO fbpostpro_operational_logs
                         (log_id, customer_id, campaign_id, event_type, severity, message, created_at)
                       SELECT 'expired_load_log_' || n, %s, %s, 'load_test_old', 'info', 'expired row', NOW() - INTERVAL '45 days'
                       FROM generate_series(1, 50) n""",
                    (owner_id, campaign["campaign_id"]),
                )
            conn.commit()
        started = time.perf_counter()
        assert admin.get("/admin/campaigns?q=PG%20campaign").status_code == 200
        detail_page = admin.get(f"/admin/campaigns/{campaign['campaign_id']}?page=20")
        assert detail_page.status_code == 200
        assert admin.get(f"/admin/logs?customer_id={owner_id}&campaign_id={campaign['campaign_id']}&q=load_test&page=50").status_code == 200
        elapsed = time.perf_counter() - started
        assert elapsed < 10, elapsed
        cleanup = module.cleanup_expired_logs()
        assert cleanup["operational_deleted"] >= 50
        assert len(module.load_engine_tasks(owner_id, campaign["campaign_id"])) == 2120
        print(f"PASS PostgreSQL safe load: 2,120 tasks/5,000 logs paginated in {elapsed:.3f}s; cleanup preserved tasks")

        # Run the actual pg_dump and guarded restore scripts against isolated databases.
        backup_dir = Path(temp) / "backups"
        tool_bin = str(Path(r"C:\Program Files\PostgreSQL\18\bin"))
        child_env = os.environ.copy()
        child_env.update(DATABASE_URL=database_url, BACKUP_DIR=str(backup_dir), BACKUP_RETENTION_DAYS="30")
        child_env["PATH"] = tool_bin + os.pathsep + child_env.get("PATH", "")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "backup_postgres.py")], cwd=ROOT, env=child_env, check=True)
        backup = next(backup_dir.glob("*.dump"))
        restore_env = child_env.copy()
        restore_env.update(RESTORE_DATABASE_URL=restore_url, ALLOW_TEST_RESTORE="yes")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "restore_verify.py"), str(backup)], cwd=ROOT, env=restore_env, check=True)
        print("PASS PostgreSQL backup/restore: custom dump, checksum and isolated restore verification")


if __name__ == "__main__":
    database_url = os.environ.get("PHASE10_POSTGRES_TEST_URL", "")
    restore_url = os.environ.get("PHASE10_POSTGRES_RESTORE_URL", "")
    if not database_url or not restore_url:
        raise SystemExit("Set PHASE10_POSTGRES_TEST_URL and PHASE10_POSTGRES_RESTORE_URL.")
    run(database_url, restore_url)
