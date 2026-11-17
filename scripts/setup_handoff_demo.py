"""Create and populate two new, uniquely named databases for a restored project.

Requires prepared local data and installed project dependencies. Refuses an existing
.env or setup log, does not modify existing databases, and never deletes volumes.
"""
import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

from scripts.district_candidate import inspect as inspect_districts
from scripts.handoff_bundle import REQUIRED
from scripts.load_dwh import read_sources
from scripts.prepare_spatial_research import ROOT, inspect as inspect_points

IMAGE = 'postgis/postgis:17-3.6@sha256:1ac10b2551ba84ae05db8e7da477afcd0a3fede99bcb7705da2ee157390cb095'
COMPOSE = ROOT / 'compose.handoff.yaml'
ENV_FILE = ROOT / '.env'
LOG = ROOT / '.handoff-setup.log'


def validate_compose(path):
    actual = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    expected = {'services': {
        'db': {'image': 'postgres:17.11',
               'environment': {'POSTGRES_DB': 'logistics_dwh', 'POSTGRES_USER': 'dwh_user',
                               'POSTGRES_PASSWORD': '${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}'},
               'ports': ['127.0.0.1:${DWH_CORE_PORT:?Set DWH_CORE_PORT}:5432'],
               'volumes': ['postgres_data:/var/lib/postgresql/data', './sql:/sql:ro']},
        'spatial-db': {'profiles': ['spatial-research'], 'image': IMAGE,
                       'environment': {'POSTGRES_DB': 'spatial_research', 'POSTGRES_USER': 'dwh_user',
                                       'POSTGRES_PASSWORD': '${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}'},
                       'ports': ['127.0.0.1:${DWH_SPATIAL_PORT:?Set DWH_SPATIAL_PORT}:5432'],
                       'volumes': ['spatial_research_data:/var/lib/postgresql/data']}},
        'volumes': {'postgres_data': None, 'spatial_research_data': None}}
    if actual != expected:
        raise ValueError('Unexpected handoff Compose configuration; refusing to create databases')


def validate_ports(core, spatial):
    if not (1024 <= core <= 65535 and 1024 <= spatial <= 65535) or core == spatial:
        raise ValueError('Choose two distinct TCP ports between 1024 and 65535')
    sockets = []
    try:
        for port in (core, spatial):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.bind(('127.0.0.1', port))
    finally:
        for sock in sockets:
            sock.close()


def project_is_free(project):
    if not re.fullmatch(r'dwhhandoff[0-9a-f]{16}', project):
        raise ValueError('Invalid isolated project identifier')
    for kind in ('container', 'volume', 'network'):
        args = ['docker', kind, 'ls', '-q', '--filter', f'label=com.docker.compose.project={project}']
        if kind == 'container':
            args.insert(3, '-a')
        result = subprocess.run(args, capture_output=True, check=True, text=True)
        if result.stdout.strip():
            raise RuntimeError('Project identifier already exists')
    for kind, name in (('volume', f'{project}_postgres_data'), ('volume', f'{project}_spatial_research_data'),
                       ('network', f'{project}_default'), ('container', f'{project}-db-1'),
                       ('container', f'{project}-spatial-db-1')):
        if subprocess.run(['docker', kind, 'inspect', name], capture_output=True).returncode == 0:
            raise RuntimeError('Project resource name already exists')


