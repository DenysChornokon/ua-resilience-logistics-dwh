"""Verify and compare state joins for 10k/100k synthetic deliveries."""
import argparse
import bisect
import csv
import hashlib
import io
import json
import platform
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path
from statistics import median

from scripts.generate_density_scenarios import ROOT, START, END
from scripts.load_benchmark_datasets import DATA as DELIVERY_DATA, inspect_sources
from scripts.load_density_scenarios import DATA as DENSITY_DATA, inspect as inspect_density
from scripts.run_delivery_cache_benchmark import parse_sections, sql_for_case, statements
from scripts.run_delivery_density_benchmark import SNAPSHOT
from scripts.run_postgres_benchmark import plan_metrics, query
from scripts.verify_density_delivery_states import verify_all
from scripts.verify_multifactor_states import parse, reference

SIZES = (10000, 100000)
PROFILES = ('sparse', 'uniform', 'clustered')
SEED = 42
COMMAND = ['docker','compose','exec','-T','db','psql','-X','-q','-A','-t',
           '-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh']


def scaled_statements(scenario, size):
    if size not in SIZES:
        raise ValueError('Unsupported size')
    original, optimized = statements(scenario)
    optimized = optimized.replace('core.fact_delivery d', f'benchmark.deliveries_{size} d')
    baseline = optimized.replace('pg_temp.delivery_state_cache r', 'scenario.region_state r').replace(
        "tstzrange(r.started_at,r.finished_at,'[)') @> d.started_at",
        'd.started_at>=r.started_at AND d.started_at<r.finished_at')
    return original, baseline, optimized


def case_sql(scenario, size, first, compare):
    original, baseline, optimized = scaled_statements(scenario, size)
    old_optimized = statements(scenario)[1]
    sql = sql_for_case(scenario, first).replace(original, baseline).replace(old_optimized, optimized)
    sql = sql.replace('CREATE TEMP TABLE delivery_state_cache AS SELECT',
                      'CREATE TEMP TABLE delivery_state_cache ON COMMIT DROP AS SELECT')
    sql = sql.replace('\\set ON_ERROR_STOP on\n', '\\set ON_ERROR_STOP on\nBEGIN ISOLATION LEVEL REPEATABLE READ;\n', 1)
    if not compare:
        prefix, rest = sql.split('\\echo __CHECK__\n', 1)
        _, suffix = rest.split('\\echo __', 1)
        sql = prefix + "\\echo __CHECK__\nSELECT 'null';\n\\echo __" + suffix
    return sql + 'ROLLBACK;\n'


def snapshot():
    other = query("""SELECT json_build_object('manifest',(SELECT json_agg(row_to_json(m) ORDER BY rows_expected) FROM benchmark.dataset_manifest m WHERE rows_expected IN (10000,100000)),
      'datasets',(SELECT json_agg(x ORDER BY size) FROM (
        SELECT 10000 AS size,count(*) AS rows,md5(string_agg(row_to_json(d)::text,'' ORDER BY delivery_id)) AS digest FROM benchmark.deliveries_10000 d
        UNION ALL SELECT 100000,count(*),md5(string_agg(row_to_json(d)::text,'' ORDER BY delivery_id)) FROM benchmark.deliveries_100000 d) x));""")
    return {'core':json.loads(query(SNAPSHOT)), 'benchmark':json.loads(other)}


def verify_dataset(size, path):
    sql = f'COPY (SELECT * FROM benchmark.deliveries_{size} ORDER BY delivery_id) TO STDOUT WITH (FORMAT CSV, HEADER true);'
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(COMMAND + ['-c', sql], cwd=ROOT, stdout=subprocess.PIPE, stderr=err)
        try:
            actual = csv.DictReader(io.TextIOWrapper(proc.stdout, encoding='utf-8', newline=''))
            with path.open(encoding='utf-8', newline='') as f:
                source = csv.DictReader(f)
                count = 0
                for left, right in zip_longest(source, actual):
                    count += 1
                    if left is None or right is None:
                        raise AssertionError('Dataset row count differs')
                    for key in ('delivery_id','region_id','status'):
                        if left[key] != right[key]: raise AssertionError(f'Dataset differs at {count}: {key}')
                    for key in ('started_at','finished_at'):
                        if parse(left[key]) != parse(right[key]): raise AssertionError(f'Dataset differs at {count}: {key}')
                    if Decimal(left['distance_km']) != Decimal(right['distance_km']):
                        raise AssertionError(f'Dataset distance differs at {count}')
            if proc.wait() != 0:
                err.seek(0); raise RuntimeError(err.read().decode('utf-8', errors='replace'))
            if count != size: raise AssertionError(f'Expected {size} rows, got {count}')
            print(f'{size}: database rows match source file', flush=True)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()


