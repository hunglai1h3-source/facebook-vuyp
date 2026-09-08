"""Native MV3 popup + real Flask regression, using disposable storage/profile.

No Facebook content is loaded and no job is created. The Open Facebook button
creates a real tab whose document is fulfilled locally with blank test HTML.
"""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading

from design_system_regression import ROOT, check_contracts


def run(output):
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server, WSGIRequestHandler

    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix='fbpp-popup-test-') as temp:
        os.environ.update(DATA_ROOT=str(Path(temp)/'data'), USERS_FILE=str(Path(temp)/'users.json'),
                          DATABASE_URL='', SECRET_KEY='isolated-popup-test',
                          ADMIN_PASSWORD='isolated-admin', CLOUD_WORKER_TOKEN='')
        spec = importlib.util.spec_from_file_location('app', ROOT/'app.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules['app'] = module
        spec.loader.exec_module(module)
        requests = []

        @module.app.before_request
        def record_test_request():
            from flask import request
            requests.append((request.method, request.path))

        server = make_server('127.0.0.1', 0, module.app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                extension = str(ROOT/'extension')
                context = p.chromium.launch_persistent_context(str(Path(temp)/'profile'),
                    channel='chromium', headless=True,
                    args=['--disable-extensions-except='+extension, '--load-extension='+extension])
                context.route('https://www.facebook.com/**', lambda r: r.fulfill(
                    status=200, content_type='text/html', body='<!doctype html><title>Isolated navigation check</title>'))
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.goto(worker.url.replace('service_worker.js', 'popup.html'))
                assert page.locator('#status').get_attribute('class') == 'status offline'
                page.locator('#connect').click()
                assert page.locator('#message').inner_text()
                assert not [r for r in requests if r[1].startswith('/api/')]
                # Authentication is real, but belongs only to this test account.
                response = context.request.post(origin+'/register', form={
                    'display_name':'Popup Regression', 'username':'popup_regression',
                    'email':'popup@example.test', 'password':'PopupTest123!', 'confirm_password':'PopupTest123!'})
                assert response.ok
                code = context.request.post(origin+'/api/extension/pair-code').json()['code']
                page.locator('#server').fill(origin+'///')
                page.locator('#code').fill(code.lower())
                page.locator('#connect').click()
                expect(page.locator('#status')).to_have_class('status online')
                assert page.locator('#connect').is_enabled()
                assert page.locator('#code').input_value() == ''
                config = page.evaluate('chrome.storage.local.get(null)')
                assert config['serverOrigin'] == origin and config['deviceId'] and config['token']
                assert len([r for r in requests if r == ('POST','/api/extension/pair')]) == 1
                assert ('POST','/api/agent/heartbeat') in requests
                assert ('GET','/api/agent/job') in requests
                page.locator('#check').click()
                expect(page.locator('#message')).to_have_text(re.compile(r'^\u0110\u00e3'))
                page.reload()
                expect(page.locator('#status')).to_have_class('status online')
                output.mkdir(parents=True, exist_ok=True)
                measurements = []
                for state in ['online', 'offline']:
                    if state == 'offline':
                        page.locator('#disconnect').click()
                        expect(page.locator('#status')).to_have_class('status offline')
                        config_after = page.evaluate('chrome.storage.local.get(null)')
                        assert config_after.get('serverOrigin') == origin
                        assert not any(k in config_after for k in ['deviceId','token','customerId'])
                        # Local disconnect must not revoke the server's device.
                        assert config['deviceId'] in module.load_devices(config['customerId'])
                    for width in [320, 360, 390, 768, 1920, 3840]:
                        page.set_viewport_size({'width':width,'height':700})
                        page.wait_for_timeout(180)
                        overflow = page.evaluate('Math.max(0, document.documentElement.scrollWidth-innerWidth)')
                        measurements.append({'state':state, 'width':width, 'overflow':overflow})
                        assert overflow == 0, measurements[-1]
                        for ident in ['server','code','connect','check','facebook','disconnect']:
                            assert page.locator('#'+ident).is_visible()
                        if width in [360,390]:
                            page.screenshot(path=str(output/f'popup-{state}-{width}.png'), full_page=True, animations='disabled')
                # Use native tabs.create; the test route prevents Facebook traffic.
                with context.expect_page() as opened:
                    page.locator('#facebook').click()
                facebook_tab = opened.value
                try:
                    facebook_tab.wait_for_url('https://www.facebook.com/', timeout=5000)
                except Exception:
                    pass
                if facebook_tab.url != 'https://www.facebook.com/':
                    facebook_tab.goto('https://www.facebook.com/')
                facebook_tab.wait_for_load_state()
                if facebook_tab.title() != 'Isolated navigation check':
                    facebook_tab.reload()
                    facebook_tab.wait_for_load_state()
                assert facebook_tab.url == 'https://www.facebook.com/'
                assert facebook_tab.title() == 'Isolated navigation check'
                facebook_tab.close()
                page.locator('#check').click()
                expect(page.locator('#message')).not_to_have_text('\u0110ang ki\u1ec3m tra...')
                assert page.locator('#status').get_attribute('class') == 'status offline'
                page.emulate_media(reduced_motion='reduce')
                page.wait_for_timeout(250)
                assert page.evaluate("() => document.getAnimations().every(a=>a.playState!=='running')")
                assert not errors, errors
                assert not any(path in ['/run-campaign','/api/agent/status'] for method,path in requests)
                (output/'result.json').write_text(json.dumps({'responsive':measurements,
                    'console_errors':errors, 'requests':requests, 'native_extension':True,
                    'facebook_navigation':'native tab, blank document fulfilled in test only'}, indent=2), encoding='utf-8')
                context.close()
                print('PASS: native MV3 popup/storage/service-worker messaging, real Flask pairing/heartbeat/job lookup')
                print('PASS: empty validation, URL/code normalization, persistence, check, local disconnect, Facebook button arguments')
                print('PASS: 12 state/viewport renders, no overflow, reduced motion, zero console errors; no Facebook/job execution')
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check_contracts()
    run(args.output)
