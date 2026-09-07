"""Admin regression for malformed mutations, whole-campaign counts and pagination.

Uses only isolated test data; never launches an automation worker.
"""
from contextlib import contextmanager
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

from flask import template_rendered

from phase6_worker_command_regression import load_app
from phase7_bulk_groups_regression import register


@contextmanager
def captured_templates(app):
    contexts = []
    def capture(sender, template, context, **extra):
        contexts.append(context)
    template_rendered.connect(capture, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(capture, app)


def run_checks():
    with tempfile.TemporaryDirectory(prefix="fbpp-admin-stability-") as temp:
        module = load_app(temp)
        admin, customer = module.app.test_client(), module.app.test_client()
        admin_id = register(admin, "stability_admin")
        customer_id = register(customer, "stability_customer")
        users = module.load_users()
        users[admin_id]["role"] = "admin"
        users[customer_id]["max_facebook_accounts"] = 100
        module.save_users(users)

        for value in ({}, [], "unknown", 2, 1.5, None):
            assert admin.post(f"/api/admin/users/{customer_id}/status", json={"is_active": value}).status_code == 400
            assert module.find_user_by_id(customer_id)["is_active"] is True
        for value in (True, False, 2.7, {}, [], "1.5"):
            assert admin.post(f"/api/admin/users/{customer_id}/quota", json={"max_groups": value}).status_code == 400
        assert admin.post(f"/api/admin/users/{admin_id}/status", json={"is_active": False}).status_code == 409
        print("PASS admin mutation: malformed boolean/quotas cannot silently change user state")

        module.save_devices(customer_id, {
            f"ext_paged_{index:03d}": {"name": f"Paged Worker {index:03d}", "last_seen": module.now_iso(), "token_hash": "never-display-hash"}
            for index in range(61)
        })
        for index in range(61):
            module.create_facebook_account(customer_id, f"Paged Account {index:03d}")
            module.record_operational_log(customer_id, "paging_check", "info", f"paging message {index}")
            module.record_admin_audit(admin_id, "paging_check", "user", customer_id, {"ordinal": index})

        for path in ("/admin/accounts?q=Paged", "/admin/workers?q=Paged&state=online", "/admin/logs?q=paging_check&severity=info", "/admin/audit"):
            page = admin.get(path)
            assert page.status_code == 200
            body = page.get_data(as_text=True)
            link = re.search(r'rel="next" href="([^"]+)"', body)
            assert link, path
            target = link.group(1).replace("&amp;", "&")
            original_filters = parse_qs(urlsplit(path).query)
            next_filters = parse_qs(urlsplit(target).query)
            assert all(next_filters.get(key) == value for key, value in original_filters.items())
            with captured_templates(module.app) as contexts:
                assert admin.get(target).status_code == 200
            assert contexts[-1]["pagination"]["page"] == 2
            assert "never-display-hash" not in body
        print("PASS admin pagination: accounts/workers/logs/audit reach page 2 and preserve filters")

        campaigns = [{
            "campaign_id": "cmp_admin_counts", "customer_id": customer_id, "campaign_name": "Summary beyond first page",
            "lifecycle": "completed", "total": 61, "successful": 61, "failed": 0,
            "created_at": module.now_iso(), "started_at": module.now_iso(), "finished_at": module.now_iso(),
        }]
        tasks = [{
            "task_id": f"task_admin_counts_{index}", "campaign_id": "cmp_admin_counts", "customer_id": customer_id,
            "status": "successful", "retry_count": 1, "device_id": "ext_paged_000", "last_error": "", "max_retries": 2,
        } for index in range(61)]
        module._save_local_engine(customer_id, campaigns=campaigns, tasks=tasks)
        for page_num in (1, 2):
            with captured_templates(module.app) as contexts:
                response = admin.get(f"/admin/campaigns/cmp_admin_counts?page={page_num}")
            assert response.status_code == 200
            ctx = contexts[-1]
            assert ctx["campaign"]["retry_total"] == 61
            assert ctx["campaign"]["task_counts"]["successful"] == 61
            assert len(ctx["tasks"]) == (50 if page_num == 1 else 11)
        with captured_templates(module.app) as contexts:
            assert admin.get(f"/admin/users/{customer_id}?page=2").status_code == 200
        ctx = contexts[-1]
        assert ctx["account_count"] == 61 and len(ctx["accounts"]) == 11
        assert ctx["campaign_count"] == 1 and ctx["device_count"] == 61
        print("PASS admin totals: detail metrics cover full campaign/user, not only visible page")


if __name__ == "__main__":
    run_checks()
