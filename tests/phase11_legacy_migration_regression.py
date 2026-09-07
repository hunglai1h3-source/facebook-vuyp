"""Builds a known duplicate LEGACY fixture in an explicitly isolated empty database."""
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from phase10_postgres_integration import load_postgres_app
from scripts.deploy_preflight import check_deploy, DeployBlocked
from scripts.migration_report import report


def run(source_url, legacy_url):
    for value in (source_url, legacy_url):
        u = urlsplit(value)
        assert u.hostname in {'localhost', '127.0.0.1'} and u.path.startswith('/fbpostpro_phase11')
    assert source_url != legacy_url
    with psycopg.connect(legacy_url) as target, psycopg.connect(source_url) as source:
        assert not target.execute("SELECT 1 FROM pg_tables WHERE schemaname='public'").fetchone(), 'Empty isolated database required'
        for table in ('fbpostpro_accounts', 'fbpostpro_campaign_tasks'):
            columns = source.execute('''SELECT a.attname, format_type(a.atttypid,a.atttypmod), pg_get_expr(d.adbin,d.adrelid)
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''', (table,)).fetchall()
            defs = [sql.SQL('{} {}{}').format(sql.Identifier(name), sql.SQL(kind), sql.SQL(' DEFAULT ' + default) if default else sql.SQL('')) for name, kind, default in columns]
            target.execute(sql.SQL('CREATE TABLE {} ({})').format(sql.Identifier(table), sql.SQL(',').join(defs)))
        target.execute('ALTER TABLE fbpostpro_accounts ADD PRIMARY KEY(account_id)')
        target.execute('ALTER TABLE fbpostpro_campaign_tasks ADD PRIMARY KEY(task_id)')
        target.execute('''INSERT INTO fbpostpro_accounts(account_id,customer_id,display_name,device_id)
            VALUES ('legacy_account1','legacy_owner','Account 1','ext_legacy'),('legacy_account2','legacy_owner','Account 2','ext_legacy')''')
        target.execute('''INSERT INTO fbpostpro_campaign_tasks(task_id,campaign_id,customer_id,account_id,device_id,group_url,status,idempotency_key)
            VALUES ('legacy_task1','legacy_campaign','legacy_owner','legacy_account1','ext_legacy','https://www.facebook.com/groups/one','running','one'),
                   ('legacy_task2','legacy_campaign','legacy_owner','legacy_account1','ext_legacy','https://www.facebook.com/groups/two','running','two')''')
    try:
        check_deploy(legacy_url)
        raise AssertionError('Old populated schema cannot deploy without backup')
    except DeployBlocked:
        pass
    with tempfile.TemporaryDirectory(prefix='fbpp-legacy-fixture-') as temp:
        m = load_postgres_app(Path(temp), legacy_url)
        result = report(legacy_url)
        assert not result['ready'] and len(result['missing_indexes']) == 2
        assert len([i for i in result['issues'] if not i['resolved_at']]) == 2
        assert m.app.test_client().get('/ready').status_code == 503
        with m.postgres_connect() as conn:
            assert conn.execute('SELECT COUNT(*) AS n FROM fbpostpro_campaign_tasks').fetchone()['n'] == 2
            assert not conn.execute("SELECT 1 FROM fbpostpro_schema_migrations WHERE migration_id='phase11_remaining_risk_remediation_v1'").fetchone()
        try:
            check_deploy(legacy_url)
            raise AssertionError('Unresolved duplicates must block startup')
        except DeployBlocked:
            pass
        m.PERSISTENCE_TABLES_READY = False
        m.init_persistence_tables()
        assert len(report(legacy_url)['issues']) == 2
        print('PASS legacy duplicates: persistent reports, stable rows, no success marker, readiness 503 and deploy blocked')
        # Explicit simulated operator decision on fixture rows; no automatic dedupe code.
        with m.postgres_connect() as conn:
            conn.execute("UPDATE fbpostpro_accounts SET device_id='' WHERE account_id='legacy_account2'")
            conn.execute("UPDATE fbpostpro_campaign_tasks SET status='failed',last_error='Operator-reviewed isolated fixture' WHERE task_id='legacy_task2'")
        m.PERSISTENCE_TABLES_READY = False
        m.init_persistence_tables()
        assert report(legacy_url)['ready']
        assert m.app.test_client().get('/ready').status_code == 200
        assert check_deploy(legacy_url)['status'] == 'current'
        print('PASS reviewed legacy repair: additive indexes created, issues resolved, rerun idempotent without deleting rows')


if __name__ == '__main__':
    run(os.environ['PHASE11_POSTGRES_TEST_URL'], os.environ['PHASE11_POSTGRES_LEGACY_URL'])
