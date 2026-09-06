"""Final isolated audit for routes, API contracts, UI performance and assets."""
import argparse
import base64
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import threading

from design_system_regression import ROOT, check_contracts


EXPECTED_ROUTES = {
    ('GET', '/'), ('GET', '/compose'), ('GET', '/customer-image/<filename>'),
    ('POST', '/save-post'), ('POST', '/delete-post-image/<filename>'),
    ('POST', '/delete-all-post-images'), ('GET', '/groups'), ('POST', '/add-group'),
    ('POST', '/delete-group/<int:index>'), ('GET', '/history'), ('POST', '/clear-history'),
    ('GET', '/settings'), ('POST', '/settings'), ('GET', '/register'), ('POST', '/register'),
    ('GET', '/login'), ('POST', '/login'), ('GET', '/logout'),
    ('POST', '/api/extension/pair-code'), ('POST', '/api/extension/pair'),
    ('POST', '/connector/disconnect'), ('GET', '/api/facebook/status'),
    ('POST', '/api/connect/request'), ('GET', '/api/connect/web-status/<request_id>'),
    ('POST', '/api/connect/register'), ('POST', '/api/connect/status'),
    ('GET', '/admin/login'), ('POST', '/admin/login'), ('GET', '/admin/logout'),
    ('GET', '/admin/devices'), ('POST', '/admin/devices/<request_id>/approve'),
    ('POST', '/admin/devices/<request_id>/reject'),
    ('POST', '/admin/device/<customer_id>/<device_id>/disconnect'),
    ('POST', '/run-campaign'), ('POST', '/stop-campaign'),
    ('POST', '/pause-campaign'), ('POST', '/resume-campaign'),
    ('GET', '/campaign-status'), ('GET', '/agent-status'),
    ('POST', '/reset-facebook-profile'), ('GET', '/api/cloud/job'),
    ('GET', '/api/cloud/image/<customer_id>/<filename>'), ('GET', '/api/cloud/control'),
    ('POST', '/api/cloud/status'), ('POST', '/api/cloud/control/ack'),
    ('POST', '/api/agent/heartbeat'), ('GET', '/api/agent/job'),
    ('POST', '/api/agent/status'), ('GET', '/api/agent/control'),
    ('POST', '/api/agent/control/ack'), ('GET', '/api/agent/image/<filename>'),
    ('GET', '/customer-info'), ('POST', '/new-customer-session'), ('GET', '/health'),
}


