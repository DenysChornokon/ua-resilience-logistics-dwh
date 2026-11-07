"""Compare dynamic delivery joins with a session-local indexed state cache."""
import argparse
import csv
import json
import math
import platform
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from scripts.generate_density_scenarios import ROOT, PROFILES, SEEDS
from scripts.run_delivery_density_benchmark import SNAPSHOT
from scripts.run_postgres_benchmark import plan_metrics, query
from scripts.verify_density_delivery_states import verify_all


def statements(scenario):
    if scenario not in {f'density_{p}_s{s}_v1' for p in PROFILES for s in SEEDS}:
        raise ValueError('Unexpected scenario identifier')
    baseline = f"SELECT * FROM scenario.delivery_start_state WHERE scenario_id='{scenario}'"
    optimized = f"""SELECT s.scenario_id,d.delivery_id,d.region_id,d.started_at AS delivery_started_at,
       r.started_at AS state_started_at,r.finished_at AS state_finished_at,
       (r.started_at IS NOT NULL) AS state_known,r.alert_active,r.outage_active
FROM scenario.definitions s JOIN core.fact_delivery d ON d.region_id=s.region_id
 AND d.started_at>=s.window_start AND d.started_at<s.window_end
LEFT JOIN pg_temp.delivery_state_cache r ON r.scenario_id=s.scenario_id AND r.region_id=d.region_id
 AND tstzrange(r.started_at,r.finished_at,'[)') @> d.started_at
WHERE s.scenario_id='{scenario}'"""
    return baseline, optimized


def sql_for_case(scenario, first):
    baseline, optimized = statements(scenario)
    if first not in ('baseline', 'cached'):
        raise ValueError('Invalid order')
    checks = f"""WITH a AS MATERIALIZED ({baseline}), b AS MATERIALIZED ({optimized}),
       diff AS ((SELECT * FROM a EXCEPT ALL SELECT * FROM b)
                UNION ALL (SELECT * FROM b EXCEPT ALL SELECT * FROM a))
SELECT json_build_object('baseline_rows',(SELECT count(*) FROM a),
 'cached_rows',(SELECT count(*) FROM b),'differences',(SELECT count(*) FROM diff));"""
    explain = {'baseline': baseline, 'cached': optimized}
    order = [first, 'cached' if first == 'baseline' else 'baseline']
    return f"""\\set ON_ERROR_STOP on
CREATE TEMP TABLE experiment_clock(step text, at timestamptz);
INSERT INTO experiment_clock VALUES ('start', clock_timestamp());
CREATE TEMP TABLE delivery_state_cache AS SELECT * FROM scenario.region_state WHERE scenario_id='{scenario}';
INSERT INTO experiment_clock VALUES ('materialized', clock_timestamp());
CREATE INDEX delivery_state_cache_range_idx ON delivery_state_cache USING gist (tstzrange(started_at,finished_at,'[)'));
INSERT INTO experiment_clock VALUES ('indexed', clock_timestamp());
ANALYZE delivery_state_cache;
INSERT INTO experiment_clock VALUES ('analyzed', clock_timestamp());
\\echo __SETUP__
SELECT json_build_object('state_rows',(SELECT count(*) FROM delivery_state_cache),
 'materialize_ms',1000*extract(epoch FROM ((SELECT at FROM experiment_clock WHERE step='materialized')-(SELECT at FROM experiment_clock WHERE step='start'))),
 'index_ms',1000*extract(epoch FROM ((SELECT at FROM experiment_clock WHERE step='indexed')-(SELECT at FROM experiment_clock WHERE step='materialized'))),
 'analyze_ms',1000*extract(epoch FROM ((SELECT at FROM experiment_clock WHERE step='analyzed')-(SELECT at FROM experiment_clock WHERE step='indexed'))));
\\echo __CHECK__
{checks}
\\echo __{order[0].upper()}__
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {explain[order[0]]};
\\echo __{order[1].upper()}__
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {explain[order[1]]};
\\echo __END__
"""


