"""PostgreSQL admin pagination and real production startup security, using localhost fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from phase10_postgres_integration import load_postgres_app, register
from phase11_admin_regression import captured_templates


def run(url):
    parsed = urlsplit(url)
    assert parsed.hostname in {'127.0.0.1', 'localhost'} and parsed.path.startswith('/fbpostpro_phase11')
    with tempfile.TemporaryDirectory(prefix='fbpp-admin-pg-') as temp:
        m = load_postgres_app(Path(temp), url)
        clients, owners = [], []
        for index in range(5):
            c = m.app.test_client()
            owner = register(c, f'adminscale_{index}')
            clients.append(c)
            owners.append(owner)
            assert len(m.import_groups(owner, [f'https://www.facebook.com/groups/adminscale-{index}-{n}' for n in range(100)])['added']) == 100
        users = m.load_users()
        users[owners[0]]['role'] = 'admin'
        users[owners[1]]['max_facebook_accounts'] = 100
        m.save_users(users)
        for index in range(61):
            m.create_facebook_account(owners[1], f'Pagination {index:03d}', str(81000+index))
        m.save_devices(owners[1], {f'ext_adminscale_{i}': {
            'name': f'Worker {i:03d}', 'last_seen': m.now_iso() if i < 60 else '2026-99-99Tbad',
            'worker_state': 'idle', 'token_hash': 'private_device_digest',
        } for i in range(61)})
        admin = clients[0]
        for route in ['/admin', '/admin/users', f'/admin/users/{owners[1]}', '/admin/accounts?page=2', '/admin/groups?page=10', '/admin/workers?page=2', '/admin/logs', '/admin/audit']:
            response = admin.get(route)
            assert response.status_code == 200, (route, response.get_data(as_text=True)[:100])
            assert 'private_device_digest' not in response.get_data(as_text=True)
        with captured_templates(m.app) as ctx:
            assert admin.get('/admin/workers?state=offline').status_code == 200
        assert ctx[-1]['pagination']['total'] == 1
        with captured_templates(m.app) as ctx:
            assert admin.get('/admin/accounts?page=2').status_code == 200
        assert ctx[-1]['pagination']['total'] == 61
        assert ctx[-1]['pagination']['page'] == 2
        assert clients[1].get('/api/admin/dashboard').status_code == 403
        print('PASS PostgreSQL admin: 5 users/500 groups/61 accounts+workers, page 2 accessible, malformed timestamps safe, admin-only metrics')

        env = os.environ.copy()
        env.update(APP_ENV='production', DATABASE_URL=url, SECRET_KEY='production-isolated-test-' + 's'*32,
            ALLOWED_HOSTS='app.example,custom.example', ENABLE_SCHEDULER='true', ENABLE_LEGACY_ADMIN_AUTH='true',
            TRUST_PROXY_HEADERS='true', DATA_ROOT=temp)
        code = '''
import time
import app
assert not app.LEGACY_ADMIN_AUTH_ENABLED
assert app.SCHEDULER_THREAD and app.SCHEDULER_THREAD.is_alive()
deadline = time.monotonic() + 10
while not app.SCHEDULER_LAST_TICK_AT and time.monotonic() < deadline:
    time.sleep(.05)
c=app.app.test_client()
assert c.get('/ready',base_url='https://app.example').status_code==200
assert c.get('/ready',base_url='https://custom.example').status_code==200
assert c.get('/ready',base_url='https://unknown.example',headers={'X-Forwarded-Host':'app.example'}).status_code==400
assert c.post('/login',base_url='https://app.example',headers={'Origin':'https://evil.example'}).status_code==403
with c.session_transaction(base_url='https://app.example') as s:
    s['admin_logged_in']=True
assert c.get('/api/admin/dashboard',base_url='https://app.example').status_code==401
assert not app.app.debug and app.app.config['SESSION_COOKIE_SECURE']
app.SCHEDULER_STOP_EVENT.set()
app.SCHEDULER_THREAD.join(3)
print('PASS production startup: live scheduler, host/custom domain and forwarded-host rejection, legacy bypass denied, CSRF/cookies/debug safe')
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())


if __name__ == '__main__':
    run(os.environ['PHASE11_ADMIN_POSTGRES_URL'])
