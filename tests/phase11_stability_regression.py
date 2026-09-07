"""Risk regression: isolated local data and optional localhost PostgreSQL only."""
import importlib.util
import io
import logging
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from phase6_worker_command_regression import load_app, register, pair, heartbeat
from phase10_postgres_integration import load_postgres_app

ROOT = Path(__file__).resolve().parents[1]


def provision(module, name):
    client = module.app.test_client()
    owner = register(client, name)
    device, headers = pair(client, name)
    account = module.create_facebook_account(owner, 'Account ' + name, '81001')
    module.bind_facebook_account_device(owner, account['account_id'], device['device_id'])
    proof = {
        'facebook_logged_in': True, 'worker_state': 'idle', 'current_job_id': '',
        'session_verification_version': 1,
        'facebook_session_fingerprint': module.facebook_session_fingerprint('81001'),
        'browser_profile_id': 'chrome-profile:' + device['device_id'],
        'session_context': 'chrome-profile:' + device['device_id'],
    }
    assert client.post('/api/agent/heartbeat', headers=headers, json=proof).status_code == 200
    return client, owner, device, headers, account, proof


def make_campaign(module, owner, account, state='queued'):
    groups = [f'https://www.facebook.com/groups/stability-{i}' for i in range(3)]
    return module.create_engine_campaign(owner, 'Stability campaign', [
        {'account_id': account['account_id'], 'groups': groups}],
        {'content': 'Test only; no publication', 'min_delay': 0, 'max_delay': 0}, state,
        module.utc_now() - timedelta(minutes=1) if state == 'scheduled' else None)


def run_local(temp):
    m = load_app(temp)
    client, owner, device, headers, account, proof = provision(m, 'stability')
    stored = m.load_devices(owner)
    expires = stored[device['device_id']]['token_expires_at']
    assert m.parse_iso(expires) > m.utc_now()
    stored[device['device_id']]['token_expires_at'] = (m.utc_now() - timedelta(seconds=1)).isoformat()
    m.save_devices(owner, stored)
    assert heartbeat(client, headers).status_code == 401
    stored[device['device_id']].pop('token_expires_at')
    m.save_devices(owner, stored)
    assert client.post('/api/agent/heartbeat', headers=headers, json=proof).status_code == 200
    grace = m.load_devices(owner)[device['device_id']]['token_expires_at']
    assert client.post('/api/agent/heartbeat', headers=headers, json=proof).status_code == 200
    assert m.load_devices(owner)[device['device_id']]['token_expires_at'] == grace
    malformed = dict(headers, **{'X-Device-ID': device['device_id'] + '/'})
    assert heartbeat(client, malformed).status_code == 401
    print('PASS token expiry/grace: expired and malformed credentials refused; grace never slides')

    campaign = make_campaign(m, owner, account)
    bad_proof = dict(proof, facebook_session_fingerprint=m.facebook_session_fingerprint('99999'))
    assert client.post('/api/agent/heartbeat', headers=headers, json=bad_proof).status_code == 200
    assert not client.get('/api/agent/job', headers=headers).get_json()['has_job']
    assert 'account' in m.get_campaign_state(owner)['message']
    assert client.post('/api/agent/heartbeat', headers=headers, json=proof).status_code == 200
    job = client.get('/api/agent/job', headers=headers).get_json()['job']
    assert job['expected_session_fingerprint'] == proof['facebook_session_fingerprint']
    assert job['execution_token_required'] and job['execution_token']
    report = {'job_id': job['job_id'], 'status': 'finished', 'processed': 1, 'success': 1, 'errors': 0}
    assert client.post('/api/agent/status', headers=headers, json=report).status_code == 409
    assert client.post('/api/agent/status', headers=headers, json=dict(report, execution_token=job['execution_token'])).status_code == 200
    stale_hb = dict(proof, worker_state='busy', current_job_id=job['job_id'], execution_token=job['execution_token'])
    assert client.post('/api/agent/heartbeat', headers=headers, json=stale_hb).status_code == 409
    task = next(t for t in m.load_engine_tasks(owner) if t['task_id'] == job['engine_task_id'])
    assert task['status'] == 'successful'
    m._update_engine_task(owner, task['task_id'], status='running')
    assert next(t for t in m.load_engine_tasks(owner) if t['task_id'] == task['task_id'])['status'] == 'successful'
    job2 = client.get('/api/agent/job', headers=headers).get_json()['job']
    retry = {'job_id': job2['job_id'], 'status': 'finished_with_errors', 'processed': 1, 'success': 0, 'errors': 1, 'execution_token': job2['execution_token']}
    assert client.post('/api/agent/status', headers=headers, json=retry).status_code == 200
    m._update_engine_task(owner, job2['engine_task_id'], next_retry_at=(m.utc_now() - timedelta(seconds=1)).isoformat())
    retry_job = client.get('/api/agent/job', headers=headers).get_json()['job']
    assert retry_job['job_id'] == job2['job_id'] and retry_job['execution_token'] != job2['execution_token']
    assert client.post('/api/agent/status', headers=headers, json=retry).status_code == 409
    print('PASS session/attempt identity: wrong Facebook account blocked; stale heartbeat/ACK cannot revive success or mutate retry')

    with client.session_transaction() as session:
        session['admin_logged_in'] = True
    owner_record = m.find_user_by_id(owner)
    m.IS_PRODUCTION = True
    m.LEGACY_ADMIN_AUTH_ENABLED = True
    m.LOG_CLEANUP_NEXT_AT = float('inf')
    with patch.object(m, 'find_user_by_id', return_value=owner_record):
        assert client.get('/admin').status_code == 403
        assert client.get('/api/admin/dashboard').status_code in {403, 404}
        assert client.post('/admin/login', headers={'Origin': 'http://localhost'}, data={'password': m.ADMIN_PASSWORD}).status_code == 302
    m.IS_PRODUCTION = False
    assert client.post('/connector/disconnect').status_code == 302
    assert heartbeat(client, headers).status_code == 401
    print('PASS admin/revoke: legacy flag cannot bypass production role; disconnect revokes bearer token')

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    m.app.logger.addHandler(handler)
    m.app.logger.error('password=hunter2 Bearer ABCsecret postgresql://alice:dbpass@db.example/test')
    assert not any(secret in stream.getvalue() for secret in ['hunter2', 'ABCsecret', 'dbpass'])
    m.app.logger.removeHandler(handler)
    print('PASS actual application logger redacts credentials')


