"""Compare district/alert joins with and without a spatial index in an isolated database.

The candidate districts, alerts and synthetic delivery source are read-only. All generated
data and measurements go to a new output directory; a disposable Docker container is used.
"""
import argparse
import bisect
import csv
import hashlib
import io
import json
import os
import platform
import random
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from scripts.district_candidate import SHA256 as DISTRICT_SHA256, inspect as inspect_districts
from scripts.load_benchmark_datasets import DATA, inspect_sources
from scripts.prepare_spatial_research import ROOT, digest, relation

ALERTS = ROOT / 'data/processed/alerts.csv'
ALERTS_SHA256 = '52af1ac3c8430f698b560798eb962c82742e68370f546d8a18cc94815619b76b'
IMAGE = 'postgis/postgis:17-3.6@sha256:1ac10b2551ba84ae05db8e7da477afcd0a3fede99bcb7705da2ee157390cb095'
SEED = 20261111
SIZES = (10000, 100000)
REGION = 'UA80000000000093317'


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Timestamp has no UTC offset')
    return result.astimezone(timezone.utc)


def alerts_from_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ['event_id', 'region_id', 'region_name', 'event_type', 'started_at', 'finished_at']:
            raise ValueError('Unexpected alert fields')
        records = list(reader)
    events = []
    for row in records:
        start, end = instant(row['started_at']), instant(row['finished_at'])
        if row['region_id'] != REGION or end <= start:
            raise ValueError('Unexpected alert region or interval')
        events.append((start, end))
    events.sort()
    if not events or any(a[1] > b[0] for a, b in zip(events, events[1:])):
        raise ValueError('Reference calculation requires non-overlapping alerts')
    return events


def in_alert(when, events, starts):
    pos = bisect.bisect_right(starts, when) - 1
    return pos >= 0 and when < events[pos][1]


