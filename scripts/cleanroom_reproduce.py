"""Rebuild the prototype in disposable Compose databases from a Git snapshot and supplied inputs."""
import argparse
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime,timezone
from pathlib import Path

import yaml

from scripts.prepare_spatial_research import ROOT,digest

INPUTS=(
 'data/processed/alerts.csv','data/processed/kyiv_weather.csv','data/synthetic/deliveries.csv',
 'data/raw/geography/geoBoundaries-UKR-ADM2.geojson','data/raw/geography/kyiv_districts_candidate_20260913.geojson',
 'data/raw/geography/boundary_reference_20261110/sportplaygrounds.json',
 'data/raw/geography/boundary_reference_20261110/manifest.json',
)
PROJECT_RE=re.compile(r'dwhverify[0-9a-f]{16}\Z')


def strip_ports(compose,service,volume):
    """Reject unexpected services or volumes before preparing isolated Compose files."""
    if set(compose)!={'services','volumes'}:raise ValueError('Unexpected Compose configuration')
    if set(compose.get('services',{}))!={service} or set(compose.get('volumes',{}))!={volume}:
        raise ValueError('Unexpected Compose service or volume configuration')
    if compose['volumes'][volume] not in (None,{}):raise ValueError('External or configured volume rejected')
    item=compose['services'][service]
    allowed_keys={'image','environment','ports','volumes'} | ({'profiles'} if service=='spatial-db' else set())
    if set(item)!=allowed_keys:raise ValueError('Unexpected Compose service options')
    image=('postgres:17.11' if service=='db' else 'postgis/postgis:17-3.6@sha256:1ac10b2551ba84ae05db8e7da477afcd0a3fede99bcb7705da2ee157390cb095')
    database='logistics_dwh' if service=='db' else 'spatial_research'
    expected_env={'POSTGRES_DB':database,'POSTGRES_USER':'dwh_user','POSTGRES_PASSWORD':'${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}'}
    if item['image']!=image or item['environment']!=expected_env:raise ValueError('Unexpected database image or environment')
    if service=='spatial-db' and item['profiles']!=['spatial-research']:raise ValueError('Unexpected profile')
    expected=f'{volume}:/var/lib/postgresql/data'
    mounts=item.get('volumes',[])
    allowed={expected,'./sql:/sql:ro'} if service=='db' else {expected}
    if set(mounts)!=allowed:raise ValueError('Unexpected Compose volume mount')
    item.pop('ports',None)
    return compose


def safe_resources(project,volumes,networks):
    if not PROJECT_RE.fullmatch(project):raise ValueError('Invalid isolated project identifier')
    if not set(volumes).issubset({f'{project}_postgres_data',f'{project}_spatial_research_data'}):
        raise ValueError('Unexpected volume attached to isolated project')
    if not set(networks).issubset({f'{project}_default'}):raise ValueError('Unexpected network attached to isolated project')


