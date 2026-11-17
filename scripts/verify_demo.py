"""Verify the pgAdmin demonstration queries without modifying either database."""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import re
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts import verify_alert_exposure, verify_delivery_weather, verify_versioned_context
from scripts.load_district_context import SCENARIO
from scripts.load_spatial_research import COMMAND as SPATIAL_COMMAND
from scripts.prepare_spatial_research import ROOT

FILES = {'logistics': ROOT / 'sql/demo_logistics.sql', 'spatial': ROOT / 'sql/demo_spatial.sql'}
NAMES = {'logistics': ('source_counts', 'overview', 'delivery_examples', 'scenario_counts'),
         'spatial': ('active_version', 'match_counts', 'district_examples', 'district_summary')}
CORE_COMMAND = ['docker', 'compose', 'exec', '-T', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                '-U', 'dwh_user', '-d', 'logistics_dwh']
MARKER = re.compile(r'^-- DEMO:([a-z_]+)[ \t]*$', re.MULTILINE)


def read_queries(path, expected_names):
    text = Path(path).read_text(encoding='utf-8-sig')
    markers = list(MARKER.finditer(text))
    if tuple(m.group(1) for m in markers) != tuple(expected_names):
        raise ValueError('Unexpected demonstration query names or order')
    if markers and any(line.strip() and not line.lstrip().startswith('--') for line in text[:markers[0].start()].splitlines()):
        raise ValueError('Only comments are allowed before the first query')
    result = {}
    for index, match in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        sql = text[match.end():end].strip()
        if not re.match(r'^(SELECT|WITH)\b', sql, re.IGNORECASE) or sql.count(';') != 1 or not sql.endswith(';'):
            raise ValueError('Each demonstration section must contain one SELECT or WITH query')
        result[match.group(1)] = sql
    return result


def execute(command, sql):
    # A read-only transaction ensures verification cannot change project data.
    payload = "BEGIN READ ONLY;\nSET LOCAL TIME ZONE 'UTC';\n" + sql + '\nROLLBACK;\n'
    result = subprocess.run(command + ['--csv', '-q'], cwd=ROOT, input=payload.encode('utf-8'),
                            capture_output=True, check=True)
    return list(csv.DictReader(io.StringIO(result.stdout.decode('utf-8-sig'), newline='')))


def capture_verifiers():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        verify_alert_exposure.main()
    match = re.fullmatch(r'Compared (\d+) deliveries; exposed=(\d+); total_seconds=(\d+); differences=0', output.getvalue().strip())
    if not match:
        raise AssertionError('Unexpected independent alert verification result')
    alert = dict(zip(('deliveries', 'exposed', 'total_seconds'), map(int, match.groups())))
    paths = {'deliveries': ROOT / 'data/synthetic/deliveries.csv', 'weather': ROOT / 'data/processed/kyiv_weather.csv'}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    data = json.loads(verify_delivery_weather.query(verify_delivery_weather.SQL), parse_float=Decimal)
    weather = verify_delivery_weather.check(data, verify_delivery_weather.read_csv(paths['deliveries']),
                                             verify_delivery_weather.read_csv(paths['weather']), hashes)
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name] for name, path in paths.items()):
        raise RuntimeError('Weather or delivery source changed during verification')
    return alert, weather


def validate_results(logistics, spatial, alert, weather, version):
    counts = {r['dataset']: int(r['rows_count']) for r in logistics['source_counts']}
    if set(counts) != {'alerts', 'deliveries_synthetic', 'weather_hourly'} or min(counts.values()) <= 0:
        raise AssertionError('Missing or duplicate source-count rows')
    if len(logistics['source_counts']) != len(counts) or len(logistics['overview']) != 1:
        raise AssertionError('Unexpected source count or overview shape')
    total = counts['deliveries_synthetic']
    overview = logistics['overview'][0]
    if (int(overview['total_deliveries']) != total or alert['deliveries'] != total or weather['deliveries'] != total or
        int(overview['deliveries_with_alert_overlap']) != alert['exposed'] or
        Decimal(overview['total_alert_overlap_seconds']) != alert['total_seconds'] or
        int(overview['unknown_alert_exposure']) != 0 or int(overview['weather_known']) != weather['weather_known'] or
        int(overview['weather_unknown']) != weather['weather_unknown']):
        raise AssertionError('Overview differs from independent verifiers')
    scenario = logistics['scenario_counts']
    if (not scenario or any(r['scenario_id'] != SCENARIO for r in scenario) or
        sum(int(r['deliveries']) for r in scenario) != total):
        raise AssertionError('Scenario is missing or does not cover deliveries')
    examples = logistics['delivery_examples']
    if (len(examples) != min(total, 10) or len({r['delivery_id'] for r in examples}) != len(examples) or
        any(r['scenario_row_present'] != 't' for r in examples)):
        raise AssertionError('Delivery examples are incomplete or duplicated')
    active = spatial['active_version']
    if (len(active) != 1 or active[0]['version_id'] != version['active_version_id'] or
        active[0]['scenario_id'] != SCENARIO or int(active[0]['row_count']) != total):
        raise AssertionError('Displayed active version differs from verified version')
    matches = {r['district_match_status']: int(r['deliveries']) for r in spatial['match_counts']}
    if (len(spatial['match_counts']) != 3 or matches != version['match_counts'] or sum(matches.values()) != total):
        raise AssertionError('District match counts differ from verified version')
    district_examples = spatial['district_examples']
    if (len(district_examples) != min(matches['matched'], 10) or
        len({r['delivery_id'] for r in district_examples}) != len(district_examples) or
        any(r['district_match_status'] != 'matched' or r['version_id'] != version['active_version_id'] for r in district_examples)):
        raise AssertionError('District examples are incomplete or use another version')
    if sum(int(r['synthetic_deliveries']) for r in spatial['district_summary']) != matches['matched']:
        raise AssertionError('District summary does not cover all matched deliveries')
    return {'source_counts': counts,
            'overview': {key: overview[key] for key in overview},
            'scenario_counts': scenario, 'district_match_counts': matches,
            'district_summary_rows': len(spatial['district_summary'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='optional new JSON report')
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise FileExistsError('Report already exists')
    hashes = {kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in FILES.items()}
    queries = {kind: read_queries(path, NAMES[kind]) for kind, path in FILES.items()}
    alert, weather = capture_verifiers()
    before = verify_versioned_context.verify()
    logistics = {name: execute(CORE_COMMAND, sql) for name, sql in queries['logistics'].items()}
    spatial = {name: execute(SPATIAL_COMMAND, sql) for name, sql in queries['spatial'].items()}
    summary = validate_results(logistics, spatial, alert, weather, before)
    after = verify_versioned_context.verify()
    for key in ('active_version_id', 'context_sha256', 'scenario_id', 'delivery_count', 'match_counts'):
        if before[key] != after[key]:
            raise RuntimeError('Active version or source context changed during demonstration verification')
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != hashes[kind] for kind, path in FILES.items()):
        raise RuntimeError('Demo SQL changed during verification')
    report = {'status': 'verified', 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'sql_sha256': hashes, 'checked_queries': NAMES,
              'independent_alert_verification': alert, 'independent_weather_verification': weather,
              'active_version_id': after['active_version_id'], 'context_sha256': after['context_sha256'],
              'spatial_source_fresh_at_before_and_after_checks': True,
              'query_result_summary': summary,
              'scope': 'SELECT queries in read-only transactions; synthetic deliveries, outages and points; unofficial district candidates',
              'limitation': 'Freshness is checked at two moments, not continuously or atomically across the two databases.'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2); handle.write('\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