def import_isolated(temp):
    os.environ.update(
        DATA_ROOT=str(Path(temp) / 'data'), USERS_FILE=str(Path(temp) / 'users.json'),
        DATABASE_URL='', SECRET_KEY='isolated-phase5-test', ADMIN_PASSWORD='phase5-admin',
        CLOUD_WORKER_TOKEN='phase5-cloud-token',
    )
    spec = importlib.util.spec_from_file_location('app', ROOT / 'app.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['app'] = module
    spec.loader.exec_module(module)
    return module


def assert_status(response, expected, label):
    assert response.status_code == expected, f'{label}: {response.status_code}, {response.get_data(as_text=True)[:300]}'
    return response


def route_and_api_checks(module):
    actual = set()
    for rule in module.app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            actual.add((method, rule.rule))
    assert actual == EXPECTED_ROUTES, {
        'missing': sorted(EXPECTED_ROUTES - actual), 'unexpected': sorted(actual - EXPECTED_ROUTES)
    }

    client = module.app.test_client()
    for path in ['/', '/compose', '/groups', '/history', '/settings', '/customer-info']:
        response = client.get(path)
        assert response.status_code == 302 and '/login?next=' in response.location, path
    health = assert_status(client.get('/health'), 200, 'health').get_json()
    assert health['status'] == 'ok' and health['mode'] == 'chrome_extension'
    assert_status(client.get('/admin/devices'), 302, 'admin protected')
    assert_status(client.post('/api/agent/heartbeat', json={}), 401, 'agent unauthorized')
    assert_status(client.get('/api/agent/job'), 401, 'agent job unauthorized')
    assert_status(client.get('/api/agent/control'), 401, 'agent control unauthorized')
    assert_status(client.post('/api/agent/control/ack', json={}), 401, 'agent ack unauthorized')
    assert_status(client.get('/api/agent/image/missing.png'), 401, 'agent image unauthorized')
    cloud_header = {'X-Cloud-Worker-Token': 'phase5-cloud-token'}
    for method, path in [('get','/api/cloud/job'), ('get','/api/cloud/control'),
                         ('post','/api/cloud/status'), ('post','/api/cloud/control/ack')]:
        response = getattr(client, method)(path, json={} if method == 'post' else None)
        assert_status(response, 401, path + ' unauthorized')
    assert_status(client.get('/api/cloud/image/no-user/no.png'), 401, 'cloud image unauthorized')

    registration = client.post('/register', data={
        'display_name': 'Phase 5 Audit', 'username': 'phase5_audit',
        'email': 'phase5@example.test', 'password': 'Phase5Test123!',
        'confirm_password': 'Phase5Test123!',
    })
    assert_status(registration, 302, 'register')
    with client.session_transaction() as session:
        user_id = session['user_id']
        assert session.permanent
    for path in ['/', '/compose', '/groups', '/history', '/settings']:
        assert_status(client.get(path), 200, 'authenticated ' + path)
    assert client.get('/customer-info').get_json()['customer_id'] == user_id
    assert client.get('/campaign-status').get_json()['running'] is False
    assert client.get('/agent-status').get_json()['online'] is False
    assert client.get('/api/facebook/status').get_json()['mode'] == 'chrome_extension'

    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV1sAAAAASUVORK5CYII=')
    save = client.post('/save-post', data={
        'campaign_name': 'Final audit', 'content': 'Nội dung Phase 5',
        'min_delay': '2', 'max_delay': '4', 'images': (BytesIO(png), 'audit.png'),
    }, content_type='multipart/form-data')
    assert_status(save, 302, 'save post with image')
    settings = module.load_settings(user_id)
    assert settings['min_delay'] == 2 and settings['max_delay'] == 4
    filename = settings['post_images'][0]
    assert_status(client.get('/customer-image/' + filename), 200, 'customer image')
    assert_status(client.post('/add-group', data={'group_url':'https://www.facebook.com/groups/phase5'}), 302, 'add group')

    pair_code = assert_status(client.post('/api/extension/pair-code'), 200, 'pair code').get_json()['code']
    pair = assert_status(client.post('/api/extension/pair', json={
        'code': pair_code, 'device_name':'Phase 5 Chrome', 'extension_version':'1.0.0'
    }), 200, 'pair').get_json()
    agent_header = {'X-Device-ID': pair['device_id'], 'X-Agent-Token': pair['token']}
    heartbeat = client.post('/api/agent/heartbeat', headers=agent_header,
                            json={'facebook_logged_in':True, 'device_name':'Phase 5 Chrome'})
    assert_status(heartbeat, 200, 'heartbeat')
    assert client.get('/agent-status').get_json()['online'] is True
    assert_status(client.post('/run-campaign'), 302, 'run campaign')
    job = assert_status(client.get('/api/agent/job', headers=agent_header), 200, 'agent job').get_json()
    assert job['has_job'] and job['job']['content'] == 'Nội dung Phase 5'
    assert_status(client.get('/api/agent/image/' + filename, headers=agent_header), 200, 'agent image')
    assert_status(client.post('/stop-campaign'), 302, 'stop campaign')
    assert client.get('/api/agent/control', headers=agent_header).get_json()['stop_requested'] is True
    assert_status(client.post('/api/agent/control/ack', headers=agent_header, json={'stop_ack':True}), 200, 'agent ack')
    assert client.get('/api/agent/control', headers=agent_header).get_json()['stop_requested'] is False
    assert_status(client.post('/api/agent/status', headers=agent_header, json={
        'status':'finished', 'processed':1, 'success':1, 'errors':0, 'message':'Phase 5 done'
    }), 200, 'agent status')

    connect = assert_status(client.post('/api/connect/request'), 200, 'connect request').get_json()
    request_id = connect['request_id']
    item = module.load_connect_requests()[request_id]
    assert client.get('/api/connect/web-status/' + request_id).get_json()['status'] == 'pending_admin'
    assert_status(client.post('/api/connect/register', json={'request_id':request_id, 'secret':'wrong'}), 401, 'connect wrong secret')
    assert_status(client.post('/api/connect/register', json={'request_id':request_id, 'secret':item['secret']}), 200, 'connect register')
    assert_status(client.post('/api/connect/status', json={
        'request_id':request_id, 'secret':item['secret'], 'device_id':item['device_id']
    }), 200, 'connect status')
    assert_status(client.post('/admin/login', data={'password':'wrong'}), 200, 'admin wrong password')
    assert_status(client.post('/admin/login', data={'password':'phase5-admin'}), 302, 'admin login')
    assert_status(client.get('/admin/devices'), 200, 'admin devices')
    assert_status(client.post(f'/admin/devices/{request_id}/approve'), 302, 'admin approve')
    approved = client.post('/api/connect/status', json={
        'request_id':request_id, 'secret':item['secret'], 'device_id':item['device_id']
    }).get_json()
    assert approved['status'] == 'approved' and approved['agent_token']
    cloud_device = approved['device_id']

    module.save_jobs(user_id, {cloud_device:{
        'job_id':'job_phase5_cloud', 'status':'pending', 'mode':'cloud',
        'created_at':module.now_iso(), 'campaign_name':'Cloud audit',
        'groups':['https://www.facebook.com/groups/phase5'], 'content':'Cloud content',
        'images':[filename], 'min_delay':0, 'max_delay':0,
    }})
    cloud_job = assert_status(client.get('/api/cloud/job', headers=cloud_header), 200, 'cloud job').get_json()
    assert cloud_job['has_job'] and cloud_job['job']['job_id'] == 'job_phase5_cloud'
    assert_status(client.get(f'/api/cloud/image/{user_id}/{filename}', headers=cloud_header), 200, 'cloud image')
    module.save_control(user_id, {cloud_device:{'stop_requested':True, 'reset_profile_requested':True}})
    control = client.get(f'/api/cloud/control?customer_id={user_id}&device_id={cloud_device}', headers=cloud_header).get_json()
    assert control['stop_requested'] and control['reset_profile_requested']
    assert_status(client.post('/api/cloud/control/ack', headers=cloud_header, json={
        'customer_id':user_id, 'device_id':cloud_device, 'stop_ack':True, 'reset_profile_ack':True,
    }), 200, 'cloud ack')
    assert_status(client.post('/api/cloud/status', headers=cloud_header, json={
        'customer_id':user_id, 'device_id':cloud_device, 'job_id':'job_phase5_cloud',
        'status':'finished', 'processed':1, 'success':1, 'errors':0, 'message':'Cloud done',
    }), 200, 'cloud status')

    rejected = client.post('/api/connect/request').get_json()['request_id']
    assert_status(client.post(f'/admin/devices/{rejected}/reject'), 302, 'admin reject')
    assert module.load_connect_requests()[rejected]['status'] == 'rejected'
    assert_status(client.post(f'/admin/device/{user_id}/{cloud_device}/disconnect'), 302, 'admin disconnect')
    assert_status(client.get('/admin/logout'), 302, 'admin logout')
    assert_status(client.get('/admin/devices'), 302, 'admin protected after logout')

    # Fix verification: save and delete must work while saved images are rendered.
    save_again = client.post('/save-post', data={
        'campaign_name':'Saved-image form fixed', 'content':'Still saved',
        'min_delay':'6', 'max_delay':'9',
    })
    assert_status(save_again, 302, 'save with existing image')
    assert module.load_settings(user_id)['min_delay'] == 6
    assert_status(client.post('/delete-post-image/' + filename), 302, 'delete one image')
    assert filename not in module.load_settings(user_id)['post_images']
    assert_status(client.post('/delete-all-post-images'), 302, 'delete all images')
    assert_status(client.post('/reset-facebook-profile'), 302, 'reset facebook state')
    assert_status(client.post('/delete-group/0'), 302, 'delete group')
    assert_status(client.post('/clear-history'), 302, 'clear history')
    assert_status(client.post('/new-customer-session'), 302, 'new customer session compatibility')
    assert_status(client.get('/logout'), 302, 'logout')
    login = client.post('/login?next=/groups', data={
        'login':'phase5@example.test', 'password':'Phase5Test123!', 'next':'/groups'
    })
    assert_status(login, 302, 'login by email')
    assert login.location.endswith('/groups')
    print(f'PASS: exact route contract ({len(EXPECTED_ROUTES)} method/path pairs), public/protected routes and auth/session')
    print('PASS: CRUD/forms, extension pair, agent job/status/control/image, compatibility connect/admin flow, cloud job/status/control/image')


def asset_audit():
    frontend = [p for root in ['static', 'templates', 'extension']
                for p in (ROOT/root).rglob('*') if p.is_file()]
    sizes = {str(p.relative_to(ROOT)).replace('\\','/'): p.stat().st_size for p in frontend}
    css = '\n'.join(p.read_text(encoding='utf-8') for p in frontend if p.suffix == '.css')
    defined = set(re.findall(r'@keyframes\s+([\w-]+)', css))
    referenced = set(re.findall(r'animation(?:-name)?\s*:\s*([\w-]+)', css)) - {'none'}
    unused = sorted(defined - referenced)
    assert not unused, f'Unused keyframes: {unused}'
    compose = (ROOT/'templates/compose.html').read_text(encoding='utf-8')
    saved_images = re.findall(r'<img\b[^>]*>', compose, re.S)
    assert saved_images and all('loading="lazy"' in tag and 'decoding="async"' in tag for tag in saved_images)
    js_files = [p for p in frontend if p.suffix == '.js']
    normalized = [re.sub(r'\s+', '', p.read_text(encoding='utf-8')) for p in js_files]
    assert len(normalized) == len(set(normalized)), 'Duplicate standalone JavaScript file detected'
    css_rules = {}
    for path in (p for p in frontend if p.suffix == '.css'):
        scope = 'extension' if path.parts[-2] == 'extension' else 'web'
        source = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
        for selector, body in re.findall(r'([^{}]+)\{([^{}]+)\}', source):
            selector = re.sub(r'\s+', ' ', selector).strip()
            body = re.sub(r'\s+', ' ', body).strip()
            if (selector.startswith('@') or not body or
                    re.fullmatch(r'(?:from|to|\d+(?:\.\d+)?%)(?:\s*,\s*\d+(?:\.\d+)?%)*', selector)):
                continue
            # Popup CSS is an isolated extension document, not part of the web bundle.
            key = (scope, selector, body)
            css_rules.setdefault(key, []).append(str(path.relative_to(ROOT)).replace('\\', '/'))
    duplicate_css = [
        {'selector': selector, 'files': files}
        for (_scope, selector, _body), files in css_rules.items() if len(files) > 1
    ]
    assert not duplicate_css, f'Exact duplicate CSS rules detected: {duplicate_css}'
    inline_scripts = []
    for path in (p for p in frontend if p.suffix == '.html'):
        for source in re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',
                                 path.read_text(encoding='utf-8'), flags=re.S | re.I):
            normalized_source = re.sub(r'\s+', '', source)
            if normalized_source:
                inline_scripts.append((normalized_source,
                                       str(path.relative_to(ROOT)).replace('\\', '/')))
    duplicate_inline_js = []
    for source in set(item[0] for item in inline_scripts):
        files = [path for item, path in inline_scripts if item == source]
        if len(files) > 1:
            duplicate_inline_js.append(files)
    assert not duplicate_inline_js, f'Duplicate inline JavaScript blocks detected: {duplicate_inline_js}'
    page_css = sum(size for name,size in sizes.items() if name.startswith('static/css/'))
    assert max(sizes.values()) < 25 * 1024, 'Unexpected heavy frontend asset'
    return {'files': sizes, 'total_frontend_bytes':sum(sizes.values()),
            'design_css_bytes':page_css, 'largest':max(sizes.items(), key=lambda x:x[1]),
            'keyframes_defined':sorted(defined), 'unused_keyframes':unused,
            'duplicate_css_rules':duplicate_css,
            'duplicate_standalone_js':False,
            'duplicate_inline_js':duplicate_inline_js,
            'saved_image_lazy_async':True}


