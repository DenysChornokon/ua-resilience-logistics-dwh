"""Check every delivery state against independent intervals and source files."""
import csv
import hashlib
import json

from scripts.generate_density_scenarios import ROOT, REGION, START, END, PROFILES, SEEDS
from scripts.load_density_scenarios import DATA, inspect
from scripts.run_postgres_benchmark import query
from scripts.verify_multifactor_states import parse
from scripts.verify_scenario_delivery_states import verify


def source_deliveries(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    result = [(r['delivery_id'], parse(r['started_at'])) for r in rows
              if r['region_id'] == REGION and START <= parse(r['started_at']) < END]
    if not result or len({r[0] for r in result}) != len(result):
        raise ValueError('Missing or duplicate source deliveries')
    return sorted(result)


def check_record(data, entry, profile, seed, alert_hash, delivery_hash, deliveries):
    scenario, filename, digest, rows, metrics = entry
    definition, m = data['scenario'], data['manifest']
    if not definition or not m or not data['alerts']:
        raise AssertionError(f'Missing source or scenario: {scenario}')
    if (definition['region_id'], parse(definition['window_start']), parse(definition['window_end'])) != (REGION, START, END):
        raise AssertionError(f'Scenario definition mismatch: {scenario}')
    if (m['source_file'], m['sha256'], m['seed'], m['profile'], m['event_count'],
        m['overlap_pairs'], m['peak_simultaneous'], m['union_seconds']) != (
        filename, digest, seed, profile, len(rows), metrics['overlap_pairs'],
        metrics['peak_simultaneous'], metrics['union_seconds']):
        raise AssertionError(f'Manifest mismatch: {scenario}')
    if data['alert_hash'] != alert_hash or data['delivery_hash'] != delivery_hash:
        raise AssertionError(f'Loaded source hash mismatch: {scenario}')
    actual = sorted((i, parse(t)) for i, t in data['deliveries'])
    if actual != deliveries:
        raise AssertionError(f'Deliveries differ from source: {scenario}')
    counts = verify(data, rows, digest, seed, len(rows))
    return {'scenario_id': scenario, 'profile': profile, 'seed': seed,
            'overlap_pairs': metrics['overlap_pairs'], 'deliveries': sum(counts.values()),
            'neither': counts[(False, False)], 'alert_only': counts[(True, False)],
            'outage_only': counts[(False, True)], 'both': counts[(True, True)]}


def verify_all():
    datasets = inspect(DATA)
    alert_path = ROOT / 'data/processed/alerts.csv'
    delivery_path = ROOT / 'data/synthetic/deliveries.csv'
    alert_hash = hashlib.sha256(alert_path.read_bytes()).hexdigest()
    delivery_hash = hashlib.sha256(delivery_path.read_bytes()).hexdigest()
    deliveries = source_deliveries(delivery_path)
    records = []
    for profile in PROFILES:
        for seed in SEEDS:
            entry = datasets[(profile, seed)]
            scenario = entry[0]
            sql = f"""SELECT json_build_object(
              'scenario',(SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='{scenario}'),
              'manifest',(SELECT row_to_json(m) FROM scenario.density_manifest m WHERE scenario_id='{scenario}'),
              'alert_hash',(SELECT b.source_sha256 FROM control.load_batch b WHERE b.source_type='alerts' AND b.status='success' AND b.rows_accepted=(SELECT count(*) FROM core.fact_alert) AND NOT EXISTS (SELECT 1 FROM core.fact_alert a WHERE a.load_id<>b.load_id)),
              'delivery_hash',(SELECT b.source_sha256 FROM control.load_batch b WHERE b.source_type='deliveries' AND b.status='success' AND b.rows_accepted=(SELECT count(*) FROM core.fact_delivery) AND NOT EXISTS (SELECT 1 FROM core.fact_delivery d WHERE d.load_id<>b.load_id)),
              'deliveries',(SELECT coalesce(json_agg(json_build_array(d.delivery_id,d.started_at) ORDER BY d.delivery_id),'[]'::json) FROM core.fact_delivery d JOIN scenario.definitions s ON d.region_id=s.region_id WHERE s.scenario_id='{scenario}' AND d.started_at>=s.window_start AND d.started_at<s.window_end),
              'alerts',(SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json) FROM core.fact_alert a JOIN scenario.definitions s ON a.region_id=s.region_id WHERE s.scenario_id='{scenario}' AND a.event_type='Повітряна тривога'),
              'outages',(SELECT coalesce(json_agg(json_build_array(event_id,started_at,finished_at,source_type) ORDER BY event_id),'[]'::json) FROM scenario.outage_events WHERE scenario_id='{scenario}'),
              'results',(SELECT coalesce(json_agg(json_build_array(delivery_id,delivery_started_at,state_started_at,state_finished_at,state_known,alert_active,outage_active) ORDER BY delivery_id),'[]'::json) FROM scenario.delivery_start_state WHERE scenario_id='{scenario}')
            );"""
            records.append(check_record(json.loads(query(sql)), entry, profile, seed,
                                        alert_hash, delivery_hash, deliveries))
            print(f'{scenario}: {records[-1]["deliveries"]} deliveries verified', flush=True)
    return records


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