def setup(core_port, spatial_port):
    if ENV_FILE.exists() or LOG.exists():
        raise FileExistsError('Existing .env or setup log found; use a fresh restored project directory')
    validate_compose(COMPOSE)
    validate_ports(core_port, spatial_port)
    missing = sorted(name for name in REQUIRED if not (ROOT / name).is_file())
    if missing:
        raise FileNotFoundError('Missing restored data: ' + ', '.join(missing))
    read_sources(ROOT)
    inspect_districts()
    inspect_points()
    subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, check=True)
    project = 'dwhhandoff' + secrets.token_hex(8)
    project_is_free(project)
    password = secrets.token_urlsafe(32)
    settings = {'COMPOSE_PROJECT_NAME': project, 'COMPOSE_FILE': 'compose.handoff.yaml',
                'POSTGRES_PASSWORD': password, 'DWH_CORE_PORT': str(core_port), 'DWH_SPATIAL_PORT': str(spatial_port)}
    with ENV_FILE.open('x', encoding='utf-8', newline='\n') as handle:
        for key, value in settings.items():
            handle.write(f'{key}={value}\n')
    env = os.environ.copy()
    for key in ('COMPOSE_PROJECT_NAME', 'COMPOSE_FILE', 'COMPOSE_PROFILES', 'COMPOSE_ENV_FILES',
                'POSTGRES_PASSWORD', 'DWH_CORE_PORT', 'DWH_SPATIAL_PORT'):
        env.pop(key, None)
    env.update(settings)
    env['PYTHONUTF8'] = '1'; env['PYTHONDONTWRITEBYTECODE'] = '1'; env['PYTHONPATH'] = str(ROOT)
    compose = ['docker', 'compose', '-f', 'compose.handoff.yaml', '--profile', 'spatial-research']
    python = [sys.executable, '-B']
    LOG.open('x', encoding='utf-8').close()

    def run(label, command):
        print(f'Running: {label}', flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
        text = (result.stdout + b'\n' + result.stderr).decode('utf-8', errors='replace').replace(password, '[redacted]')
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(f'\n[{label}]\n{text}\n')
        if result.returncode:
            raise RuntimeError(f'{label} failed; inspect the local .handoff-setup.log')
        print(f'OK: {label}', flush=True)
        return result.stdout.decode('utf-8', errors='replace').strip()

    try:
        run('start new databases', [*compose, 'up', '-d', 'db', 'spatial-db'])
        for service, database in (('db', 'logistics_dwh'), ('spatial-db', 'spatial_research')):
            deadline = time.monotonic() + 120
            while True:
                ready = subprocess.run([*compose, 'exec', '-T', service, 'pg_isready', '-h', '127.0.0.1',
                                        '-U', 'dwh_user', '-d', database], cwd=ROOT, env=env, capture_output=True)
                if ready.returncode == 0:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f'{service} did not become ready')
                time.sleep(1)
            result = run(f'check empty {database}', [*compose, 'exec', '-T', service, 'psql', '-X', '-A', '-t',
                         '-v', 'ON_ERROR_STOP=1', '-U', 'dwh_user', '-d', database, '-c',
                         "SELECT count(*) FROM pg_namespace WHERE nspname IN ('core','control','mart','scenario','spatial_research');"])
            if result != '0':
                raise RuntimeError(f'{database} contains project schemas; refusing to continue')
        def sql(label, filename):
            run(label, [*compose, 'exec', '-T', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'dwh_user',
                        '-d', 'logistics_dwh', '-f', f'/sql/{filename}'])
        def module(label, name):
            return run(label, [*python, '-m', name])
        sql('create schema', '01_create_schema.sql')
        module('load core data', 'scripts.load_dwh')
        sql('create alert exposure view', '02_delivery_alert_exposure.sql')
        sql('create scenario states', '07_multifactor_states.sql')
        sql('create delivery states', '08_delivery_start_states.sql')
        sql('create weather context', '09_delivery_weather_context.sql')
        module('load outage scenarios', 'scripts.load_outage_scenarios')
        module('load density scenarios', 'scripts.load_density_scenarios')
        module('load spatial points', 'scripts.load_spatial_research')
        module('load district context', 'scripts.load_district_context')
        module('activate versioned context', 'scripts.setup_versioned_context')
        verification = json.loads(module('verify demonstration', 'scripts.verify_demo'))
        if verification.get('status') != 'verified':
            raise RuntimeError('Demonstration verification did not pass')
    except BaseException:
        print(f'Setup incomplete; new project {project} and local .env/log are retained for inspection.', file=sys.stderr, flush=True)
        raise
    return {'status': 'ready', 'compose_project': project, 'core_port': core_port, 'spatial_port': spatial_port,
            'password_location': '.env', 'demonstration_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-port', type=int, default=55434)
    parser.add_argument('--spatial-port', type=int, default=55435)
    args = parser.parse_args()
    print(json.dumps(setup(args.core_port, args.spatial_port), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