def browser_performance(module, output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server, WSGIRequestHandler
    class QuietHandler(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass
    server = make_server('127.0.0.1', 0, module.app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--js-flags=--expose-gc'])
            context = browser.new_context(viewport={'width':1920,'height':1080})
            context.route('https://fonts.googleapis.com/**', lambda r:r.fulfill(content_type='text/css', body=''))
            context.route('https://fonts.gstatic.com/**', lambda r:r.fulfill(body=''))
            context.add_init_script("""(() => {
              window.__listeners = {};
              const add = EventTarget.prototype.addEventListener;
              EventTarget.prototype.addEventListener = function(type, ...rest) {
                const owner = this === window ? 'window' : this === document ? 'document' : (this.id || this.tagName || 'other');
                const key = owner + ':' + type;
                window.__listeners[key] = (window.__listeners[key] || 0) + 1;
                return add.call(this, type, ...rest);
              };
            })();""")
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.on('console', lambda m:errors.append(m.text) if m.type == 'error' else None)
            page.goto(origin+'/register')
            values = {'display_name':'Performance Audit', 'username':'performance_audit',
                      'email':'performance@example.test', 'password':'Performance123!',
                      'confirm_password':'Performance123!'}
            for name,value in values.items(): page.locator(f'[name={name}]').fill(value)
            page.locator('button[type=submit]').click()
            page.wait_for_url(origin+'/')
            nav_metrics, fps_metrics = [], []
            routes = ['/', '/compose', '/groups', '/history', '/settings']
            for width,height in [(390,844),(768,1024),(1366,768),(1920,1080),(2560,1440),(3840,2160)]:
                page.set_viewport_size({'width':width,'height':height})
                for route in routes:
                    page.goto(origin+route, wait_until='load')
                    timing = page.evaluate("""() => {
                      const n=performance.getEntriesByType('navigation')[0];
                      const resources=performance.getEntriesByType('resource');
                      return {dcl:n.domContentLoadedEventEnd, load:n.loadEventEnd,
                        response:n.responseEnd, resources:resources.length,
                        transfer:resources.reduce((s,r)=>s+(r.transferSize||0),0)};
                    }""")
                    timing.update(width=width, route=route,
                                  overflow=page.evaluate('Math.max(0,document.documentElement.scrollWidth-innerWidth)'))
                    assert timing['overflow'] == 0
                    nav_metrics.append(timing)
            for route in ['/', '/compose', '/settings']:
                for width,height in [(390,844),(1920,1080)]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.goto(origin+route)
                    sample = page.evaluate("""() => new Promise(resolve => {
                      const samples=[]; let first;
                      requestAnimationFrame(function frame(t) {
                        if (first === undefined) first=t; samples.push(t);
                        if (t-first < 1000) requestAnimationFrame(frame); else {
                          const gaps=samples.slice(1).map((v,i)=>v-samples[i]);
                          resolve({frames:samples.length, elapsed:samples.at(-1)-samples[0], gaps});
                        }
                      });
                    })""")
                    gaps = sample.pop('gaps')
                    sample.update(route=route, width=width,
                                  fps=round((sample['frames']-1)*1000/sample['elapsed'],1),
                                  p95_gap_ms=round(sorted(gaps)[int(len(gaps)*.95)-1],2),
                                  gaps_over_25ms=sum(g>25 for g in gaps))
                    assert sample['fps'] >= 45 and sample['p95_gap_ms'] < 35, sample
                    fps_metrics.append(sample)
            page.set_viewport_size({'width':1920,'height':1080})
            page.goto(origin+'/')
            cdp = context.new_cdp_session(page)
            cdp.send('HeapProfiler.collectGarbage')
            before = cdp.send('Runtime.getHeapUsage')['usedSize']
            listeners_before = page.evaluate('window.__listeners')
            page.evaluate("""() => {
              for(let i=0;i<30;i++) showToast('Memory audit '+i, 'info');
              document.querySelectorAll('.toast').forEach(closeToast);
              for(let i=0;i<30;i++) { themeBtn.click(); themeBtn.click(); }
            }""")
            page.wait_for_timeout(5000)
            cdp.send('HeapProfiler.collectGarbage')
            after = cdp.send('Runtime.getHeapUsage')['usedSize']
            listeners_after = page.evaluate('window.__listeners')
            assert page.locator('.toast').count() == 0
            assert listeners_after == listeners_before
            assert after - before < 2 * 1024 * 1024, {'before':before,'after':after}
            assert not errors, errors
            result = {'navigation':nav_metrics, 'fps':fps_metrics,
                      'max_load_ms':round(max(x['load'] for x in nav_metrics),2),
                      'median_load_ms':round(statistics.median(x['load'] for x in nav_metrics),2),
                      'max_transfer_bytes':max(x['transfer'] for x in nav_metrics),
                      'heap_before':before, 'heap_after':after, 'heap_delta':after-before,
                      'listeners':listeners_after, 'console_errors':errors}
            output.mkdir(parents=True, exist_ok=True)
            (output/'performance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            browser.close()
            print(f"PASS: 30 responsive performance navigations; load median {result['median_load_ms']}ms, max {result['max_load_ms']}ms")
            print('PASS: animation frame samples:', [(x['route'],x['width'],x['fps'],x['p95_gap_ms']) for x in fps_metrics])
            print(f"PASS: repeated toast/theme memory/listeners; heap delta {result['heap_delta']} bytes, listener registry unchanged")
            return result
    finally:
        server.shutdown(); thread.join(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check_contracts()
    with tempfile.TemporaryDirectory(prefix='fbpp-phase5-') as temp:
        app_module = import_isolated(temp)
        route_and_api_checks(app_module)
        assets = asset_audit()
        perf = browser_performance(app_module, args.output)
        (args.output/'asset-audit.json').write_text(json.dumps(assets, indent=2), encoding='utf-8')
        print(f"PASS: {len(assets['files'])} frontend files, {assets['total_frontend_bytes']} bytes; no duplicate standalone JS or unused keyframes")