def district_rings(features):
    result = []
    for feature in features:
        ring = feature['geometry']['coordinates'][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        result.append((feature['properties']['id'], ring, (min(xs), min(ys), max(xs), max(ys))))
    return result


def match_districts(point, rings):
    x, y = point
    matches = []
    for district_id, ring, (xmin, ymin, xmax, ymax) in rings:
        if xmin <= x <= xmax and ymin <= y <= ymax and relation(point, ring) != 'outside':
            matches.append(district_id)
    return len(matches), matches[0] if len(matches) == 1 else None


def make_inputs(deliveries, events, features, output, max_rows):
    rings = district_rings(features)
    bounds = (min(b[0] for _, _, b in rings) - .05, min(b[1] for _, _, b in rings) - .05,
              max(b[2] for _, _, b in rings) + .05, max(b[3] for _, _, b in rings) + .05)
    rng = random.Random(SEED)
    starts = [event[0] for event in events]
    points_path, expected_path = output / 'synthetic_points.csv', output / 'independent_expected.csv'
    counts = {size: {'matched': 0, 'unmatched': 0, 'ambiguous': 0, 'alert_active': 0} for size in SIZES}
    with Path(deliveries).open(encoding='utf-8', newline='') as source, \
         points_path.open('x', encoding='utf-8', newline='') as pf, \
         expected_path.open('x', encoding='utf-8', newline='') as ef:
        reader = csv.DictReader(source)
        if reader.fieldnames != ['delivery_id', 'region_id', 'started_at', 'finished_at', 'distance_km', 'status']:
            raise ValueError('Unexpected delivery fields')
        pw, ew = csv.writer(pf, lineterminator='\n'), csv.writer(ef, lineterminator='\n')
        pw.writerow(['ordinal', 'started_at', 'longitude', 'latitude'])
        ew.writerow(['ordinal', 'alert_active', 'match_count', 'district_id'])
        count = 0
        for count, row in enumerate(reader, start=1):
            if count > max_rows:
                raise ValueError('Too many delivery rows')
            when, end = instant(row['started_at']), instant(row['finished_at'])
            if row['delivery_id'] != f'B-{count:08d}' or row['region_id'] != REGION or end <= when:
                raise ValueError('Unexpected synthetic delivery row')
            lon = f'{rng.uniform(bounds[0], bounds[2]):.8f}'
            lat = f'{rng.uniform(bounds[1], bounds[3]):.8f}'
            match_count, district = match_districts((float(lon), float(lat)), rings)
            active = in_alert(when, events, starts)
            pw.writerow([count, when.isoformat(), lon, lat])
            ew.writerow([count, str(active).lower(), match_count, district or ''])
            for size in SIZES:
                if count <= size:
                    key = 'unmatched' if match_count == 0 else 'matched' if match_count == 1 else 'ambiguous'
                    counts[size][key] += 1
                    counts[size]['alert_active'] += int(active)
        if count != max_rows:
            raise ValueError('Wrong number of deliveries')
    return points_path, expected_path, bounds, counts


def query_sql(size, method):
    if size not in SIZES or method not in ('indexed', 'unindexed'):
        raise ValueError('Invalid benchmark case')
    return f"""WITH matches AS MATERIALIZED (
 SELECT p.ordinal, count(*)::integer AS match_count, min(d.district_id) AS district_id
 FROM bench.districts d JOIN bench.points_{method} p ON ST_Covers(d.geom,p.geom)
 WHERE p.ordinal <= {size} GROUP BY p.ordinal
)
SELECT p.ordinal,
 COALESCE((SELECT a.finished_at > p.started_at FROM bench.alerts a
           WHERE a.started_at <= p.started_at ORDER BY a.started_at DESC LIMIT 1),false) AS alert_active,
 COALESCE(m.match_count,0)::integer AS match_count,
 CASE WHEN m.match_count=1 THEN m.district_id ELSE NULL END AS district_id
FROM bench.points_base p LEFT JOIN matches m ON m.ordinal=p.ordinal WHERE p.ordinal <= {size}"""


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def docker(args, env=None, check=True, data=None):
    result = subprocess.run(['docker', *args], env=env, input=data, capture_output=True)
    if check and result.returncode:
        raise RuntimeError('Docker command failed: ' + result.stderr.decode('utf-8', errors='replace').strip())
    return result


def pg(container, sql):
    result = docker(['exec', '-i', container, 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                     '-U', 'dwh_user', '-d', 'spatial_bench'], data=sql.encode('utf-8'))
    return result.stdout.decode('utf-8').strip()


def start_container():
    name = 'dwhbench' + secrets.token_hex(8)
    if docker(['container', 'inspect', name], check=False).returncode == 0:
        raise RuntimeError('Container name collision')
    env = os.environ.copy()
    env['POSTGRES_PASSWORD'] = secrets.token_urlsafe(32)
    started = docker(['run', '-d', '--rm', '--pull=never', '--network', 'none', '--name', name,
                      '--label', 'dwh.benchmark.id=' + name, '-e', 'POSTGRES_PASSWORD',
                      '-e', 'POSTGRES_USER=dwh_user', '-e', 'POSTGRES_DB=spatial_bench', IMAGE], env=env)
    container = started.stdout.decode('utf-8').strip()
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise RuntimeError('Unexpected Docker container identifier')
    return name, container


def stop_container(name, container):
    obj = json.loads(docker(['inspect', container]).stdout)[0]
    if (not re.fullmatch('dwhbench[0-9a-f]{16}', name) or obj['Name'].lstrip('/') != name or
        obj['Config']['Labels'].get('dwh.benchmark.id') != name or obj['Id'] != container or
        any(m['Type'] != 'volume' for m in obj['Mounts'])):
        raise RuntimeError('Refusing to stop unexpected Docker container')
    volumes = [mount['Name'] for mount in obj['Mounts']]
    docker(['stop', '--time', '20', container])
    if docker(['container', 'inspect', container], check=False).returncode == 0:
        raise RuntimeError('Benchmark container still exists')
    for volume in volumes:
        if docker(['volume', 'inspect', volume], check=False).returncode == 0:
            raise RuntimeError('Benchmark anonymous volume still exists')


def load_data(container, features, events, points_path, expected_path):
    values = []
    for feature in features:
        geom = json.dumps(feature['geometry'], separators=(',', ':'))
        values.append(f"({sql_literal(feature['properties']['id'])},ST_SetSRID(ST_GeomFromGeoJSON({sql_literal(geom)}),4326))")
    alert_values = ','.join(f"({sql_literal(a.isoformat())}::timestamptz,{sql_literal(b.isoformat())}::timestamptz)" for a, b in events)
    sql = f"""CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA bench;
CREATE TABLE bench.districts(district_id text PRIMARY KEY,geom geometry(Polygon,4326) NOT NULL);
INSERT INTO bench.districts VALUES {','.join(values)};
CREATE TABLE bench.alerts(started_at timestamptz NOT NULL,finished_at timestamptz NOT NULL);
INSERT INTO bench.alerts VALUES {alert_values};
CREATE INDEX alert_started_btree ON bench.alerts(started_at);
CREATE TABLE bench.stage(ordinal integer,started_at timestamptz,longitude double precision,latitude double precision);
COPY bench.stage FROM STDIN WITH (FORMAT csv, HEADER true);
{points_path.read_text(encoding='utf-8')}\\.
CREATE TABLE bench.points_base AS SELECT ordinal,started_at,ST_SetSRID(ST_MakePoint(longitude,latitude),4326)::geometry(Point,4326) geom FROM bench.stage;
ALTER TABLE bench.points_base ADD PRIMARY KEY (ordinal);
CREATE TABLE bench.points_unindexed AS TABLE bench.points_base;
ALTER TABLE bench.points_unindexed ADD PRIMARY KEY (ordinal);
CREATE TABLE bench.points_indexed AS TABLE bench.points_base;
ALTER TABLE bench.points_indexed ADD PRIMARY KEY (ordinal);
CREATE TABLE bench.expected(ordinal integer PRIMARY KEY,alert_active boolean,match_count integer,district_id text);
COPY bench.expected FROM STDIN WITH (FORMAT csv, HEADER true);
{expected_path.read_text(encoding='utf-8')}\\.
ANALYZE bench.districts; ANALYZE bench.alerts; ANALYZE bench.points_base;
ANALYZE bench.points_unindexed; ANALYZE bench.points_indexed; ANALYZE bench.expected;
"""
    pg(container, sql)
    before = time.perf_counter()
    pg(container, 'CREATE INDEX points_geometry_gist ON bench.points_indexed USING gist(geom); ANALYZE bench.points_indexed;')
    return round((time.perf_counter() - before) * 1000, 3)


def verify(container, size, method):
    query = query_sql(size, method)
    sql = f"""WITH actual AS MATERIALIZED ({query}), differences AS (
 (SELECT * FROM actual EXCEPT ALL SELECT * FROM bench.expected WHERE ordinal <= {size})
 UNION ALL (SELECT * FROM bench.expected WHERE ordinal <= {size} EXCEPT ALL SELECT * FROM actual)
) SELECT count(*) FROM differences;"""
    differences = int(pg(container, sql))
    if differences:
        raise AssertionError(f'{size} {method}: {differences} rows differ from independent reference')


def verify_boundaries(container):
    """Exercise both actual SQL variants on shared boundaries and alert endpoints."""
    queries = {method: query_sql(10000, method).replace('bench.', 'bench_fixture.')
               for method in ('unindexed', 'indexed')}
    sql = f"""BEGIN;
CREATE SCHEMA bench_fixture;
CREATE TABLE bench_fixture.districts(district_id text, geom geometry(Polygon,4326));
INSERT INTO bench_fixture.districts VALUES
 ('left',ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',4326)),
 ('right',ST_GeomFromText('POLYGON((1 0,2 0,2 1,1 1,1 0))',4326));
CREATE TABLE bench_fixture.alerts(started_at timestamptz,finished_at timestamptz);
INSERT INTO bench_fixture.alerts VALUES ('2026-07-01 00:10:00+00','2026-07-01 00:20:00+00');
CREATE TABLE bench_fixture.points_base(ordinal integer,started_at timestamptz,geom geometry(Point,4326));
INSERT INTO bench_fixture.points_base VALUES
 (1,'2026-07-01 00:10:00+00',ST_GeomFromText('POINT(0.5 0.5)',4326)),
 (2,'2026-07-01 00:20:00+00',ST_GeomFromText('POINT(1 0.5)',4326)),
 (3,'2026-07-01 00:09:00+00',ST_GeomFromText('POINT(3 0.5)',4326)),
 (4,'2026-07-01 00:19:59+00',ST_GeomFromText('POINT(1.5 0.5)',4326)),
 (5,'2026-07-01 00:10:00+00',ST_GeomFromText('POINT(0 0)',4326));
CREATE TABLE bench_fixture.points_unindexed AS TABLE bench_fixture.points_base;
CREATE TABLE bench_fixture.points_indexed AS TABLE bench_fixture.points_base;
CREATE INDEX fixture_points_gist ON bench_fixture.points_indexed USING gist(geom);
CREATE TEMP TABLE fixture_expected(ordinal integer,alert_active boolean,match_count integer,district_id text);
INSERT INTO fixture_expected VALUES (1,true,1,'left'),(2,false,2,NULL),(3,false,0,NULL),
 (4,true,1,'right'),(5,true,1,'left');
CREATE TEMP TABLE fixture_plain AS {queries['unindexed']};
CREATE TEMP TABLE fixture_indexed AS {queries['indexed']};
DO $$ BEGIN
 IF EXISTS ((SELECT * FROM fixture_plain EXCEPT ALL SELECT * FROM fixture_expected)
   UNION ALL (SELECT * FROM fixture_expected EXCEPT ALL SELECT * FROM fixture_plain)
   UNION ALL (SELECT * FROM fixture_indexed EXCEPT ALL SELECT * FROM fixture_expected)
   UNION ALL (SELECT * FROM fixture_expected EXCEPT ALL SELECT * FROM fixture_indexed))
 THEN RAISE EXCEPTION 'Boundary or alert endpoint mismatch'; END IF;
END $$;
ROLLBACK;"""
    pg(container, sql)


def walk(node):
    yield node
    for child in node.get('Plans', []):
        yield from walk(child)


def run_benchmark(container, output, repetitions):
    plans = output / 'plans'
    plans.mkdir()
    measurements = []
    rng = random.Random(SEED)
    for rep in range(repetitions + 1):
        cases = [(size, method) for size in SIZES for method in ('unindexed', 'indexed')]
        rng.shuffle(cases)
        for size, method in cases:
            phase = 'warmup' if rep == 0 else 'measured'
            data = json.loads(pg(container, 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query_sql(size, method)))
            root = data[0]['Plan']
            if root['Actual Rows'] != size:
                raise AssertionError('Measured query returned incorrect row count')
            indexes = sorted({node['Index Name'] for node in walk(root) if 'Index Name' in node})
            row = {'order': len(measurements) + 1, 'phase': phase, 'repetition': rep, 'size': size, 'method': method,
                   'planning_ms': data[0]['Planning Time'], 'execution_ms': data[0]['Execution Time'],
                   'output_rows': root['Actual Rows'], 'shared_hit_blocks': root.get('Shared Hit Blocks', 0),
                   'shared_read_blocks': root.get('Shared Read Blocks', 0), 'temp_read_blocks': root.get('Temp Read Blocks', 0),
                   'temp_written_blocks': root.get('Temp Written Blocks', 0), 'indexes': ';'.join(indexes)}
            (plans / f"{row['order']:02d}_{phase}_{method}_{size}.json").write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
            measurements.append(row)
            print(f"{row['order']}: {phase} {method} {size}: {row['execution_ms']:.3f} ms", flush=True)
    with (output / 'measurements.csv').open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(measurements[0]))
        writer.writeheader(); writer.writerows(measurements)
    summary = []
    for size in SIZES:
        for method in ('unindexed', 'indexed'):
            values = [r['execution_ms'] for r in measurements if r['size'] == size and r['method'] == method and r['phase'] == 'measured']
            summary.append({'size': size, 'method': method, 'runs': len(values), 'median_ms': round(median(values), 3),
                            'min_ms': min(values), 'max_ms': max(values)})
    with (output / 'summary.csv').open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
    return measurements, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new directory for synthetic inputs, plans and reports')
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    if args.repetitions < 1 or args.repetitions > 20:
        parser.error('repetitions must be between 1 and 20')
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Choose a new output directory')
    if digest(ALERTS) != ALERTS_SHA256:
        raise ValueError('Alert source differs from benchmark snapshot')
    seed, datasets = inspect_sources(DATA)
    if seed != 42:
        raise ValueError('Unexpected benchmark delivery seed')
    features, events = inspect_districts(), alerts_from_csv(ALERTS)
    original_hashes = {'alerts': digest(ALERTS), 'deliveries': datasets[100000][1], 'districts': DISTRICT_SHA256}
    output.mkdir(parents=True, exist_ok=False)
    print('Generating synthetic coordinates and independent reference...', flush=True)
    points_path, expected_path, bounds, counts = make_inputs(datasets[100000][0], events, features, output, 100000)
    name = container = None
    cleanup = 'not_started'
    try:
        name, container = start_container()
        deadline = time.monotonic() + 120
        while True:
            ready = docker(['exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'dwh_user', '-d', 'spatial_bench'], check=False)
            if ready.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise TimeoutError('PostGIS did not become ready')
            time.sleep(1)
        print('Loading isolated database...', flush=True)
        index_build_ms = load_data(container, features, events, points_path, expected_path)
        integrity = json.loads(pg(container, """SELECT json_build_object(
          'districts',(SELECT count(*) FROM bench.districts), 'valid_districts',(SELECT count(*) FROM bench.districts WHERE ST_IsValid(geom)),
          'alerts',(SELECT count(*) FROM bench.alerts), 'points',(SELECT count(*) FROM bench.points_base),
          'expected',(SELECT count(*) FROM bench.expected),
          'copies_equal',NOT EXISTS ((SELECT * FROM bench.points_indexed EXCEPT ALL SELECT * FROM bench.points_unindexed)
             UNION ALL (SELECT * FROM bench.points_unindexed EXCEPT ALL SELECT * FROM bench.points_indexed)),
          'indexed_gist',to_regclass('bench.points_geometry_gist') IS NOT NULL);"""))
        if integrity != {'districts': 10, 'valid_districts': 10, 'alerts': len(events), 'points': 100000,
                         'expected': 100000, 'copies_equal': True, 'indexed_gist': True}:
            raise AssertionError(f'Benchmark input integrity failed: {integrity}')
        verify_boundaries(container)
        print('Verified SQL district boundaries and alert endpoints.', flush=True)
        print('Checking every result against independent reference...', flush=True)
        for size in SIZES:
            for method in ('unindexed', 'indexed'):
                verify(container, size, method)
                print(f'Verified {size}: {method}', flush=True)
        postgres = pg(container, """SELECT json_build_object('version',version(),'postgis',postgis_full_version(),
             'settings',(SELECT json_object_agg(name,setting || ' ' || coalesce(unit,'')) FROM pg_settings
             WHERE name IN ('work_mem','shared_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit')));""")
        measurements, summary = run_benchmark(container, output, args.repetitions)
        print('Rechecking every result after measurements...', flush=True)
        for size in SIZES:
            for method in ('unindexed', 'indexed'):
                verify(container, size, method)
        if digest(ALERTS) != original_hashes['alerts'] or digest(datasets[100000][0]) != original_hashes['deliveries']:
            raise RuntimeError('Source files changed during benchmark')
        if len(inspect_districts()) != 10:
            raise RuntimeError('District source changed during benchmark')
    finally:
        if container:
            stop_container(name, container)
            cleanup = 'complete'
    meta = {'status': 'complete', 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
            'runner_sha256': digest(Path(__file__)), 'delivery_manifest_sha256': digest(DATA / 'manifest.json'),
            'source_sha256': original_hashes, 'input_data_sha256': {'synthetic_points.csv': digest(points_path), 'independent_expected.csv': digest(expected_path)},
            'source_commit': '5b82d4c46c828f65b4f945098f8e6801b57ce1b0', 'districts': 10,
            'source_note': 'Unofficial simplified OSM-derived district candidates; administrative accuracy not established.',
            'data_note': 'Synthetic deliveries and coordinates; uniform longitude/latitude over district bounding box plus 0.05 degrees; not representative routes or area-uniform density.',
            'seed': SEED, 'bounds_lon_lat': bounds, 'events': len(events), 'sizes': SIZES,
            'counts': counts, 'input_integrity': integrity, 'boundary_fixture_passed': True,
            'independent_reference': 'Python ray casting plus binary search over non-overlapping alert intervals; all outputs compared before and after',
            'temporal_query': 'Most recent alert start <= delivery start using B-tree, then check delivery start < alert end; valid for the validated non-overlapping alert snapshot only.',
            'repetitions': args.repetitions, 'warmups_per_case': 1, 'order_seed': SEED,
            'timing_scope': 'EXPLAIN ANALYZE server time; excludes result transfer and index creation; buffers and plans retained',
            'index_build_wall_ms_once': index_build_ms, 'index_build_scope': 'CREATE INDEX plus ANALYZE and docker exec process overhead; one sample, not included in query times',
            'cache': 'warmups; cache not cleared', 'memory': 'total process memory not measured',
            'postgres': json.loads(postgres), 'python': platform.python_version(), 'platform': platform.platform(),
            'image': IMAGE, 'container_and_anonymous_volume_cleanup': cleanup, 'summary': summary,
            'query_sha256': {f'{s}_{m}': hashlib.sha256(query_sql(s, m).encode('utf-8')).hexdigest() for s in SIZES for m in ('unindexed','indexed')},
            'spatial_index_used_in_measured': {str(s): sum('points_geometry_gist' in r['indexes'] for r in measurements if r['size']==s and r['method']=='indexed' and r['phase']=='measured') for s in SIZES},
            'limitations': 'One host, one seed, prefix datasets, 10 unofficial polygons, synthetic points, warm cache; no causal or production claims.'}
    with (output / 'metadata.json').open('x', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2); f.write('\n')
    print('Benchmark complete; isolated container and anonymous volume removed.', flush=True)


if __name__ == '__main__':
    main()