def parse_sections(raw):
    parts = {}
    key = None
    for line in raw.splitlines():
        if line.startswith('__') and line.endswith('__'):
            key = line.strip('_').lower()
            if key in parts:
                raise ValueError('Duplicate output marker')
            parts[key] = []
        elif key is not None:
            parts[key].append(line)
    if set(parts) != {'setup', 'check', 'baseline', 'cached', 'end'}:
        raise ValueError('Missing benchmark output marker')
    return {k: json.loads('\n'.join(parts[k])) for k in ('setup', 'check', 'baseline', 'cached')}


def run_case(scenario, first, expected):
    cmd = ['docker','compose','exec','-T','db','psql','-X','-q','-A','-t',
           '-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh']
    proc = subprocess.run(cmd, cwd=ROOT, input=sql_for_case(scenario, first).encode('utf-8'),
                          capture_output=True, check=True)
    sections = parse_sections(proc.stdout.decode('utf-8'))
    check = sections['check']
    if check != {'baseline_rows': expected, 'cached_rows': expected, 'differences': 0}:
        raise AssertionError(f'Results differ for {scenario}: {check}')
    if sections['setup']['state_rows'] < 1:
        raise AssertionError(f'No states materialized for {scenario}')
    for method in ('baseline', 'cached'):
        plan_metrics(sections[method][0], expected)
    return sections


def break_even(setup, baseline, cached):
    """Estimated repeated-query count for one fixed snapshot, not a confidence bound."""
    if baseline <= cached:
        return None
    return math.ceil(setup / (baseline - cached))


