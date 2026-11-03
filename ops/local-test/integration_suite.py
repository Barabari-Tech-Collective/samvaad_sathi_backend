"""Run destructive-schema integration fixtures only in a dedicated local test DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import psycopg2
from sqlalchemy.engine import URL


def main():
    root=Path(__file__).resolve().parents[2]
    state=root.parent/'samvaad-local-test'
    config=json.loads((state/'product.json').read_text())
    if (config.get('POSTGRES_HOST'),config.get('POSTGRES_PORT'),config.get('POSTGRES_DB')) != ('127.0.0.1','15432','samvaad_local'):
        raise RuntimeError('Dedicated local database required')
    connection=psycopg2.connect(host='127.0.0.1',port=15432,dbname='postgres',user=config['POSTGRES_USERNAME'],password=config['POSTGRES_PASSWORD'])
    connection.autocommit=True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname='ss_count_tests'")
            if cursor.fetchone() is None:
                cursor.execute('CREATE DATABASE ss_count_tests')
    finally:
        connection.close()
    url=URL.create('postgresql+asyncpg',username=config['POSTGRES_USERNAME'],password=config['POSTGRES_PASSWORD'],host='127.0.0.1',port=15432,database='ss_count_tests').render_as_string(hide_password=False)
    env=os.environ.copy()
    env['FIXED_ROUND_TEST_DATABASE_URL']=url
    env['SCHEMA_TEST_DATABASE_URL']=url
    # Pytest treats each positional path as an additional collection target.
    # Supplying a focused test after the directory used to run the entire
    # end-to-end suite as well, which defeats diagnosis and makes unrelated
    # schema-rebuilding fixtures contend with the selected test.  With no
    # arguments preserve the complete-suite default; otherwise let callers
    # select exactly the intended test path or expression.
    selection = sys.argv[1:] or ['backend/backend/tests/end_to_end_tests']
    return subprocess.call(
        [str(root/'.venv/bin/python'), '-m', 'pytest', *selection, '-q'],
        cwd=root,
        env=env,
    )

if __name__ == '__main__':
    raise SystemExit(main())