def verify_cached_python(scenario, size, path, density):
    _, _, optimized = scaled_statements(scenario, size)
    events = density[(scenario.split('_')[1], SEED)][3]
    with (ROOT/'data/processed/alerts.csv').open(encoding='utf-8-sig', newline='') as f:
        alerts = [(parse(r['started_at']), parse(r['finished_at'])) for r in csv.DictReader(f)
                  if r['region_id']=='UA80000000000093317' and r['event_type']=='Повітряна тривога']
    expected = reference(START, END, alerts, [(parse(r['started_at']),parse(r['finished_at'])) for r in events])
    starts = [x[0] for x in expected]
    sql = f"""BEGIN ISOLATION LEVEL REPEATABLE READ;
CREATE TEMP TABLE delivery_state_cache ON COMMIT DROP AS SELECT * FROM scenario.region_state WHERE scenario_id='{scenario}';
CREATE INDEX delivery_state_cache_range_idx ON delivery_state_cache USING gist (tstzrange(started_at,finished_at,'[)'));
ANALYZE delivery_state_cache;
COPY ({optimized} ORDER BY d.delivery_id) TO STDOUT WITH (FORMAT CSV, HEADER true);
ROLLBACK;"""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(COMMAND, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        try:
            proc.stdin.write(sql.encode('utf-8')); proc.stdin.close()
            actual = csv.DictReader(io.TextIOWrapper(proc.stdout, encoding='utf-8', newline=''))
            with path.open(encoding='utf-8', newline='') as f:
                count = 0
                for source, row in zip_longest(csv.DictReader(f), actual):
                    count += 1
                    if source is None or row is None: raise AssertionError('Missing or extra output row')
                    moment = parse(source['started_at']); i = bisect.bisect_right(starts, moment)-1
                    if i < 0: raise AssertionError('No reference state')
                    left,right,alert,outage = expected[i]
                    if not (left <= moment < right): raise AssertionError('Reference state does not contain delivery')
                    actual_row = (row['scenario_id'],row['delivery_id'],row['region_id'],parse(row['delivery_started_at']),
                                  parse(row['state_started_at']),parse(row['state_finished_at']),row['state_known'],row['alert_active'],row['outage_active'])
                    expected_row = (scenario,source['delivery_id'],source['region_id'],moment,left,right,'t', 't' if alert else 'f','t' if outage else 'f')
                    if actual_row != expected_row: raise AssertionError(f'Python mismatch at {scenario}, row {count}')
            if proc.wait() != 0:
                err.seek(0); raise RuntimeError(err.read().decode('utf-8', errors='replace'))
            if count != size: raise AssertionError(f'Expected {size}, got {count}')
            print(f'{scenario} {size}: all results match Python reference', flush=True)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()


def verify_equality(scenario, size):
    sql = case_sql(scenario, size, 'baseline', True)
    prefix, _ = sql.split('\\echo __BASELINE__\n', 1)
    sql = prefix + '\\echo __END__\nROLLBACK;\n'
    proc = subprocess.run(COMMAND, cwd=ROOT, input=sql.encode('utf-8'), capture_output=True, check=True)
    output = proc.stdout.decode('utf-8')
    body = output.split('__CHECK__\n', 1)[1].split('__END__', 1)[0]
    result = json.loads(body)
    if result != {'baseline_rows':size, 'cached_rows':size, 'differences':0}:
        raise AssertionError(f'Full baseline and cached results differ: {result}')
    print(f'{scenario} {size}: baseline equals cache on all fields', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1: parser.error('repetitions must be positive')
    if args.output.exists(): raise FileExistsError('Output directory exists')
    seed, files = inspect_sources(DELIVERY_DATA)
    density = inspect_density(DENSITY_DATA)
    before = snapshot()
    manifests = {m['rows_expected']:m for m in before['benchmark']['manifest']}
    for size in SIZES:
        if manifests[size]['sha256'] != files[size][1] or manifests[size]['seed'] != seed:
            raise AssertionError('Benchmark manifest differs from source')
        verify_dataset(size, files[size][0])
    verified = verify_all()
    for size in SIZES:
        for profile in PROFILES:
            scenario = f'density_{profile}_s{SEED}_v1'
            verify_cached_python(scenario,size,files[size][0],density)
            verify_equality(scenario,size)
    if snapshot() != before: raise RuntimeError('Inputs changed during verification')
    args.output.mkdir(parents=True, exist_ok=False); (args.output/'plans').mkdir()
    meta = {'status':'running','started_at_utc':datetime.now(timezone.utc).isoformat(),
            'sizes':SIZES,'profiles':PROFILES,'seed':SEED,'repetitions':args.repetitions,'warmups_per_case':1,
            'order_seed':20261107,'python':sys.version,'platform':platform.platform(),
            'postgres_version':query('SELECT version();'),
            'postgres_settings':json.loads(query("SELECT json_object_agg(name,setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','temp_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
            'scope':'server EXPLAIN ANALYZE; session-local indexed states with setup separately; no client transfer; REPEATABLE READ, rollback',
            'cache':'verification and preparation precede timing; warm cache not cleared',
            'datasets_source_sha256':{n:files[n][1] for n in SIZES},'input_snapshot':before,'small_reference_verification':verified}
    def save(): (args.output/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    fields=['order','phase','repetition','profile','seed','size','scenario_id','first_method','state_rows',
            'materialize_ms','index_ms','analyze_ms','setup_ms','baseline_ms','cached_ms','baseline_planning_ms','cached_planning_ms','baseline_indexes','cached_indexes']
    rng=random.Random(meta['order_seed']); order=0
    try:
        with (args.output/'measurements.csv').open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
            for rep in range(args.repetitions+1):
                cases=[(profile,size) for profile in PROFILES for size in SIZES]; rng.shuffle(cases)
                for profile,size in cases:
                    scenario=f'density_{profile}_s{SEED}_v1'; first=rng.choice(['baseline','cached']); phase='warmup' if rep==0 else 'measured'
                    result=subprocess.run(COMMAND,cwd=ROOT,input=case_sql(scenario,size,first,False).encode('utf-8'),capture_output=True,check=True)
                    sections=parse_sections(result.stdout.decode('utf-8')); setup=sections['setup']
                    baseline=plan_metrics(sections['baseline'][0],size); cached=plan_metrics(sections['cached'][0],size)
                    order+=1
                    for method in ('baseline','cached'):
                        (args.output/'plans'/f'{order:03d}_{phase}_{profile}_{size}_{method}.json').write_text(json.dumps(sections[method],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    writer.writerow({'order':order,'phase':phase,'repetition':rep,'profile':profile,'seed':SEED,'size':size,'scenario_id':scenario,'first_method':first,'state_rows':setup['state_rows'],
                        **{k:setup[k] for k in ('materialize_ms','index_ms','analyze_ms')},'setup_ms':round(sum(float(setup[k]) for k in ('materialize_ms','index_ms','analyze_ms')),3),
                        'baseline_ms':baseline['execution_ms'],'cached_ms':cached['execution_ms'],'baseline_planning_ms':baseline['planning_ms'],'cached_planning_ms':cached['planning_ms'],
                        'baseline_indexes':baseline['indexes'],'cached_indexes':cached['indexes']})
                    f.flush(); print(f"{order}: {phase} {profile} {size} baseline={baseline['execution_ms']:.3f} cached={cached['execution_ms']:.3f} ms",flush=True)
        if snapshot()!=before: raise RuntimeError('Inputs changed during experiment')
        with (args.output/'measurements.csv').open(encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
        summaries=[]
        for profile in PROFILES:
            for size in SIZES:
                r=[x for x in rows if x['phase']=='measured' and x['profile']==profile and int(x['size'])==size]
                if len(r)!=args.repetitions: raise AssertionError('Incomplete series')
                summaries.append({'profile':profile,'seed':SEED,'size':size,'runs':len(r),'states':int(r[0]['state_rows']),
                   **{k:round(median(float(x[k]) for x in r),3) for k in ('setup_ms','baseline_ms','cached_ms')},
                   'setup_plus_cached_ms':round(median(float(x['setup_ms'])+float(x['cached_ms']) for x in r),3),
                   'baseline_min_ms':min(float(x['baseline_ms']) for x in r),'baseline_max_ms':max(float(x['baseline_ms']) for x in r),
                   'cached_min_ms':min(float(x['cached_ms']) for x in r),'cached_max_ms':max(float(x['cached_ms']) for x in r)})
        with (args.output/'summary.csv').open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(summaries[0])); writer.writeheader(); writer.writerows(summaries)
        meta['status']='complete'; meta['finished_at_utc']=datetime.now(timezone.utc).isoformat(); save()
    except BaseException as exc:
        meta['status']='failed'; meta['error']=str(exc); save(); raise


if __name__=='__main__': main()