def summarize_runs(verified, measurements, repetitions):
    summaries = []
    for row in verified:
        runs = [m for m in measurements if m['phase']=='measured' and m['scenario_id']==row['scenario_id']]
        if len(runs) != repetitions:
            raise ValueError('Incomplete measurement series')
        med = {key: median(float(r[key]) for r in runs) for key in ('materialize_ms','index_ms','analyze_ms','setup_ms','baseline_ms','cached_ms')}
        total = [float(r['setup_ms']) + float(r['cached_ms']) for r in runs]
        summaries.append({'profile':row['profile'],'seed':row['seed'],'scenario_id':row['scenario_id'],
           'states':int(runs[0]['state_rows']),'deliveries':row['deliveries'],'runs':len(runs),
           **{key:round(value,3) for key,value in med.items()},
           'setup_plus_cached_median_ms':round(median(total),3),
           'setup_plus_cached_min_ms':round(min(total),3),'setup_plus_cached_max_ms':round(max(total),3),
           'baseline_min_ms':min(float(r['baseline_ms']) for r in runs),
           'baseline_max_ms':max(float(r['baseline_ms']) for r in runs),
           'estimated_break_even_queries':break_even(med['setup_ms'],med['baseline_ms'],med['cached_ms'])})
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    if args.output.exists():
        raise FileExistsError('Output directory already exists')
    before = json.loads(query(SNAPSHOT))
    verified = verify_all()
    if json.loads(query(SNAPSHOT)) != before:
        raise RuntimeError('Inputs changed during verification')
    cases = {r['scenario_id']: r for r in verified}
    if len(cases) != len(PROFILES) * len(SEEDS):
        raise ValueError('Expected nine distinct scenarios')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'plans').mkdir()
    meta = {'status': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
            'repetitions': args.repetitions, 'warmups_per_scenario': 1, 'order_seed': 20261107,
            'scope': 'EXPLAIN ANALYZE server execution; baseline constructs states each query; cached uses session-local temp states and GiST index; setup timed separately with server clock',
            'cache': 'independent verification, setup and pre-measurement result comparison warm cache; cache not cleared',
            'memory': 'overall process memory not measured', 'python': sys.version,
            'platform': platform.platform(), 'processor': platform.processor(),
            'postgres_version': query('SELECT version();'),
            'postgres_settings': json.loads(query("SELECT json_object_agg(name,setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','temp_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
            'input_snapshot': before, 'verification': verified,
            'optimized_query_template': statements(verified[0]['scenario_id'])[1],
            'setup_definition': 'CREATE TEMP TABLE AS SELECT region_state for scenario; create GiST index over tstzrange; ANALYZE; times from server clock around each statement'}
    def save():
        (args.output / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    save()
    fields = ['order','phase','repetition','scenario_id','profile','seed','first_method','state_rows',
              'materialize_ms','index_ms','analyze_ms','setup_ms','baseline_ms','cached_ms',
              'baseline_planning_ms','cached_planning_ms','baseline_shared_read_blocks','cached_shared_read_blocks',
              'baseline_temp_written_blocks','cached_temp_written_blocks','baseline_indexes','cached_indexes']
    rng = random.Random(meta['order_seed'])
    try:
        order = 0
        with (args.output / 'measurements.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            for rep in range(args.repetitions + 1):
                ids = sorted(cases); rng.shuffle(ids)
                for scenario in ids:
                    case = cases[scenario]; first = rng.choice(['baseline','cached'])
                    result = run_case(scenario, first, case['deliveries'])
                    setup = result['setup']
                    baseline = plan_metrics(result['baseline'][0], case['deliveries'])
                    cached = plan_metrics(result['cached'][0], case['deliveries'])
                    order += 1; phase = 'warmup' if rep == 0 else 'measured'
                    for method in ('baseline','cached'):
                        (args.output / 'plans' / f'{order:03d}_{phase}_{scenario}_{method}.json').write_text(json.dumps(result[method], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    writer.writerow({'order':order,'phase':phase,'repetition':rep,'scenario_id':scenario,
                       'profile':case['profile'],'seed':case['seed'],'first_method':first,'state_rows':setup['state_rows'],
                       'materialize_ms':setup['materialize_ms'],'index_ms':setup['index_ms'],'analyze_ms':setup['analyze_ms'],
                       'setup_ms':round(sum(float(setup[k]) for k in ('materialize_ms','index_ms','analyze_ms')),3),
                       'baseline_ms':baseline['execution_ms'],'cached_ms':cached['execution_ms'],
                       'baseline_planning_ms':baseline['planning_ms'],'cached_planning_ms':cached['planning_ms'],
                       'baseline_shared_read_blocks':baseline['shared_read_blocks'],'cached_shared_read_blocks':cached['shared_read_blocks'],
                       'baseline_temp_written_blocks':baseline['temp_written_blocks'],'cached_temp_written_blocks':cached['temp_written_blocks'],
                       'baseline_indexes':baseline['indexes'],'cached_indexes':cached['indexes']})
                    f.flush()
                    print(f"{order}: {phase} {scenario} setup={sum(float(setup[k]) for k in ('materialize_ms','index_ms','analyze_ms')):.3f} baseline={baseline['execution_ms']:.3f} cached={cached['execution_ms']:.3f} ms", flush=True)
        if json.loads(query(SNAPSHOT)) != before:
            raise RuntimeError('Inputs changed during benchmark')
        if verify_all() != verified or json.loads(query(SNAPSHOT)) != before:
            raise RuntimeError('Inputs or delivery results changed during final verification')
        with (args.output / 'measurements.csv').open(encoding='utf-8', newline='') as f:
            measurements = list(csv.DictReader(f))
        summaries = summarize_runs(verified, measurements, args.repetitions)
        with (args.output / 'summary.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(summaries[0])); writer.writeheader(); writer.writerows(summaries)
        meta['status'] = 'complete'; meta['finished_at_utc'] = datetime.now(timezone.utc).isoformat(); save()
    except BaseException as exc:
        meta['status'] = 'failed'; meta['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()
