"""Verify and benchmark delivery start-state joins across density scenarios."""
import argparse
import csv
import hashlib
import json
import platform
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from scripts.load_density_scenarios import PROFILES, SEEDS
from scripts.run_postgres_benchmark import plan_metrics, query
from scripts.verify_density_delivery_states import verify_all


SNAPSHOT = """SELECT json_build_object(
 'delivery_view',pg_get_viewdef('scenario.delivery_start_state'::regclass,true),
 'region_view',pg_get_viewdef('scenario.region_state'::regclass,true),
 'alerts_md5',(SELECT md5(string_agg(row_to_json(a)::text,'' ORDER BY a.event_id)) FROM core.fact_alert a),
 'deliveries_md5',(SELECT md5(string_agg(row_to_json(d)::text,'' ORDER BY d.delivery_id)) FROM core.fact_delivery d),
 'definitions_md5',(SELECT md5(string_agg(row_to_json(s)::text,'' ORDER BY s.scenario_id)) FROM scenario.definitions s),
 'outages_md5',(SELECT md5(string_agg(row_to_json(o)::text,'' ORDER BY o.scenario_id,o.event_id)) FROM scenario.outage_events o),
 'manifests_md5',(SELECT md5(string_agg(row_to_json(m)::text,'' ORDER BY m.scenario_id)) FROM scenario.density_manifest m),
 'load_batches_md5',(SELECT md5(string_agg(row_to_json(b)::text,'' ORDER BY b.load_id)) FROM control.load_batch b),
 'alerts_count',(SELECT count(*) FROM core.fact_alert),
 'deliveries_count',(SELECT count(*) FROM core.fact_delivery),
 'definitions_count',(SELECT count(*) FROM scenario.definitions),
 'outages_count',(SELECT count(*) FROM scenario.outage_events)
);"""


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
            'scope': 'EXPLAIN ANALYZE SELECT all delivery start states for one scenario, including region state construction and join; excludes client result transfer and data preparation',
            'cache': 'verification, preflight and warmup; cache not cleared',
            'memory': 'overall process memory not measured',
            'python': sys.version, 'platform': platform.platform(), 'processor': platform.processor(),
            'postgres_version': query('SELECT version();'),
            'postgres_settings': json.loads(query("SELECT json_object_agg(name,setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
            'input_snapshot': before,
            'delivery_view_sha256': hashlib.sha256(before['delivery_view'].encode('utf-8')).hexdigest(),
            'region_view_sha256': hashlib.sha256(before['region_view'].encode('utf-8')).hexdigest(),
            'verification': verified}
    def save():
        (args.output / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    save()
    fields = ['order', 'phase', 'repetition', 'scenario_id', 'profile', 'seed', 'overlap_pairs',
              'planning_ms', 'execution_ms', 'output_rows', 'shared_hit_blocks', 'shared_read_blocks',
              'temp_read_blocks', 'temp_written_blocks', 'indexes']
    rng = random.Random(meta['order_seed'])
    try:
        order = 0
        with (args.output / 'measurements.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for rep in range(args.repetitions + 1):
                ids = sorted(cases)
                rng.shuffle(ids)
                for scenario in ids:
                    row = cases[scenario]
                    phase = 'warmup' if rep == 0 else 'measured'
                    plan = json.loads(query(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM scenario.delivery_start_state WHERE scenario_id='{scenario}';"))
                    metrics = plan_metrics(plan[0], row['deliveries'])
                    order += 1
                    (args.output / 'plans' / f'{order:03d}_{phase}_{scenario}.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    writer.writerow({'order': order, 'phase': phase, 'repetition': rep, 'scenario_id': scenario,
                                     'profile': row['profile'], 'seed': row['seed'], 'overlap_pairs': row['overlap_pairs'], **metrics})
                    f.flush()
                    print(f"{order}: {phase} {scenario}, {metrics['output_rows']} deliveries, {metrics['execution_ms']:.3f} ms", flush=True)
        if json.loads(query(SNAPSHOT)) != before:
            raise RuntimeError('Inputs changed during benchmark')
        if verify_all() != verified or json.loads(query(SNAPSHOT)) != before:
            raise RuntimeError('Inputs or delivery results changed during final verification')
        with (args.output / 'verification.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(verified[0]))
            writer.writeheader(); writer.writerows(verified)
        with (args.output / 'measurements.csv').open(encoding='utf-8', newline='') as f:
            measurements = list(csv.DictReader(f))
        summaries = []
        for row in verified:
            values = [float(m['execution_ms']) for m in measurements if m['phase'] == 'measured' and m['scenario_id'] == row['scenario_id']]
            if len(values) != args.repetitions:
                raise ValueError('Incomplete measurement series')
            summaries.append({**{k: row[k] for k in ('profile', 'seed', 'scenario_id', 'overlap_pairs', 'deliveries')},
                              'runs': len(values), 'median_ms': round(median(values), 3), 'min_ms': min(values), 'max_ms': max(values)})
        with (args.output / 'summary.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(summaries[0]))
            writer.writeheader(); writer.writerows(summaries)
        with (args.output / 'profile_summary.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['profile', 'seeds', 'median_of_seed_medians_ms', 'min_seed_median_ms', 'max_seed_median_ms'])
            writer.writeheader()
            for profile in PROFILES:
                values = [r['median_ms'] for r in summaries if r['profile'] == profile]
                writer.writerow({'profile': profile, 'seeds': len(values), 'median_of_seed_medians_ms': round(median(values), 3),
                                 'min_seed_median_ms': min(values), 'max_seed_median_ms': max(values)})
        meta['status'] = 'complete'; meta['finished_at_utc'] = datetime.now(timezone.utc).isoformat(); save()
    except BaseException as exc:
        meta['status'] = 'failed'; meta['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()
