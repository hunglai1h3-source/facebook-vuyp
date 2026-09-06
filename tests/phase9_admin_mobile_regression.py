"""Real-browser responsive and console regression for Phase 9 admin pages."""
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from phase6_worker_command_regression import load_app
from phase7_bulk_groups_regression import register


def run():
    with tempfile.TemporaryDirectory(prefix="fbpp-phase9-ui-") as temp:
        module = load_app(temp)
        setup = module.app.test_client()
        user_id = register(setup, "phase9_ui")
        users = module.load_users()
        users[user_id]["role"] = "admin"
        users[user_id]["max_facebook_accounts"] = 2
        module.save_users(users)
        module.save_devices(user_id, {"ext_phase9_ui": {
            "name": "Admin UI Worker", "token": "never-render-this-token",
            "mode": "chrome_extension", "last_seen": module.now_iso(),
            "status": "online", "worker_state": "idle", "current_job_id": "",
            "facebook_logged_in": True, "extension_version": "9.0-test",
        }})
        module.record_operational_log(user_id, "ui_test", "info", "Responsive log")

        server = make_server("127.0.0.1", 0, module.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 390, "height": 844})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "ERR_NETWORK_ACCESS_DENIED" not in message.text else None)
                page.goto(origin + "/login")
                page.locator("#login").fill("phase7_phase9_ui")
                page.locator("#password").fill("Phase7Test123!")
                page.locator("button[type=submit]").click()

                paths = ["/admin", "/admin/users", f"/admin/users/{user_id}", "/admin/accounts", "/admin/groups", "/admin/campaigns", "/admin/workers", "/admin/logs", "/admin/audit"]
                for width, height in ((390, 844), (2560, 1440)):
                    page.set_viewport_size({"width": width, "height": height})
                    for path in paths:
                        response = page.goto(origin + path)
                        assert response.status == 200, (path, response.status)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, path)
                        assert page.locator(".admin-shell").is_visible()
                        assert "never-render-this-token" not in page.content()
                assert not errors, errors
                browser.close()
                print("PASS 16-17: all Phase 9 pages render at 390px and 2K without document overflow or console/page errors")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    run()