def docker_list(kind,project):
    args={'volumes':['volume','ls','-q'], 'networks':['network','ls','--format','{{.Name}}'],
          'containers':['container','ls','-aq']}[kind]
    p=subprocess.run(['docker',*args,'--filter',f'label=com.docker.compose.project={project}'],capture_output=True,check=True,text=True)
    return [x for x in p.stdout.splitlines() if x]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-dir',type=Path,required=True,help='new directory for tracked snapshot and temporary copies')
    parser.add_argument('--report',type=Path,required=True,help='new JSON report file')
    args=parser.parse_args();scratch=args.scratch_dir.resolve();report_path=args.report.resolve()
    if scratch.exists() or report_path.exists():raise FileExistsError('Scratch directory or report already exists')
    files={name:ROOT/name for name in INPUTS}
    missing=[name for name,path in files.items() if not path.is_file()]
    if missing:raise FileNotFoundError('Missing inputs: '+', '.join(missing))
    hashes={name:digest(path) for name,path in files.items()}
    git=['git','-c',f'safe.directory={ROOT.as_posix()}']
    revision=subprocess.run([*git,'rev-parse','HEAD'],cwd=ROOT,capture_output=True,check=True,text=True).stdout.strip()
    archive=subprocess.run([*git,'archive','--format=tar',revision],cwd=ROOT,capture_output=True,check=True).stdout
    scratch.mkdir(parents=True,exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as tar:tar.extractall(scratch,filter='data')
    for name,path in files.items():
        target=scratch/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        if digest(target)!=hashes[name]:raise RuntimeError('Input copy differs from source')
    for name,service,volume in (('compose.yaml','db','postgres_data'),('compose.spatial.yaml','spatial-db','spatial_research_data')):
        path=scratch/name; obj=strip_ports(yaml.safe_load(path.read_text(encoding='utf-8')),service,volume)
        path.write_text(yaml.safe_dump(obj,sort_keys=False,allow_unicode=True),encoding='utf-8')
    project='dwhverify'+secrets.token_hex(8)
    safe_resources(project,[],[])
    if any(docker_list(k,project) for k in ('containers','volumes','networks')):raise RuntimeError('Isolated project already exists')
    all_volumes=subprocess.run(['docker','volume','ls','-q'],capture_output=True,check=True,text=True).stdout.splitlines()
    if set(all_volumes)&{f'{project}_postgres_data',f'{project}_spatial_research_data'}:raise RuntimeError('Isolated volume name collision')
    all_networks=subprocess.run(['docker','network','ls','--format','{{.Name}}'],capture_output=True,check=True,text=True).stdout.splitlines()
    if f'{project}_default' in all_networks:raise RuntimeError('Isolated network name collision')
    env=os.environ.copy();env['COMPOSE_PROJECT_NAME']=project;env['POSTGRES_PASSWORD']=secrets.token_urlsafe(32)
    env['PYTHONPATH']=str(scratch);env['PYTHONUTF8']='1';env['PYTHONDONTWRITEBYTECODE']='1'
    env.pop('COMPOSE_FILE',None);env.pop('COMPOSE_PROFILES',None)
    compose=['docker','compose','-f','compose.yaml','-f','compose.spatial.yaml','--profile','spatial-research']
    python=[sys.executable,'-B'];steps=[];started=datetime.now(timezone.utc).isoformat();cleanup='not_started'
    log_path=scratch/'cleanroom.log'
    def run(label,command,needle=None,input_bytes=None):
        print(f'Running: {label}',flush=True)
        p=subprocess.run(command,cwd=scratch,env=env,input=input_bytes,capture_output=True)
        out=p.stdout.decode('utf-8',errors='replace');err=p.stderr.decode('utf-8',errors='replace')
        with log_path.open('a',encoding='utf-8') as log:log.write(f'\n[{label}]\n{out}\n{err}\n')
        if p.returncode or (needle and needle not in out):
            raise RuntimeError(f'{label} failed (exit {p.returncode}); inspect cleanroom.log')
        steps.append(label);print(f'OK: {label}',flush=True)
    def module(label,name,needle=None):run(label,[*python,'-m',name],needle)
    def sql(label,name):
        run(label,['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh','-f',f'/sql/{name}'])
    def fixture(label,file,spatial=False):
        cmd=([*compose,'exec','-T','spatial-db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','spatial_research'] if spatial
             else ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh'])
        run(label,cmd,'ROLLBACK',input_bytes=(scratch/'tests'/file).read_bytes())
    try:
        run('start isolated databases',[*compose,'up','-d','--pull','never','db','spatial-db'])
        for service,db in (('db','logistics_dwh'),('spatial-db','spatial_research')):
            deadline=time.monotonic()+120
            while True:
                # The image briefly starts a socket-only initialization server; wait for the final TCP server.
                p=subprocess.run([*compose,'exec','-T',service,'pg_isready','-h','127.0.0.1','-U','dwh_user','-d',db],cwd=scratch,env=env,capture_output=True)
                if p.returncode==0:break
                if time.monotonic()>deadline:raise TimeoutError(f'{service} did not become ready')
                time.sleep(1)
        sql('create core schema','01_create_schema.sql')
        run('validate prepared inputs',[*python,'-m','scripts.load_dwh','--validate-only'])
        module('load prepared inputs','scripts.load_dwh','LOADED')
        module('repeat initial load without changes','scripts.load_dwh','UNCHANGED')
        sql('create alert exposure view','02_delivery_alert_exposure.sql')
        sql('create multifactor states','07_multifactor_states.sql')
        sql('create delivery states','08_delivery_start_states.sql')
        sql('create weather context','09_delivery_weather_context.sql')
        module('generate outage scenarios','scripts.generate_outage_scenarios')
        module('load outage scenarios','scripts.load_outage_scenarios')
        module('generate density scenarios','scripts.generate_density_scenarios')
        module('load density scenarios','scripts.load_density_scenarios')
        module('verify alert exposure','scripts.verify_alert_exposure','differences=0')
        module('verify weather context','scripts.verify_delivery_weather','"weather_unknown": 0')
        module('verify density scenarios','scripts.verify_density_scenarios','verified')
        module('verify all delivery states','scripts.verify_density_delivery_states','500 deliveries verified')
        fixture('test alert exposure boundaries','test_delivery_alert_exposure.sql')
        fixture('test regional state boundaries','test_multifactor_states.sql')
        fixture('test delivery state boundaries','test_delivery_start_states.sql')
        fixture('test weather boundaries','test_delivery_weather_context.sql')
        module('generate synthetic spatial points','scripts.prepare_spatial_research')
        module('load spatial prototype','scripts.load_spatial_research')
        module('verify spatial prototype','scripts.verify_spatial_research','"matched_synthetic_deliveries": 500')
        fixture('test spatial boundary','test_spatial_research.sql',spatial=True)
        module('load district context','scripts.load_district_context')
        module('verify district context','scripts.verify_district_context','"matched": 255')
        fixture('test district boundary ambiguity','test_district_context.sql',spatial=True)
        module('create versioned context','scripts.setup_versioned_context')
        module('verify versioned context','scripts.verify_versioned_context','"source_fresh_at_check": true')
        module('test version transaction rollback','scripts.test_versioned_context_transactions','previous version preserved')
        module('verify idempotent version refresh','scripts.refresh_versioned_context','"status": "unchanged"')
        module('audit district reference points','scripts.audit_district_boundaries','"unique_points_checked": 921')
        pytest_tmp=scratch/'pytest_tmp'
        if pytest_tmp.exists():raise FileExistsError('Refusing to replace existing pytest temporary directory')
        run('run project unit tests',[*python,'-m','pytest','-p','no:cacheprovider','-q','--basetemp',str(pytest_tmp)],'passed')
    finally:
        try:
            volumes=docker_list('volumes',project);networks=docker_list('networks',project);safe_resources(project,volumes,networks)
            containers=docker_list('containers',project)
            for container in containers:
                inspect=subprocess.run(['docker','inspect',container],capture_output=True,check=True,text=True)
                info=json.loads(inspect.stdout)[0]
                labels=info['Config']['Labels'] or {}
                service=labels.get('com.docker.compose.service')
                if service not in ('db','spatial-db') or info['Name'].lstrip('/')!=f'{project}-{service}-1':
                    raise ValueError('Unexpected container attached to isolated project')
            run('remove only isolated resources',[*compose,'down','--volumes'])
            if any(docker_list(k,project) for k in ('containers','volumes','networks')):raise RuntimeError('Isolated resources remain')
            cleanup='complete'
        except Exception as exc:
            cleanup=f'failed: {exc}'
            print(f'Cleanup did not finish; isolated project: {project}; {exc}',file=sys.stderr,flush=True)
    if cleanup!='complete':raise RuntimeError('Isolated resources need attention; see log')
    if any(digest(path)!=hashes[name] for name,path in files.items()):raise RuntimeError('Original input changed during test')
    data={'status':'complete','repository_commit':revision,'started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),
          'scope':'tracked Git snapshot, prepared local inputs, new isolated PostgreSQL and PostGIS volumes, existing host Python environment',
          'input_sha256':hashes,'steps_completed':steps,'isolated_resources_cleanup':cleanup,
          'limitations':'not a clean Python/OS installation; raw inputs and full large benchmarks not reconstructed; district boundaries remain unconfirmed'}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    with report_path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
    print(f'Cleanroom verification completed; scratch directory retained at {scratch}',flush=True)


if __name__=='__main__':main()
