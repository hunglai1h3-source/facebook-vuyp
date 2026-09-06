"""Browser regression for responsive campaign control and live status polling."""
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from phase6_worker_command_regression import load_app, register


def run():
    with tempfile.TemporaryDirectory(prefix="fbpp-phase6-ui-") as temp:
        module = load_app(temp)
        setup = module.app.test_client()
        user_id = register(setup, "mobile")
        device_id = "ext_mobile_ui"
        module.save_devices(user_id, {device_id: {
            "name": "Mobile test worker", "token": "mobile-token",
            "mode": "chrome_extension", "last_seen": module.now_iso(),
            "status": "online", "worker_state": "busy", "current_job_id": "job_mobile",
            "facebook_logged_in": True,
        }})
        settings = module.load_settings(user_id)
        settings["active_device_id"] = device_id
        module.save_settings(user_id, settings)
        module.update_campaign_state(
            user_id, job_id="job_mobile", device_id=device_id, running=True,
            status="running", message="Đang đăng Group 1/2",
            processed=1, total=2, success=1, errors=0,
        )

        server = make_server("127.0.0.1", 0, module.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 390, "height": 844})
                page = context.new_page()
                errors = []
                page.on(
                    "console",
                    lambda message: errors.append(message.text)
                    if message.type == "error" and "ERR_NETWORK_ACCESS_DENIED" not in message.text
                    else None,
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/login")
                page.locator("#login").fill("phase6_mobile")
                page.locator("#password").fill("Phase6Test123!")
                page.locator("button[type=submit]").click()
                page.goto(origin + "/compose")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("[data-campaign-monitor]").is_visible()
                assert page.locator("[data-pause-campaign]").is_visible()
                assert page.locator('form[action="/stop-campaign"] button').is_visible()
                assert page.locator("[data-progress-count]").text_content() == "1/2"

                module.update_campaign_state(
                    user_id, running=True, status="paused", message="Đã tạm dừng",
                    processed=1, total=2, success=1, errors=0,
                )
                page.wait_for_function(
                    "document.querySelector('[data-campaign-message]').textContent.includes('Đã tạm dừng')",
                    timeout=6000,
                )
                page.reload()
                assert page.locator("[data-resume-campaign]").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

                page.set_viewport_size({"width": 2560, "height": 1440})
                page.reload()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("[data-resume-campaign]").is_visible()
                assert not errors, errors
                browser.close()
                print("PASS: mobile/2K controls, realtime polling, Pause/Resume/Stop visibility, no overflow or console errors")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    run()
