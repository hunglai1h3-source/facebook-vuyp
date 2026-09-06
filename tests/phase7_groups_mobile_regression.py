"""Real-browser checks for the 100-Group management workspace."""
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from phase7_bulk_groups_regression import load_app, register, allow_two_accounts


def run():
    with tempfile.TemporaryDirectory(prefix="fbpp-phase7-ui-") as temp:
        module = load_app(temp)
        setup = module.app.test_client()
        user_id = register(setup, "mobile")
        allow_two_accounts(module, user_id)
        urls = [f"https://www.facebook.com/groups/mobile-{index:03d}" for index in range(100)]
        assert len(module.import_groups(user_id, urls)["added"]) == 100
        account_a = module.create_facebook_account(user_id, "Account A")
        account_b = module.create_facebook_account(user_id, "Account B")

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
                page.on(
                    "console",
                    lambda message: errors.append(message.text)
                    if message.type == "error" and "ERR_NETWORK_ACCESS_DENIED" not in message.text
                    else None,
                )
                page.goto(origin + "/login")
                page.locator("#login").fill("phase7_mobile")
                page.locator("#password").fill("Phase7Test123!")
                page.locator("button[type=submit]").click()
                page.goto(origin + "/groups")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("[data-group-row]").count() == 100
                assert page.locator("#groupUrls").is_visible()

                page.locator("[data-select-all]").check()
                assert page.locator(".group-select:checked").count() == 100
                page.locator("[data-even-distribute]").click()
                selects = page.locator("[data-account-assignment]")
                assert selects.nth(0).input_value() == account_a["account_id"]
                assert selects.nth(1).input_value() == account_b["account_id"]
                # User can edit the generated preview before saving.
                selects.nth(0).select_option(account_b["account_id"])
                with page.expect_navigation():
                    page.locator("[data-save-assignments]").click()
                mapping = module.load_group_assignments(user_id)
                assert len(mapping) == 100 and mapping[urls[0]] == account_b["account_id"]

                page.locator('[name="q"]').fill("mobile-099")
                page.locator(".group-filter-bar button").click()
                assert page.locator("[data-group-row]").count() == 1
                assert "mobile-099" in page.locator("[data-group-row]").inner_text()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

                page.set_viewport_size({"width": 2560, "height": 1440})
                page.goto(origin + "/groups")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("[data-group-row]").count() == 100
                assert not errors, errors
                browser.close()
                print("PASS 8: 390px/2K UI, 100 rows, select-all, editable even split, save and search; no overflow/errors")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    run()