def run_postgres(temp, url):
    parsed = urlsplit(url)
    assert parsed.hostname in {'127.0.0.1', 'localhost'} and parsed.path.startswith('/fbpostpro_phase11'), 'Isolated test DB required'
    m = load_postgres_app(Path(temp) / 'pg', url)
    client, owner, device, headers, account, proof = provision(m, 'pg_stability')
    campaign = make_campaign(m, owner, account, 'scheduled')
    ids = {t['task_id'] for t in m.load_engine_tasks(owner)}
    stored = m.load_devices(owner)
    stored[device['device_id']]['last_seen'] = ''
    m.save_devices(owner, stored)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: m.activate_due_campaigns_all(), range(12)))
    assert sum(len(result) for result in results) == 1
    assert m.get_engine_campaign(owner, campaign['campaign_id'])['lifecycle'] == 'queued'
    assert {t['task_id'] for t in m.load_engine_tasks(owner)} == ids
    logs, total = m.load_operational_logs({'campaign_id': campaign['campaign_id'], 'query': 'scheduled_campaign_waiting_worker'})
    assert total == 1
    assert not client.get('/api/agent/job', headers=headers).get_json()['has_job']
    assert client.post('/api/agent/heartbeat', headers=headers, json=proof).status_code == 200
    with ThreadPoolExecutor(max_workers=6) as executor:
        claims = list(executor.map(lambda _: m.app.test_client().get('/api/agent/job', headers=headers).get_json(), range(12)))
    assert sum(result['has_job'] for result in claims) == 1
    job = next(result['job'] for result in claims if result['has_job'])
    assert client.post('/api/agent/status', headers=headers, json={'job_id': job['job_id'], 'execution_token': job['execution_token'], 'status': 'finished', 'processed': 1, 'success': 1}).status_code == 200
    reloaded = load_postgres_app(Path(temp) / 'pg', url)
    assert {t['task_id'] for t in reloaded.load_engine_tasks(owner)} == ids
    assert not reloaded.activate_due_campaigns_all()
    assert next(t for t in reloaded.load_engine_tasks(owner) if t['task_id'] == job['engine_task_id'])['status'] == 'successful'
    print('PASS PostgreSQL concurrency/restart: 12 simultaneous scheduler ticks/claims produce one dispatch; task IDs and success persist')

    def rate_check(_):
        with m.app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.0.2.11'}):
            return m.auth_rate_allowed('stability_rate', 7, 3600)
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(rate_check, range(30)))
    assert sum(results) == 7
    with reloaded.app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.0.2.11'}):
        assert not reloaded.auth_rate_allowed('stability_rate', 7, 3600)
    with m.postgres_connect() as conn:
        conn.execute("UPDATE fbpostpro_rate_limits SET updated_at=NOW()-INTERVAL '3 days' WHERE scope='stability_rate'")
    before = len(m.load_engine_tasks(owner))
    m.cleanup_expired_logs()
    assert len(m.load_engine_tasks(owner)) == before
    with m.postgres_connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM fbpostpro_rate_limits WHERE scope='stability_rate'").fetchone()['n'] == 0
    print('PASS shared rate limiter: 30 concurrent requests allow exactly 7; restart and cleanup safe')

    original_url = m.DATABASE_URL
    m.DATABASE_URL = 'postgresql://test:never-log-this@127.0.0.1:1/unavailable'
    assert client.get('/health').status_code == 200
    assert client.get('/ready').status_code == 503
    response = client.get('/campaign-status')
    assert response.status_code == 503 and 'never-log-this' not in response.get_data(as_text=True)
    m.DATABASE_URL = original_url
    assert client.get('/ready').status_code == 200
    with patch.object(m.psycopg, 'connect', side_effect=m.psycopg.OperationalError('failed to resolve host password=do-not-log')):
        assert client.get('/ready').status_code == 503
    print('PASS database outage/DNS error: health survives, readiness/API return 503 without JSON fallback; reconnect recovers')

    # Real running scheduler thread: no website/worker request is needed to queue.
    other, other_id, _, _, other_account, _ = provision(m, 'pg_background')
    due = make_campaign(m, other_id, other_account, 'scheduled')
    m.SCHEDULER_ENABLED = True
    m.start_scheduler_thread()
    deadline = time.monotonic() + 10
    while m.get_engine_campaign(other_id, due['campaign_id'])['lifecycle'] != 'queued' and time.monotonic() < deadline:
        time.sleep(.05)
    assert m.get_engine_campaign(other_id, due['campaign_id'])['lifecycle'] == 'queued'
    assert m.start_scheduler_thread() is m.SCHEDULER_THREAD
    m.SCHEDULER_STOP_EVENT.set()
    m.SCHEDULER_THREAD.join(3)
    m.SCHEDULER_ENABLED = False
    print('PASS live background scheduler: due campaign queues without worker or web poll; start is idempotent')

    _, process_owner, _, _, process_account, _ = provision(m, 'pg_processes')
    process_campaign = make_campaign(m, process_owner, process_account, 'scheduled')
    process_env = os.environ.copy()
    process_env.update(APP_ENV='development', DATABASE_URL=url, ENABLE_SCHEDULER='false', DATA_ROOT=str(Path(temp)/'processes'))
    code = "import app; print('activated=' + str(len(app.activate_due_campaigns_all())))"
    processes = [subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=process_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    outputs = [p.communicate(timeout=30) for p in processes]
    assert all(p.returncode == 0 for p in processes), outputs
    assert sum('activated=1' in out for out, _ in outputs) == 1, outputs
    assert len(m.load_engine_tasks(process_owner, process_campaign['campaign_id'])) == 3
    print('PASS PostgreSQL across 4 independent processes: startup migrations and scheduler promotion do not duplicate dispatch')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='fbpp-phase11-') as temp:
        run_local(temp)
        url = os.environ.get('PHASE11_POSTGRES_TEST_URL', '')
        if url:
            run_postgres(temp, url)
        else:
            print('NOT TESTED PostgreSQL Phase11: set PHASE11_POSTGRES_TEST_URL to isolated localhost database')
