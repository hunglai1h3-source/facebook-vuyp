"""Read-only constraint diagnostics. Contains IDs/counts only; never repairs customer data."""
import json
import os
import sys
try:
    from .postgres_tools import database_parameters
except ImportError:
    from postgres_tools import database_parameters


def report(database_url):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**database_parameters(database_url), row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='10s'")
        present = conn.execute("SELECT to_regclass('public.fbpostpro_migration_issues') AS name").fetchone()['name']
        issues = conn.execute('SELECT issue_key, object_name, affected_count, sample_data, detected_at, last_seen_at, resolved_at FROM fbpostpro_migration_issues ORDER BY issue_key').fetchall() if present else []
        indexes = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname IN ('idx_fbpostpro_one_active_account_task','idx_fbpostpro_one_account_per_device')").fetchall()
        missing = sorted({'idx_fbpostpro_one_active_account_task', 'idx_fbpostpro_one_account_per_device'} - {r['indexname'] for r in indexes})
        return {'issues': issues, 'missing_indexes': missing, 'ready': not missing and not any(not r['resolved_at'] for r in issues)}


def main():
    try:
        result = report(os.environ.get('DATABASE_URL', ''))
        print(json.dumps(result, indent=2, default=str))
        return 0 if result['ready'] else 2
    except Exception as exc:
        print(f'migration_report=unavailable error_type={type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
