"""Phase 9 admin operations built on the existing application services."""
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
import json
import math

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for


TERMINAL_CAMPAIGNS = {"completed", "partial_failed", "failed", "cancelled"}
LEGACY_STATUS_MAP = {
    "pending": "queued", "waiting_worker": "queued", "posting": "running",
    "finished": "completed", "finished_with_errors": "partial_failed",
    "stopped": "cancelled", "error": "failed",
}


def register_admin_ops(app, services):
    bp = Blueprint("admin_ops", __name__)
    admin_required = services["admin_required"]

    def _int_arg(name, default=1, low=1, high=1000000):
        try:
            return min(high, max(low, int(request.args.get(name, default))))
        except (TypeError, ValueError):
            return default

    def _paginate(items, page, per_page):
        total = len(items)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)
        start = (page - 1) * per_page
        return items[start:start + per_page], {"page": page, "pages": pages, "total": total, "per_page": per_page}

    def _pagination(total, page, per_page):
        pages = max(1, math.ceil(total / per_page))
        page = min(max(1, page), pages)
        return {"page": page, "pages": pages, "total": total, "per_page": per_page}

    def _postgres_campaign_page(query, owner, status, page, per_page):
        """Page campaigns in PostgreSQL before loading task details."""
        clauses, params = ["1=1"], []
        if owner:
            clauses.append("customer_id=%s")
            params.append(owner)
        if status:
            clauses.append("lifecycle=%s")
            params.append(status)
        if query:
            clauses.append("(LOWER(campaign_name) LIKE %s OR LOWER(campaign_id) LIKE %s)")
            params.extend([f"%{query}%", f"%{query}%"])
        where = " AND ".join(clauses)
        combined = """
            SELECT campaign_id, customer_id, campaign_name, lifecycle, scheduled_at,
                   total, successful, failed, cancelled, created_at, started_at,
                   finished_at, updated_at, 'engine'::text AS kind
            FROM fbpostpro_campaign_engine
            UNION ALL
            SELECT job_id AS campaign_id, customer_id, campaign_name,
                   CASE status
                     WHEN 'pending' THEN 'queued' WHEN 'waiting_worker' THEN 'queued'
                     WHEN 'posting' THEN 'running' WHEN 'finished' THEN 'completed'
                     WHEN 'finished_with_errors' THEN 'partial_failed'
                     WHEN 'stopped' THEN 'cancelled' WHEN 'error' THEN 'failed'
                     ELSE status END AS lifecycle,
                   scheduled_at, total, success AS successful, errors AS failed,
                   0 AS cancelled, created_at, started_at, finished_at, updated_at,
                   'legacy'::text AS kind
            FROM fbpostpro_campaigns
        """
        with services["postgres_connect"]() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM ({combined}) campaigns WHERE {where}", params)
                total = int((cur.fetchone() or {}).get("total", 0))
                pagination = _pagination(total, page, per_page)
                cur.execute(
                    f"SELECT * FROM ({combined}) campaigns WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [per_page, (pagination["page"] - 1) * per_page],
                )
                campaigns = [dict(row) for row in cur.fetchall()]
                engine_ids = [item["campaign_id"] for item in campaigns if item.get("kind") == "engine"]
                if engine_ids:
                    cur.execute(
                        """SELECT campaign_id, status, COUNT(*) AS total,
                                  COALESCE(SUM(retry_count),0) AS retries,
                                  ARRAY_AGG(DISTINCT device_id) FILTER (WHERE device_id<>'') AS device_ids
                           FROM fbpostpro_campaign_tasks WHERE campaign_id=ANY(%s)
                           GROUP BY campaign_id, status""",
                        (engine_ids,),
                    )
                    summary_rows = cur.fetchall()
                else:
                    summary_rows = []
        summaries = {}
        for row in summary_rows:
            summary = summaries.setdefault(row.get("campaign_id", ""), {"counts": Counter(), "retries": 0, "device_ids": set()})
            summary["counts"][row.get("status", "")] = int(row.get("total", 0))
            summary["retries"] += int(row.get("retries", 0) or 0)
            summary["device_ids"].update(row.get("device_ids") or [])
        return campaigns, summaries, pagination

    def _users():
        result = []
        for user_id, raw in services["load_users"]().items():
            if not isinstance(raw, dict):
                continue
            user = dict(raw)
            user["user_id"] = user_id
            user.pop("password_hash", None)
            result.append(user)
        return result

    def _all_devices(users=None):
        users = users or _users()
        rows = []
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT customer_id, data FROM fbpostpro_customer_data WHERE data_key='devices'")
                    datasets = cur.fetchall()
            source = {row.get("customer_id", ""): row.get("data") or {} for row in datasets}
        else:
            source = {user["user_id"]: services["load_devices"](user["user_id"]) for user in users}
        for customer_id, devices in source.items():
            if not isinstance(devices, dict):
                continue
            for device_id, raw in devices.items():
                device = dict(raw or {})
                device.pop("token", None)
                device.pop("token_hash", None)
                device.pop("agent_token", None)
                device.update({
                    "customer_id": customer_id,
                    "device_id": device_id,
                    "online": services["device_is_online"](device),
                })
                rows.append(device)
        return rows

    def _all_accounts(users=None):
        users = users or _users()
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT customer_id, account_id, display_name, facebook_user_id,
                                  status, device_id, browser_profile_id, created_at, updated_at
                           FROM fbpostpro_accounts ORDER BY created_at DESC"""
                    )
                    return cur.fetchall()
        rows = []
        for user in users:
            for raw in services["load_facebook_accounts"](user["user_id"]):
                rows.append({**raw, "customer_id": user["user_id"]})
        return rows

    def _all_groups(users=None):
        users = users or _users()
        if services["postgres_enabled"]():
            services["init_groups_table"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT g.customer_id, g.group_url, a.account_id
                           FROM fbpostpro_groups g
                           LEFT JOIN fbpostpro_group_assignments a
                             ON a.customer_id=g.customer_id AND a.group_url=g.group_url
                           ORDER BY g.customer_id, g.group_url"""
                    )
                    return cur.fetchall()
        rows = []
        for user in users:
            assignments = services["load_group_assignments"](user["user_id"])
            for group_url in services["load_groups"](user["user_id"]):
                rows.append({"customer_id": user["user_id"], "group_url": group_url, "account_id": assignments.get(group_url, "")})
        return rows

    def _all_campaigns(users=None):
        users = users or _users()
        rows = []
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fbpostpro_campaign_engine ORDER BY created_at DESC")
                    engine = cur.fetchall()
                    cur.execute("SELECT * FROM fbpostpro_campaigns ORDER BY created_at DESC")
                    legacy = cur.fetchall()
            rows.extend({**item, "kind": "engine", "status": item.get("lifecycle", "draft")} for item in engine)
            rows.extend({**item, "kind": "legacy", "campaign_id": item.get("job_id", ""), "lifecycle": item.get("status", "pending")} for item in legacy)
            return rows
        for user in users:
            customer_id = user["user_id"]
            rows.extend({**item, "customer_id": customer_id, "kind": "engine", "status": item.get("lifecycle", "draft")} for item in services["load_engine_campaigns"](customer_id))
            rows.extend({**item, "customer_id": customer_id, "kind": "legacy", "campaign_id": item.get("job_id", ""), "lifecycle": item.get("status", "pending")} for item in services["load_campaign_records"](customer_id, limit=10000))
        rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return rows

    def _all_tasks(users=None):
        users = users or _users()
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fbpostpro_campaign_tasks ORDER BY created_at DESC")
                    rows = cur.fetchall()
        else:
            rows = []
            for user in users:
                rows.extend(services["load_engine_tasks"](user["user_id"]))
        safe_rows = []
        for raw in rows:
            task = dict(raw)
            task.pop("lease_token", None)
            task["last_error"] = str(services["safe_log_value"](task.get("last_error", "")))
            safe_rows.append(task)
        return safe_rows

    def _dashboard_metrics():
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            services["init_groups_table"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) total, COUNT(*) FILTER (WHERE is_active) active FROM fbpostpro_users")
                    user_counts = cur.fetchone() or {}
                    cur.execute("SELECT COUNT(*) total FROM fbpostpro_accounts")
                    accounts = int((cur.fetchone() or {}).get("total", 0))
                    cur.execute("SELECT COUNT(*) total FROM fbpostpro_groups")
                    groups = int((cur.fetchone() or {}).get("total", 0))
                    cur.execute("SELECT lifecycle, COUNT(*) total FROM fbpostpro_campaign_engine GROUP BY lifecycle")
                    lifecycle = {row["lifecycle"]: int(row["total"]) for row in cur.fetchall()}
                    cur.execute("SELECT status, COUNT(*) total FROM fbpostpro_campaign_tasks GROUP BY status")
                    task_counts = {row["status"]: int(row["total"]) for row in cur.fetchall()}
                    cur.execute("SELECT status, COUNT(*) total, COALESCE(SUM(success),0) success, COALESCE(SUM(errors),0) failed FROM fbpostpro_campaigns GROUP BY status")
                    legacy_rows = cur.fetchall()
                    for row in legacy_rows:
                        mapped = LEGACY_STATUS_MAP.get(row.get("status", ""), row.get("status", ""))
                        lifecycle[mapped] = lifecycle.get(mapped, 0) + int(row.get("total", 0))
                        task_counts["successful"] = task_counts.get("successful", 0) + int(row.get("success", 0))
                        task_counts["failed"] = task_counts.get("failed", 0) + int(row.get("failed", 0))
                    cur.execute(
                        """SELECT COUNT(*) AS total,
                                  COUNT(*) FILTER (WHERE
                                    CASE WHEN COALESCE(device.value->>'last_seen','') ~ '^\\d{4}-\\d{2}-\\d{2}T'
                                      THEN (device.value->>'last_seen')::timestamptz >= NOW() - INTERVAL '75 seconds'
                                      ELSE FALSE END
                                  ) AS online
                           FROM fbpostpro_customer_data cd
                           CROSS JOIN LATERAL jsonb_each(
                             CASE WHEN jsonb_typeof(cd.data)='object' THEN cd.data ELSE '{}'::jsonb END
                           ) device
                           WHERE cd.data_key='devices'"""
                    )
                    device_counts = cur.fetchone() or {}
            # Legacy rows were merged into lifecycle above, so they must not be counted twice.
            total_campaigns = sum(lifecycle.values())
            total_users, active_users = int(user_counts.get("total", 0)), int(user_counts.get("active", 0))
            device_total = int(device_counts.get("total", 0))
            devices_online = int(device_counts.get("online", 0))
            devices_offline = max(0, device_total - devices_online)
        else:
            users = _users()
            devices = _all_devices(users)
            accounts_rows, group_rows = _all_accounts(users), _all_groups(users)
            campaigns, tasks = _all_campaigns(users), _all_tasks(users)
            lifecycle = Counter(
                LEGACY_STATUS_MAP.get(item.get("lifecycle", item.get("status", "")), item.get("lifecycle", item.get("status", "")))
                for item in campaigns
            )
            task_counts = Counter(item.get("status", "") for item in tasks)
            task_counts["successful"] += sum(int(item.get("success", 0) or 0) for item in campaigns if item.get("kind") == "legacy")
            task_counts["failed"] += sum(int(item.get("errors", 0) or 0) for item in campaigns if item.get("kind") == "legacy")
            total_users = len(users)
            active_users = sum(bool(item.get("is_active", True)) for item in users)
            accounts, groups, total_campaigns = len(accounts_rows), len(group_rows), len(campaigns)
            devices_online = sum(item["online"] for item in devices)
            devices_offline = sum(not item["online"] for item in devices)
        return {
            "users": total_users, "active_users": active_users,
            "accounts": accounts, "groups": groups, "campaigns": total_campaigns,
            "campaign_status": {key: int(lifecycle.get(key, 0)) for key in (
                "scheduled", "queued", "running", "completed", "partial_failed", "failed", "cancelled"
            )},
            "devices_online": devices_online,
            "devices_offline": devices_offline,
            "task_success": int(task_counts.get("successful", 0)),
            "task_failed": int(task_counts.get("failed", 0)),
        }

    def _enrich_campaigns(campaigns, tasks, users, devices):
        user_map = {user["user_id"]: user for user in users}
        device_map = {(item["customer_id"], item["device_id"]): item for item in devices}
        by_campaign = {}
        for task in tasks:
            by_campaign.setdefault(task.get("campaign_id", ""), []).append(task)
        for item in campaigns:
            campaign_tasks = by_campaign.get(item.get("campaign_id", ""), [])
            counts = Counter(task.get("status", "") for task in campaign_tasks)
            item["owner"] = user_map.get(item.get("customer_id", ""), {})
            item["task_counts"] = counts
            item["progress_success"] = int(item.get("successful", item.get("success", counts.get("successful", 0))) or 0)
            item["progress_failed"] = int(item.get("failed", item.get("errors", counts.get("failed", 0))) or 0)
            item["retry_total"] = sum(int(task.get("retry_count", 0) or 0) for task in campaign_tasks)
            item["device_names"] = sorted({
                device_map.get((item.get("customer_id", ""), task.get("device_id", "")), {}).get("name", task.get("device_id", ""))
                for task in campaign_tasks if task.get("device_id")
            })
            start_value = item.get("started_at") or item.get("created_at")
            end_value = item.get("finished_at") or (datetime.now(timezone.utc) if item.get("lifecycle") in {"running", "paused"} else None)
            try:
                start_dt = start_value if isinstance(start_value, datetime) else datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
                end_dt = end_value if isinstance(end_value, datetime) else datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
                seconds = max(0, int((end_dt - start_dt).total_seconds()))
                item["duration"] = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
            except (TypeError, ValueError):
                item["duration"] = "—"
        return campaigns

    @bp.route("/admin")
    @admin_required
    def admin_dashboard():
        return render_template("admin/dashboard.html", metrics=_dashboard_metrics())

    @bp.route("/api/admin/dashboard")
    @admin_required
    def admin_dashboard_api():
        return jsonify({"ok": True, "metrics": _dashboard_metrics()})

    @bp.route("/admin/users")
    @admin_required
    def admin_users():
        query = str(request.args.get("q", "")).strip().casefold()[:200]
        status = str(request.args.get("status", "")).lower()
        page = _int_arg("page")
        if services["postgres_enabled"]():
            clauses, params = ["1=1"], []
            if query:
                clauses.append("(LOWER(u.username) LIKE %s OR LOWER(u.email) LIKE %s OR LOWER(u.display_name) LIKE %s)")
                params.extend([f"%{query}%"] * 3)
            if status in {"active", "locked"}:
                clauses.append("u.is_active=%s")
                params.append(status == "active")
            where = " AND ".join(clauses)
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) AS total FROM fbpostpro_users u WHERE {where}", params)
                    total = int((cur.fetchone() or {}).get("total", 0))
                    pagination = _pagination(total, page, 25)
                    cur.execute(
                        f"""SELECT u.user_id, u.username, u.email, u.display_name, u.is_active,
                                   u.role, u.created_at, u.last_login_at,
                                   u.max_facebook_accounts, u.max_groups, u.max_campaigns,
                                   u.max_devices, u.max_active_campaigns, u.max_tasks_per_campaign,
                                   (SELECT COUNT(*) FROM fbpostpro_accounts a WHERE a.customer_id=u.user_id) AS account_count,
                                   (SELECT COUNT(*) FROM fbpostpro_groups g WHERE g.customer_id=u.user_id) AS group_count,
                                   ((SELECT COUNT(*) FROM fbpostpro_campaign_engine c WHERE c.customer_id=u.user_id)
                                     + (SELECT COUNT(*) FROM fbpostpro_campaigns lc WHERE lc.customer_id=u.user_id)) AS campaign_count,
                                   (SELECT COUNT(*) FROM jsonb_each(
                                      CASE WHEN jsonb_typeof(cd.data)='object' THEN cd.data ELSE '{{}}'::jsonb END
                                    )) AS device_count,
                                   (SELECT MAX(value->>'last_seen') FROM jsonb_each(
                                      CASE WHEN jsonb_typeof(cd.data)='object' THEN cd.data ELSE '{{}}'::jsonb END
                                    )) AS device_last_seen
                            FROM fbpostpro_users u
                            LEFT JOIN fbpostpro_customer_data cd
                              ON cd.customer_id=u.user_id AND cd.data_key='devices'
                            WHERE {where}
                            ORDER BY u.created_at DESC LIMIT %s OFFSET %s""",
                        params + [25, (pagination["page"] - 1) * 25],
                    )
                    users = cur.fetchall()
            for user in users:
                user["counts"] = {
                    "accounts": int(user.pop("account_count", 0) or 0),
                    "groups": int(user.pop("group_count", 0) or 0),
                    "campaigns": int(user.pop("campaign_count", 0) or 0),
                    "devices": int(user.pop("device_count", 0) or 0),
                }
                user["last_activity"] = max(str(user.get("last_login_at", "")), str(user.pop("device_last_seen", "") or ""))
            return render_template("admin/users.html", users=users, pagination=pagination, query=request.args.get("q", ""), status=status)

        users = _users()
        devices = _all_devices(users)
        accounts = _all_accounts(users)
        groups = _all_groups(users)
        campaigns = _all_campaigns(users)
        counts = {
            "accounts": Counter(item.get("customer_id", "") for item in accounts),
            "groups": Counter(item.get("customer_id", "") for item in groups),
            "campaigns": Counter(item.get("customer_id", "") for item in campaigns),
            "devices": Counter(item.get("customer_id", "") for item in devices),
        }
        last_seen = {}
        for device in devices:
            if str(device.get("last_seen", "")) > last_seen.get(device["customer_id"], ""):
                last_seen[device["customer_id"]] = str(device.get("last_seen", ""))
        for user in users:
            user["counts"] = {key: value[user["user_id"]] for key, value in counts.items()}
            user["last_activity"] = max(str(user.get("last_login_at", "")), last_seen.get(user["user_id"], ""))
        users = [user for user in users if (not query or query in f"{user.get('username','')} {user.get('email','')} {user.get('display_name','')}".casefold()) and (status not in {"active", "locked"} or bool(user.get("is_active", True)) == (status == "active"))]
        users.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        page_items, pagination = _paginate(users, page, 25)
        return render_template("admin/users.html", users=page_items, pagination=pagination, query=request.args.get("q", ""), status=status)

    @bp.route("/admin/users/<user_id>")
    @admin_required
    def admin_user_detail(user_id):
        user_id = services["sanitize_customer_id"](user_id)
        user = services["find_user_by_id"](user_id)
        if not user:
            abort(404)
        user = dict(user)
        user.pop("password_hash", None)
        devices = [item for item in _all_devices() if item["customer_id"] == user_id]
        accounts = services["load_facebook_accounts"](user_id)
        groups = services["load_groups"](user_id)
        campaigns = services["load_engine_campaigns"](user_id)
        return render_template("admin/user_detail.html", user=user, devices=devices, accounts=accounts, group_count=len(groups), campaigns=campaigns)

    @bp.route("/api/admin/users/<user_id>/status", methods=["POST"])
    @admin_required
    @services["synchronized_state"]
    def admin_user_status(user_id):
        user_id = services["sanitize_customer_id"](user_id)
        users = services["load_users"]()
        user = users.get(user_id)
        if not isinstance(user, dict):
            return jsonify({"error": "User not found."}), 404
        payload = request.get_json(silent=True) if request.is_json else request.form
        if not hasattr(payload, "get") or "is_active" not in payload:
            return jsonify({"error": "Thiếu is_active."}), 400
        active = payload.get("is_active")
        active = active is True or str(active).lower() in {"1", "true", "yes", "on"}
        if user.get("role") == "admin" and not active and services["get_admin_actor_id"]() == user_id:
            return jsonify({"error": "Admin không thể tự khóa tài khoản đang dùng."}), 409
        previous = bool(user.get("is_active", True))
        user["is_active"] = active
        services["save_users"](users)
        services["record_admin_audit"](services["get_admin_actor_id"](), "unlock_user" if active else "lock_user", "user", user_id, {"previous": previous, "is_active": active})
        services["record_operational_log"](user_id, "user_status_changed", "warning" if not active else "info", "Tài khoản đã được mở khóa." if active else "Tài khoản đã bị khóa.")
        return jsonify({"ok": True, "user_id": user_id, "is_active": active})

    @bp.route("/api/admin/users/<user_id>/quota", methods=["POST"])
    @admin_required
    @services["synchronized_state"]
    def admin_user_quota(user_id):
        user_id = services["sanitize_customer_id"](user_id)
        users = services["load_users"]()
        user = users.get(user_id)
        if not isinstance(user, dict):
            return jsonify({"error": "User not found."}), 404
        payload = request.get_json(silent=True) if request.is_json else request.form
        if not hasattr(payload, "items"):
            return jsonify({"error": "Payload quota không hợp lệ."}), 400
        aliases = {"max_accounts": "max_facebook_accounts"}
        limits = {
            "max_facebook_accounts": (1, 1000), "max_groups": (1, 100000),
            "max_campaigns": (1, 100000), "max_devices": (1, 1000),
            "max_active_campaigns": (1, 100), "max_tasks_per_campaign": (1, 100000),
        }
        changes = {}
        for incoming, raw in payload.items():
            key = aliases.get(incoming, incoming)
            if key not in limits:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return jsonify({"error": f"{incoming} phải là số nguyên."}), 400
            low, high = limits[key]
            if value < low or value > high:
                return jsonify({"error": f"{incoming} phải trong khoảng {low}–{high}."}), 400
            changes[key] = {"from": int(user.get(key, low) or low), "to": value}
            user[key] = value
        if not changes:
            return jsonify({"error": "Không có quota hợp lệ."}), 400
        services["save_users"](users)
        services["record_admin_audit"](services["get_admin_actor_id"](), "change_quota", "user", user_id, changes)
        return jsonify({"ok": True, "quotas": {key: user[key] for key in limits}})

    @bp.route("/admin/accounts")
    @admin_required
    def admin_accounts():
        users, devices = _users(), _all_devices()
        user_map = {item["user_id"]: item for item in users}
        device_map = {(item["customer_id"], item["device_id"]): item for item in devices}
        query = str(request.args.get("q", "")).strip().casefold()[:200]
        owner = services["sanitize_customer_id"](request.args.get("user", ""))
        page = _int_arg("page")
        if services["postgres_enabled"]():
            clauses, params = ["1=1"], []
            if owner:
                clauses.append("customer_id=%s")
                params.append(owner)
            if query:
                clauses.append("(LOWER(display_name) LIKE %s OR LOWER(facebook_user_id) LIKE %s)")
                params.extend([f"%{query}%", f"%{query}%"])
            where = " AND ".join(clauses)
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) AS total FROM fbpostpro_accounts WHERE {where}", params)
                    total = int((cur.fetchone() or {}).get("total", 0))
                    pagination = _pagination(total, page, 50)
                    cur.execute(
                        f"SELECT * FROM fbpostpro_accounts WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                        params + [50, (pagination["page"] - 1) * 50],
                    )
                    accounts = cur.fetchall()
                    account_ids = [item.get("account_id", "") for item in accounts]
                    if account_ids:
                        cur.execute(
                            """SELECT * FROM fbpostpro_campaign_tasks
                               WHERE account_id=ANY(%s) AND status IN ('claimed','running','paused')""",
                            (account_ids,),
                        )
                        tasks = cur.fetchall()
                    else:
                        tasks = []
        else:
            accounts = _all_accounts(users)
            tasks = _all_tasks(users)
            pagination = None
        active = {}
        for task in tasks:
            if task.get("status") in {"claimed", "running", "paused"}:
                active[(task.get("customer_id", ""), task.get("account_id", ""))] = task
        rows = []
        for account in accounts:
            if owner and account.get("customer_id") != owner:
                continue
            if query and query not in f"{account.get('display_name','')} {account.get('facebook_user_id','')}".casefold():
                continue
            row = dict(account)
            row["owner"] = user_map.get(row.get("customer_id", ""), {})
            row["device"] = device_map.get((row.get("customer_id", ""), row.get("device_id", "")), {})
            row["active_task"] = active.get((row.get("customer_id", ""), row.get("account_id", "")))
            rows.append(row)
        if services["postgres_enabled"]():
            page_items = rows
        else:
            page_items, pagination = _paginate(rows, page, 50)
        return render_template("admin/accounts.html", accounts=page_items, users=users, pagination=pagination, query=request.args.get("q", ""), owner=owner)

    @bp.route("/admin/groups")
    @admin_required
    def admin_groups():
        users = _users()
        user_map = {item["user_id"]: item for item in users}
        query = str(request.args.get("q", "")).strip().casefold()[:200]
        owner = services["sanitize_customer_id"](request.args.get("user", ""))
        account_filter = str(request.args.get("account", ""))[:64]
        page = _int_arg("page")
        if services["postgres_enabled"]():
            clauses, params = ["1=1"], []
            if owner:
                clauses.append("g.customer_id=%s")
                params.append(owner)
            if account_filter:
                clauses.append("a.account_id=%s")
                params.append(account_filter)
            if query:
                clauses.append("LOWER(g.group_url) LIKE %s")
                params.append(f"%{query}%")
            where = " AND ".join(clauses)
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT COUNT(*) AS total FROM fbpostpro_groups g
                            LEFT JOIN fbpostpro_group_assignments a
                              ON a.customer_id=g.customer_id AND a.group_url=g.group_url
                            WHERE {where}""",
                        params,
                    )
                    total = int((cur.fetchone() or {}).get("total", 0))
                    pagination = _pagination(total, page, 50)
                    cur.execute(
                        f"""SELECT g.customer_id, g.group_url, a.account_id,
                                   ac.display_name AS account_name, ac.facebook_user_id
                            FROM fbpostpro_groups g
                            LEFT JOIN fbpostpro_group_assignments a
                              ON a.customer_id=g.customer_id AND a.group_url=g.group_url
                            LEFT JOIN fbpostpro_accounts ac
                              ON ac.customer_id=a.customer_id AND ac.account_id=a.account_id
                            WHERE {where}
                            ORDER BY g.customer_id, g.group_url LIMIT %s OFFSET %s""",
                        params + [50, (pagination["page"] - 1) * 50],
                    )
                    groups = cur.fetchall()
                    customer_ids = sorted({item.get("customer_id", "") for item in groups})
                    group_urls = [item.get("group_url", "") for item in groups]
                    if customer_ids and group_urls:
                        cur.execute(
                            """SELECT customer_id, group_url, ARRAY_AGG(DISTINCT campaign_id) AS campaign_ids
                               FROM fbpostpro_campaign_tasks
                               WHERE customer_id=ANY(%s) AND group_url=ANY(%s)
                               GROUP BY customer_id, group_url""",
                            (customer_ids, group_urls),
                        )
                        campaign_groups = {
                            (item.get("customer_id", ""), item.get("group_url", "")): item.get("campaign_ids", [])
                            for item in cur.fetchall()
                        }
                    else:
                        campaign_groups = {}
            rows = []
            for group in groups:
                row = dict(group)
                row["owner"] = user_map.get(row.get("customer_id", ""), {})
                row["account"] = {
                    "account_id": row.get("account_id", ""),
                    "display_name": row.get("account_name", ""),
                    "facebook_user_id": row.get("facebook_user_id", ""),
                }
                row["campaign_ids"] = campaign_groups.get((row.get("customer_id", ""), row.get("group_url", "")), [])
                rows.append(row)
            page_items = rows
            return render_template("admin/groups.html", groups=page_items, users=users, pagination=pagination, query=request.args.get("q", ""), owner=owner, account_filter=account_filter)

        account_map = {(item.get("customer_id", ""), item.get("account_id", "")): item for item in _all_accounts(users)}
        campaigns = _all_campaigns(users)
        campaign_groups = {}
        for campaign in campaigns:
            for bucket in campaign.get("account_group_snapshot", []) or []:
                for group_url in bucket.get("groups", []) or []:
                    campaign_groups.setdefault((campaign.get("customer_id", ""), group_url), []).append(campaign.get("campaign_id", ""))
        rows = []
        for group in _all_groups(users):
            if owner and group.get("customer_id") != owner:
                continue
            if account_filter and group.get("account_id", "") != account_filter:
                continue
            if query and query not in str(group.get("group_url", "")).casefold():
                continue
            row = dict(group)
            row["owner"] = user_map.get(row.get("customer_id", ""), {})
            row["account"] = account_map.get((row.get("customer_id", ""), row.get("account_id", "")), {})
            row["campaign_ids"] = campaign_groups.get((row.get("customer_id", ""), row.get("group_url", "")), [])
            rows.append(row)
        page_items, pagination = _paginate(rows, page, 50)
        return render_template("admin/groups.html", groups=page_items, users=users, pagination=pagination, query=request.args.get("q", ""), owner=owner, account_filter=account_filter)

    @bp.route("/admin/campaigns")
    @admin_required
    def admin_campaigns():
        users, devices = _users(), _all_devices()
        query = str(request.args.get("q", "")).strip().casefold()[:200]
        owner = services["sanitize_customer_id"](request.args.get("user", ""))
        status = str(request.args.get("status", ""))[:32]
        page = _int_arg("page")
        if services["postgres_enabled"]():
            page_items, summaries, pagination = _postgres_campaign_page(query, owner, status, page, 40)
            page_items = _enrich_campaigns(page_items, [], users, devices)
            device_map = {(item.get("customer_id", ""), item.get("device_id", "")): item for item in devices}
            for item in page_items:
                summary = summaries.get(item.get("campaign_id", ""), {})
                item["task_counts"] = summary.get("counts", Counter())
                item["retry_total"] = summary.get("retries", 0)
                item["device_names"] = sorted({
                    device_map.get((item.get("customer_id", ""), device_id), {}).get("name", device_id)
                    for device_id in summary.get("device_ids", set())
                })
        else:
            tasks = _all_tasks(users)
            rows = _enrich_campaigns(_all_campaigns(users), tasks, users, devices)
            rows = [item for item in rows if (not owner or item.get("customer_id") == owner) and (not status or item.get("lifecycle") == status) and (not query or query in f"{item.get('campaign_name','')} {item.get('campaign_id','')}".casefold())]
            page_items, pagination = _paginate(rows, page, 40)
        return render_template("admin/campaigns.html", campaigns=page_items, users=users, pagination=pagination, query=request.args.get("q", ""), owner=owner, status=status)

    @bp.route("/admin/campaigns/<campaign_id>")
    @admin_required
    def admin_campaign_detail(campaign_id):
        users = _users()
        campaign_id = str(campaign_id or "")[:64]
        task_summary_rows, retry_total, summary_device_ids = [], 0, []
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fbpostpro_campaign_engine WHERE campaign_id=%s", (campaign_id,))
                    campaign = cur.fetchone()
                    if campaign:
                        campaign = {**campaign, "kind": "engine", "status": campaign.get("lifecycle", "draft")}
                    else:
                        cur.execute("SELECT * FROM fbpostpro_campaigns WHERE job_id=%s", (campaign_id,))
                        legacy = cur.fetchone()
                        campaign = ({**legacy, "kind": "legacy", "campaign_id": legacy.get("job_id", ""), "lifecycle": LEGACY_STATUS_MAP.get(legacy.get("status", ""), legacy.get("status", ""))} if legacy else None)
                    if campaign and campaign.get("kind") == "engine":
                        cur.execute("SELECT COUNT(*) AS total FROM fbpostpro_campaign_tasks WHERE campaign_id=%s AND customer_id=%s", (campaign_id, campaign.get("customer_id", "")))
                        task_total = int((cur.fetchone() or {}).get("total", 0))
                        pagination = _pagination(task_total, _int_arg("page"), 50)
                        cur.execute(
                            "SELECT * FROM fbpostpro_campaign_tasks WHERE campaign_id=%s AND customer_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                            (campaign_id, campaign.get("customer_id", ""), 50, (pagination["page"] - 1) * 50),
                        )
                        tasks = cur.fetchall()
                        cur.execute(
                            """SELECT status, COUNT(*) AS total, COALESCE(SUM(retry_count),0) AS retries
                               FROM fbpostpro_campaign_tasks WHERE campaign_id=%s AND customer_id=%s
                               GROUP BY status""",
                            (campaign_id, campaign.get("customer_id", "")),
                        )
                        task_summary_rows = cur.fetchall()
                        retry_total = sum(int(item.get("retries", 0) or 0) for item in task_summary_rows)
                        cur.execute(
                            """SELECT DISTINCT device_id FROM fbpostpro_campaign_tasks
                               WHERE campaign_id=%s AND customer_id=%s AND device_id<>''""",
                            (campaign_id, campaign.get("customer_id", "")),
                        )
                        summary_device_ids = [item.get("device_id", "") for item in cur.fetchall()]
                    else:
                        tasks, pagination = [], _pagination(0, 1, 50)
        else:
            campaign = next((item for item in _all_campaigns(users) if item.get("campaign_id") == campaign_id), None)
            tasks = [item for item in _all_tasks(users) if item.get("campaign_id") == campaign_id and item.get("customer_id") == (campaign or {}).get("customer_id")]
            tasks, pagination = _paginate(tasks, _int_arg("page"), 50)
        if not campaign:
            abort(404)
        safe_tasks = []
        for raw in tasks:
            task = dict(raw)
            task.pop("lease_token", None)
            task["last_error"] = str(services["safe_log_value"](task.get("last_error", "")))
            safe_tasks.append(task)
        all_devices = _all_devices(users)
        campaign = _enrich_campaigns([campaign], safe_tasks, users, all_devices)[0]
        if services["postgres_enabled"]() and campaign.get("kind") == "engine":
            campaign["task_counts"] = Counter({item.get("status", ""): int(item.get("total", 0)) for item in task_summary_rows})
            campaign["retry_total"] = retry_total
            device_map = {(item.get("customer_id", ""), item.get("device_id", "")): item for item in all_devices}
            campaign["device_names"] = sorted({
                device_map.get((campaign.get("customer_id", ""), device_id), {}).get("name", device_id)
                for device_id in summary_device_ids
            })
        return render_template("admin/campaign_detail.html", campaign=campaign, tasks=safe_tasks, pagination=pagination)

    @bp.route("/api/admin/campaigns/<campaign_id>/cancel", methods=["POST"])
    @admin_required
    @services["synchronized_state"]
    def admin_campaign_cancel(campaign_id):
        campaign_id = str(campaign_id or "")[:64]
        if services["postgres_enabled"]():
            services["init_persistence_tables"]()
            with services["postgres_connect"]() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM fbpostpro_campaign_engine WHERE campaign_id=%s", (campaign_id,))
                    campaign = cur.fetchone()
        else:
            users = _users()
            campaign = next((item for item in _all_campaigns(users) if item.get("campaign_id") == campaign_id and item.get("kind") == "engine"), None)
        if not campaign:
            return jsonify({"error": "Campaign not found."}), 404
        if campaign.get("lifecycle") in TERMINAL_CAMPAIGNS:
            return jsonify({"error": "Campaign đã kết thúc."}), 409
        customer_id = campaign.get("customer_id", "")
        jobs = services["load_jobs"](customer_id)
        for device_id in services["engine_campaign_active_devices"](customer_id, campaign_id):
            services["queue_device_command"](customer_id, device_id, "stop", (jobs.get(device_id) or {}).get("job_id", ""))
        services["cancel_engine_campaign"](customer_id, campaign_id)
        actor = services["get_admin_actor_id"]()
        services["record_admin_audit"](actor, "cancel_campaign", "campaign", campaign_id, {"customer_id": customer_id})
        services["record_operational_log"](customer_id, "campaign_cancelled_by_admin", "warning", "Admin đã hủy campaign qua command flow.", campaign_id=campaign_id)
        return jsonify({"ok": True, "campaign_id": campaign_id, "status": "cancelled"})

    @bp.route("/admin/workers")
    @admin_required
    def admin_workers():
        users = _users()
        user_map = {item["user_id"]: item for item in users}
        accounts = _all_accounts(users)
        account_by_device = {(item.get("customer_id", ""), item.get("device_id", "")): item for item in accounts if item.get("device_id")}
        query = str(request.args.get("q", "")).strip().casefold()[:200]
        state = str(request.args.get("state", ""))
        rows = []
        for device in _all_devices(users):
            if state == "online" and not device["online"] or state == "offline" and device["online"]:
                continue
            if query and query not in f"{device.get('name','')} {device.get('device_id','')}".casefold():
                continue
            device["owner"] = user_map.get(device.get("customer_id", ""), {})
            device["account"] = account_by_device.get((device.get("customer_id", ""), device.get("device_id", "")), {})
            rows.append(device)
        rows.sort(key=lambda item: str(item.get("last_seen", "")), reverse=True)
        page_items, pagination = _paginate(rows, _int_arg("page"), 50)
        return render_template("admin/workers.html", devices=page_items, pagination=pagination, query=request.args.get("q", ""), state=state)

    @bp.route("/admin/logs")
    @admin_required
    def admin_logs():
        page, per_page = _int_arg("page"), 50
        filters = {key: request.args.get(key, "") for key in ("customer_id", "campaign_id", "device_id", "severity", "date_from", "date_to")}
        filters["query"] = request.args.get("q", "")
        logs, total = services["load_operational_logs"](filters, page, per_page)
        pagination = {"page": page, "pages": max(1, math.ceil(total / per_page)), "total": total, "per_page": per_page}
        return render_template("admin/logs.html", logs=logs, users=_users(), filters=filters, query=request.args.get("q", ""), pagination=pagination)

    @bp.route("/admin/audit")
    @admin_required
    def admin_audit():
        page, per_page = _int_arg("page"), 50
        logs, total = services["load_admin_audit_logs"](page, per_page)
        pagination = {"page": page, "pages": max(1, math.ceil(total / per_page)), "total": total, "per_page": per_page}
        return render_template("admin/audit.html", logs=logs, pagination=pagination)

    app.register_blueprint(bp)
    return bp
