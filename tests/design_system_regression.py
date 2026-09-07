"""Design-system checks against the pre-Phase-3 contract, using isolated data.

Run with: python -B tests/design_system_regression.py
Use --contracts-only for the source contract checks without a browser.
No Facebook tab or extension is opened; agent responses are exercised via HTTP.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def contract(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        # CSS can change; every form, script, Jinja expression and handler stays.
        text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r'<link\b[^>]*rel=[\"\']stylesheet[\"\'][^>]*>', "", text, flags=re.I)
        # The only new script allowed by this visual-only phase. Original inline
        # scripts and every other script tag remain covered by the baseline hash.
        text = text.replace('<script defer src="{{ url_for(\'static\', filename=\'js/ui-motion.js\') }}"></script>', "")
        # A head block containing only presentation stylesheet links is allowed.
        if path.name != "base.html":
            text = re.sub(r"{%\s*block head\s*%}\s*{%\s*endblock\s*%}", "", text)
        text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_contracts():
    expected = json.loads((ROOT / "tests/design_system_contracts.json").read_text())
    for relative, digest in expected.items():
        # Phase 4 explicitly redesigns the popup stylesheet. Its HTML, JS,
        # manifest and service worker remain protected by the ORIGINAL hashes.
        # Phase 5 fixes the invalid nested forms in Compose and verifies its
        # resulting form ownership in the real browser below.
        if relative in {
            "extension/popup.css", "templates/compose.html",
            "extension/service_worker.js", "extension/web_bridge.js", "app.py",
            "templates/groups.html",
            # Phase 10 intentionally hardens environment/deploy configuration.
            "render.yaml", ".env.example",
            # Host validation requires Flask 3.1; pin contract checked below.
            "requirements.txt",
            # System hardening replaces the old runner success heuristic; its
            # delivery/identity contracts are exercised by worker_safety_regression.js.
            "extension/facebook_runner.js",
        }:
            continue
        assert contract(ROOT / relative) == digest, f"Business contract changed: {relative}"
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'Flask>=3.1,<4' in requirements, 'Production TRUSTED_HOSTS requires Flask 3.1+'
    compose_source = (ROOT / "templates/compose.html").read_text(encoding="utf-8")
    for required in [
        "name=\"campaign_name\"", "name=\"content\"", "name=\"images\"",
        "name=\"min_delay\"", "name=\"max_delay\"", "id=\"images\"",
        "id=\"newImagePreview\"", "id=\"selectedImageCount\"",
        "url_for('save_post')", "url_for('delete_post_image', filename=image)",
        "url_for('delete_all_post_images')", "url_for('run_campaign')",
        "url_for('stop_campaign')", "url_for('pause_campaign')",
        "url_for('resume_campaign')", "js/campaign-control.js",
        'imageInput?.addEventListener("change"',
    ]:
        assert required in compose_source, f"Compose contract missing: {required}"
    assert compose_source.count("<form") == compose_source.count("</form>"), "Compose form tags unbalanced"
    worker_source = (ROOT / "extension/service_worker.js").read_text(encoding="utf-8")
    runner_source = (ROOT / 'extension/facebook_runner.js').read_text(encoding='utf-8')
    for required in ('VERIFY_EXECUTION', 'requires review', 'sessionStorage'):
        assert required in runner_source, f'Worker safety contract missing: {required}'
    bridge_source = (ROOT / "extension/web_bridge.js").read_text(encoding="utf-8")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    groups_source = (ROOT / "templates/groups.html").read_text(encoding="utf-8")
    for required in [
        "current_job_id", "worker_state", "command_id", "pause_requested",
        "waitForSafeControl", "job_id:",
    ]:
        assert required in worker_source, f"Phase 6 worker contract missing: {required}"
    assert "if(!c.deviceId||!c.token)" in bridge_source, "Paired server origin may be overwritten"
    for required in [
        'def queue_device_command(', 'def public_campaign_status(',
        '@app.route("/pause-campaign"', '@app.route("/resume-campaign"',
        'reported_job_id', '"waiting_worker"',
    ]:
        assert required in app_source, f"Phase 6 server contract missing: {required}"
    for required in [
        "url_for('import_group_list')", "url_for('add_group')",
        "url_for('add_facebook_account')", "url_for('assign_groups_to_accounts')",
        "url_for('bulk_delete_groups')", "js/group-management.js",
        "data-even-distribute", "data-account-assignment",
    ]:
        assert required in groups_source, f"Phase 7 Groups contract missing: {required}"
    for required in [
        "fbpostpro_group_assignments", "def import_groups(",
        "def save_group_assignments(", "account_group_snapshot",
    ]:
        assert required in app_source, f"Phase 7 server contract missing: {required}"
    print(f"PASS: {len(expected)-6} protected source contracts plus explicit Phase 6/7 contracts")


def screen_checks(module, context, page, origin, screen, output):
    route = {"dashboard":"/"}.get(screen, "/" + screen)
    if screen in {"login","register"}:
        page.goto(origin + "/logout")
    themes = ["dark"] if screen in {"login", "register"} else ["dark", "light"]
    for theme in themes:
        for width,height in [(390,844),(768,1024),(1920,1080),(3840,2160)]:
            page.set_viewport_size({"width":width,"height":height})
            page.goto(origin + route)
            if screen not in {"login","register"}:
                page.evaluate("t => applyTheme(t)", theme)
            page.wait_for_timeout(350)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{screen}/{theme}/{width}: overflow"
            if screen == "compose":
                assert page.locator('.gallery-card .del-single-btn').evaluate_all("es => es.every(e => { const a=e.getBoundingClientRect(), b=e.closest('.gallery-card').getBoundingClientRect(); return a.top>=b.top && a.bottom<=b.bottom && a.left>=b.left && a.right<=b.right; })"), 'Image action clipped'
                if width > 1200:
                    assert abs(page.locator('.composer-form').bounding_box()['y'] - page.locator('.campaign-sidebar').bounding_box()['y']) < 1, 'Compose summary displaced by legacy forms'
            if output and width in {390,1920}:
                page.screenshot(path=str(output / f"{screen}-{theme}-{width}.png"), full_page=True, animations="disabled")
    if screen == "dashboard":
        # Stress the actual template with long backend text and error state.
        user_id = context.request.get(origin + "/customer-info").json()["customer_id"]
        module.add_history(user_id,"error","Long content " * 15,"https://example.test/" + "a"*240)
        module.update_campaign_state(user_id, running=False, status="finished_with_errors", message="Long status "*30)
        page.set_viewport_size({"width":390,"height":844})
        page.goto(origin + "/")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Dashboard stress overflow"
        for path in ["/compose","/settings","/history"]:
            assert page.locator(f'.page a[href="{path}"]').first.is_visible()
    elif screen == "groups":
        page.set_viewport_size({"width":390,"height":844})
        page.goto(origin + route)
        before = page.locator('form[action^="/delete-group/"]').count()
        page.once("dialog", lambda d: d.dismiss())
        page.locator('form[action="/delete-group/0"] button').click()
        assert page.locator('form[action^="/delete-group/"]').count() == before
        page.once("dialog", lambda d: d.accept())
        page.locator('form[action="/delete-group/0"] button').click()
        page.wait_for_load_state()
        assert page.locator('form[action^="/delete-group/"]').count() == before-1
    elif screen == "history":
        page.goto(origin + route)
        page.once("dialog", lambda d: d.dismiss())
        page.locator('form[action="/clear-history"] button').click()
        page.once("dialog", lambda d: d.accept())
        page.locator('form[action="/clear-history"] button').click()
        page.wait_for_load_state()
        user_id = context.request.get(origin + "/customer-info").json()["customer_id"]
        assert module.load_history(user_id) == []
    elif screen == "login":
        page.goto(origin + "/login?next=/groups")
        page.locator('#login').fill('design_regression')
        page.locator('#password').fill('incorrect')
        page.locator('button[type=submit]').click()
        page.locator('.error-box').wait_for()
        page.locator('#login').fill('design@example.test')
        page.locator('#password').fill('DesignTest123!')
        page.locator('[name=remember]').uncheck()
        page.locator('button[type=submit]').click()
        page.wait_for_url(origin + "/groups")
    elif screen == "register":
        page.goto(origin + "/register")
        for name,value in {"display_name":"Regression", "username":"new_user", "email":"new@example.test", "password":"Password123!", "confirm_password":"Mismatch123!"}.items():
            page.locator(f'[name="{name}"]').fill(value)
        page.locator('button[type=submit]').click()
        page.locator('.error-box').wait_for()
        assert page.url.endswith('/register')
    elif screen == "compose":
        # Phase 5 fixes the pre-existing nested-form browser defect.
        page.goto(origin + route)
        page.locator('button[form^="delete-image-form-"]').first.click()
        page.wait_for_load_state()
        assert page.locator('.saved-gallery-wrap .gallery-card').count() == 1
        page.once("dialog", lambda d: d.dismiss())
        page.locator('[form="delete-all-images-form"]').click()
        assert page.locator('.saved-gallery-wrap .gallery-card').count() == 1
        page.once("dialog", lambda d: d.accept())
        page.locator('[form="delete-all-images-form"]').click()
        page.wait_for_load_state()
        assert page.locator('img[src^="/customer-image/"]').count() == 0
        # No-image markup has the delay and save control inside the native form.
        # Exercise this branch too; do not repair the separate saved-image branch.
        for width in [390, 768, 1920]:
            page.set_viewport_size({"width":width,"height":1080})
            page.goto(origin + route)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator('[name=min_delay]').evaluate("e => e.form === document.querySelector('.composer-form')")
            if output and width in {390,1920}:
                page.screenshot(path=str(output / f'compose-empty-{width}.png'), full_page=True, animations='disabled')
        page.locator('[name=min_delay]').fill('2')
        page.locator('[name=max_delay]').fill('4')
        page.locator('.composer-form button[type=submit]').last.click()
        page.wait_for_load_state()
        assert page.locator('[name=min_delay]').input_value() == '2'
        assert page.locator('[name=max_delay]').input_value() == '4'
    elif screen == "settings":
        page.goto(origin + route)
        page.locator('[name="min_delay"]').fill('8')
        page.locator('[name="max_delay"]').fill('3')
        page.locator('#themeSelect').select_option('light')
        page.locator('form[action="/settings"] button').click()
        page.wait_for_load_state()
        assert page.locator('[name="min_delay"]').input_value() == '3'
        assert page.locator('[name="max_delay"]').input_value() == '8'
        assert page.locator('#themeSelect').input_value() == 'light'
        page.set_viewport_size({"width":390,"height":844})
        assert not page.locator('#pairBox').is_visible()
        with page.expect_response(lambda r: r.url.endswith('/api/extension/pair-code') and r.request.method == 'POST') as response:
            page.locator('#pairBtn').click()
        code = response.value.json()['code']
        page.wait_for_function("code => document.getElementById('pairCode').textContent === code", arg=code)
        assert page.locator('#pairBox').is_visible()
        context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
        page.locator('#copyPair').click()
        page.wait_for_function("document.getElementById('copyPair').textContent.includes('!')")
        assert page.evaluate('navigator.clipboard.readText()') == code
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if output:
            page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
            page.screenshot(path=str(output / 'settings-pair-code-390.png'), full_page=True, animations='disabled')
        # Exercise the existing bridge error handler without an installed connector.
        page.evaluate("window.postMessage({source:'FBPOST_EXTENSION', type:'PAIR_RESULT', result:{error:'Regression bridge failure'}}, location.origin)")
        page.locator('.toast.error').filter(has_text='Regression bridge failure').wait_for()
        page.once("dialog", lambda d: d.dismiss())
        page.locator('form[action="/connector/disconnect"] button').click()
        page.once("dialog", lambda d: d.accept())
        page.locator('form[action="/connector/disconnect"] button').click()
        page.wait_for_load_state()
        assert context.request.get(origin + '/agent-status').json()['online'] is False
    page.emulate_media(reduced_motion='reduce')
    # A focus transition started before the preference change can finish its
    # original duration. Continuous animation must settle within one second.
    page.wait_for_function("document.getAnimations().every(a => a.playState !== 'running')", timeout=1000)
    assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == 'auto'
    page.emulate_media(reduced_motion='no-preference')
    print(f"PASS screen {screen}: {len(themes)*4} viewport/theme renders, screenshots, interactions, reduced motion")


def browser_checks(output, comparison=None, screen=None):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server, WSGIRequestHandler

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix="fbpp-design-test-") as temp:
        # Set before importing app: its module initialization creates directories.
        os.environ.update(DATA_ROOT=temp, USERS_FILE=str(Path(temp) / "users.json"),
                          DATABASE_URL="", SECRET_KEY="isolated-design-system-test",
                          ADMIN_PASSWORD="isolated-admin", CLOUD_WORKER_TOKEN="")
        spec = importlib.util.spec_from_file_location("app", ROOT / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["app"] = module
        spec.loader.exec_module(module)
        server = make_server("127.0.0.1", 0, module.app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_init_script("""(() => {
                    window.designListenerCounts = {};
                    const add = document.addEventListener;
                    document.addEventListener = function(type, ...args) {
                        window.designListenerCounts[type] = (window.designListenerCounts[type] || 0) + 1;
                        return add.call(this, type, ...args);
                    };
                })();""")
                # Keep checks offline and independent of the font CDN.
                context.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(content_type="text/css", body=""))
                context.route("https://fonts.gstatic.com/**", lambda r: r.fulfill(body=""))
                page = context.new_page()
                errors, calls = [], []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("request", lambda r: calls.append((r.method, r.url.removeprefix(origin))) if r.url.startswith(origin) else None)

                page.goto(origin + "/compose")
                assert "/login?next=" in page.url
                page.goto(origin + "/register")
                page.locator("#display_name").fill("Design Regression")
                page.locator("#username").fill("design_regression")
                page.locator("#email").fill("design@example.test")
                page.locator("#password").fill("DesignTest123!")
                page.locator("#confirm_password").fill("DesignTest123!")
                page.locator("button[type=submit]").click()
                page.wait_for_url(origin + "/")
                page.locator("#themeBtn").click()
                assert page.locator("html").get_attribute("data-theme") == "light"
                page.reload()
                assert page.locator("html").get_attribute("data-theme") == "light"
                page.locator("#themeBtn").click()

                page.goto(origin + "/groups")
                page.locator('[name="group_url"]').fill("https://www.facebook.com/groups/design-test")
                page.locator('form[action="/add-group"] button').click()
                page.wait_for_load_state()
                assert page.locator('form[action="/delete-group/0"]').count() == 1
                page.goto(origin + "/compose")
                page.locator('[name="campaign_name"]').fill("UI regression")
                page.locator('[name="content"]').fill("Nội dung tiếng Việt\nEmoji ✨ và https://example.test")
                page.locator('[name="min_delay"]').fill("0")
                page.locator('[name="max_delay"]').fill("0")
                page.locator('form[action="/save-post"] button[type=submit]').last.click()
                page.wait_for_load_state()
                assert "Nội dung tiếng Việt" in page.locator('[name="content"]').input_value()

                # A real file input change exercises FileReader and the existing listener.
                import base64
                png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV1sAAAAASUVORK5CYII=")
                page.locator("#images").set_input_files([
                    {"name": "one.png", "mimeType": "image/png", "buffer": png},
                    {"name": "two.png", "mimeType": "image/png", "buffer": png},
                ])
                page.locator("#newImagePreview img").nth(1).wait_for()
                assert page.locator("#newImagePreview img").count() == 2
                page.locator('form[action="/save-post"] button[type=submit]').last.click()
                page.wait_for_load_state()
                assert page.locator('img[src^="/customer-image/"]').count() >= 2
                # Existing nested forms are intentionally preserved, not repaired here.
                forms_with_images = page.evaluate("""() => [...document.forms].map(f => ({
                    action: new URL(f.action).pathname, method: f.method,
                    fields: [...f.elements].map(e => [e.name, e.type])
                }))""")
                compose_form = next(f for f in forms_with_images if f["action"] == "/save-post")
                assert [["campaign_name","text"],["content","textarea"],["images","file"],
                        ["min_delay","number"],["max_delay","number"]] == [x for x in compose_form["fields"] if x[0]]
                assert len([f for f in forms_with_images if f["action"].startswith('/delete-post-image/')]) == 2

                page.goto(origin + "/settings")
                page.evaluate("""() => { window.pairMessages=[]; window.addEventListener('message', e => {
                    if (e.data?.source === 'FBPOST_WEB') window.pairMessages.push(e.data);
                }); }""")
                before = len([c for c in calls if c == ("POST", "/api/extension/pair-code")])
                page.locator("#pairBtn").click()
                page.wait_for_function("document.getElementById('pairCode').textContent.length === 8")
                page.wait_for_function("window.pairMessages.length === 1")
                # The initial placeholder is also eight characters; read only
                # after the existing handler has published the real API result.
                code = page.locator("#pairCode").inner_text()
                assert page.evaluate("window.pairMessages[0].type") == "PAIR_CONNECTOR"
                assert len([c for c in calls if c == ("POST", "/api/extension/pair-code")]) == before + 1
                paired = context.request.post(origin + "/api/extension/pair", data={"code": code}).json()
                assert paired.get("ok"), f"Isolated pairing failed: {paired}"
                auth = {"X-Device-ID": paired["device_id"], "X-Agent-Token": paired["token"]}
                assert context.request.post(origin + "/api/agent/heartbeat", headers=auth,
                                            data={"facebook_logged_in": True}).ok
                page.goto(origin + "/compose")
                page.locator('form[action="/run-campaign"] button[value="run"]').click()
                page.wait_for_load_state()
                assert page.locator('form[action="/run-campaign"] button[value="run"]').is_disabled()
                job = context.request.get(origin + "/api/agent/job", headers=auth).json()
                assert job["has_job"] and job["job"]["content"].startswith("Nội dung")
                page.locator('form[action="/stop-campaign"] button').click()
                page.wait_for_load_state()
                assert context.request.get(origin + "/api/agent/control", headers=auth).json()["stop_requested"]
                context.request.post(origin + "/api/agent/status", headers=auth,
                                     data={"status": "stopped", "processed": 0, "success": 0, "errors": 0})

                # Existing notification function and close listener, not a replacement.
                page.evaluate("window.showToast('Regression notification', 'warning')")
                notice = page.locator(".toast").filter(has_text="Regression notification")
                notice.wait_for()
                notice.locator(".toast-close").click()
                notice.wait_for(state="detached")

                responsive = []
                for width, height in [(390,844),(768,1024),(1366,768),(1920,1080),(2560,1440),(3840,2160)]:
                    page.set_viewport_size({"width": width, "height": height})
                    for route in ["/", "/compose", "/groups", "/history", "/settings"]:
                        page.goto(origin + route)
                        page.wait_for_timeout(300)
                        measure = page.evaluate("""() => ({
                            overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
                            main_width: document.querySelector('.main').getBoundingClientRect().width,
                            forms: [...document.forms].map(f => ({action:new URL(f.action).pathname,
                                method:f.method, fields:[...f.elements].map(e=>[e.name,e.type])})),
                            theme: document.documentElement.dataset.theme
                        })""")
                        responsive.append({"width":width,"route":route,**measure})
                        assert measure["overflow"] <= 1, f"Horizontal overflow: {width}, {route}: {measure['overflow']}"
                        expected_width = width - (248 if width > 960 else 0)
                        assert measure["main_width"] >= expected_width - 1, f"Main content collapsed at {width}: {route}"
                    if width == 390:
                        page.locator("#menuBtn").click()
                        assert "open" in page.locator("#sidebar").get_attribute("class")
                        page.locator("#overlay").click(position={"x":380,"y":500})
                        assert "open" not in page.locator("#sidebar").get_attribute("class")

                page.set_viewport_size({"width":1440,"height":1000})
                page.goto(origin + "/")
                page.wait_for_function("document.documentElement.dataset.uiMotionReady === 'true'")
                assert page.evaluate("window.designListenerCounts.visibilitychange") == 1
                api_calls_before_motion = [c for c in calls if c[1].startswith("/api/")]
                # Re-execution must not register another lifecycle listener.
                page.add_script_tag(content=(ROOT / "static/js/ui-motion.js").read_text(encoding="utf-8"))
                assert page.evaluate("window.designListenerCounts.visibilitychange") == 1

                # Opt-in primitives in a test-only fixture; production confirmations stay native.
                page.evaluate("""() => {
                    const host = document.createElement('div'); host.id = 'design-fixture';
                    host.innerHTML = '<dialog class="ds-dialog" aria-labelledby="design-dialog-title">' +
                        '<h2 id="design-dialog-title" class="ds-dialog-title">Design dialog</h2>' +
                        '<form method="dialog"><button class="btn secondary">Close</button></form></dialog>' +
                        '<span class="ds-spinner" role="status" aria-label="Loading"></span>' +
                        '<div class="skeleton" aria-hidden="true"></div>';
                    document.body.appendChild(host);
                }""")
                dialog = page.locator("#design-fixture dialog")
                assert not dialog.is_visible()
                page.evaluate("document.querySelector('#design-fixture dialog').showModal()")
                assert dialog.is_visible()
                dialog.locator("button").click()
                assert not dialog.is_visible()
                page.emulate_media(reduced_motion="reduce")
                assert page.evaluate("getComputedStyle(document.querySelector('.page')).animationName") == "none"
                assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
                assert page.evaluate("getComputedStyle(document.querySelector('.ds-spinner')).animationName") == "none"
                page.emulate_media(reduced_motion="no-preference")
                assert page.evaluate("getComputedStyle(document.querySelector('.ds-spinner')).animationName") == "ds-spin"
                page.evaluate("""() => {
                    Object.defineProperty(document, 'visibilityState', {configurable:true, get:()=> 'hidden'});
                    document.dispatchEvent(new Event('visibilitychange'));
                }""")
                assert page.evaluate("getComputedStyle(document.querySelector('.ds-spinner')).animationPlayState") == "paused"
                page.evaluate("""() => {
                    delete document.visibilityState;
                    document.dispatchEvent(new Event('visibilitychange'));
                    document.getElementById('design-fixture').remove();
                }""")
                assert page.locator("html").get_attribute("data-ui-paused") is None
                assert [c for c in calls if c[1].startswith("/api/")] == api_calls_before_motion, "Motion triggered an API call"
                print("PASS: native dialog skin, loading/reduced motion, hidden-tab pause, idempotent UI listener")
                if output:
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output / "dashboard-dark.png"), full_page=True, animations="disabled")
                    page.locator("#themeBtn").click()
                    page.screenshot(path=str(output / "dashboard-light.png"), full_page=True, animations="disabled")
                    page.locator("#themeBtn").click()
                page.goto(origin + "/logout")
                for auth_route in ["/login", "/register"]:
                    for width in [390, 768, 1920, 2560, 3840]:
                        page.set_viewport_size({"width":width,"height":1000})
                        page.goto(origin + auth_route)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Auth overflow: {auth_route}, {width}"
                        assert page.locator('button[type="submit"]').is_visible()
                page.set_viewport_size({"width":1440,"height":1000})
                page.goto(origin + "/login")
                page.locator("#login").fill("design_regression")
                page.locator("#password").fill("DesignTest123!")
                page.locator("#togglePassword").click()
                assert page.locator("#password").get_attribute("type") == "text"
                page.locator('button[type="submit"]').click()
                page.wait_for_url(origin + "/")
                assert not errors, f"Browser console/page errors: {errors}"
                result = {"responsive":responsive,"forms_with_images":forms_with_images,
                          "console_errors":errors,"post_requests":[c for c in calls if c[0] == "POST"]}
                if comparison:
                    baseline = json.loads((comparison / "result.json").read_text(encoding="utf-8"))
                    def normalized(value):
                        return re.sub(r"post_[a-f0-9]+\.png", "post_IMAGE.png", json.dumps(value, sort_keys=True))
                    baseline_save = next(f for f in baseline["forms_with_images"] if f["action"] == "/save-post")
                    assert not any(x[0] in {'min_delay','max_delay'} for x in baseline_save["fields"]), "Baseline no longer demonstrates the Compose form defect"
                    assert normalized(result["post_requests"]) == normalized(baseline["post_requests"]), "Page POST request sequence changed"
                    for current, previous in zip(responsive, baseline["responsive"], strict=True):
                        if current["route"] != "/compose":
                            assert normalized(current["forms"]) == normalized(previous["forms"]), "Rendered form contract changed"
                        assert current["overflow"] <= previous["overflow"] + 1, f"New overflow: {current['route']}, {current['width']}"
                    print("PASS: baseline comparison of form ownership, POST sequence and responsive overflow")
                if output:
                    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                if screen:
                    screen_checks(module, context, page, origin, screen, output)
                    assert not errors, f"Screen console errors: {errors}"
                print("PASS: real Flask auth/session, forms, uploads, pairing message/API, isolated job/stop, theme, toast, mobile menu")
                print("PASS: 40 page/viewport checks (including auth); console errors: 0")
                print("Responsive overflow:", [(x["width"],x["route"],x["overflow"]) for x in responsive if x["overflow"] > 1])
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="Directory containing pre-change result.json")
    parser.add_argument("--screen", choices=["dashboard","groups","history","login","register","compose","settings"])
    args = parser.parse_args()
    check_contracts()
    if not args.contracts_only:
        browser_checks(args.output, args.compare, args.screen)
