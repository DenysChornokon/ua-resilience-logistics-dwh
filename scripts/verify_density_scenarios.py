"""Compare stored events and every state with source files and Python reference."""
import hashlib
import json
import subprocess
from scripts.load_density_scenarios import DATA, ROOT, PROFILES, SEEDS, START, END, REGION, inspect
from scripts.verify_multifactor_states import parse, reference


def main():
    data=inspect(DATA)
    alert_hash=hashlib.sha256((ROOT/'data/processed/alerts.csv').read_bytes()).hexdigest()
    for profile in PROFILES:
        for seed in SEEDS:
            scenario,filename,digest,rows,metrics=data[(profile,seed)]
            sql=f"""SELECT json_build_object(
             'scenario',(SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='{scenario}'),
             'manifest',(SELECT row_to_json(m) FROM scenario.density_manifest m WHERE scenario_id='{scenario}'),
             'alert_hash',(SELECT b.source_sha256 FROM control.load_batch b WHERE b.source_type='alerts' AND b.status='success' AND b.rows_accepted=(SELECT count(*) FROM core.fact_alert) AND NOT EXISTS (SELECT 1 FROM core.fact_alert a WHERE a.load_id<>b.load_id)),
             'alerts',(SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json) FROM core.fact_alert a JOIN scenario.definitions s ON a.region_id=s.region_id WHERE s.scenario_id='{scenario}' AND a.event_type='Повітряна тривога'),
             'outages',(SELECT coalesce(json_agg(json_build_array(event_id,started_at,finished_at,source_type) ORDER BY event_id),'[]'::json) FROM scenario.outage_events WHERE scenario_id='{scenario}'),
             'states',(SELECT coalesce(json_agg(json_build_array(started_at,finished_at,alert_active,outage_active) ORDER BY started_at),'[]'::json) FROM scenario.region_state WHERE scenario_id='{scenario}')
            );"""
            cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh','-A','-t','-c',sql]
            result=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True)
            record=json.loads(result.stdout.decode('utf-8'))
            if not record['scenario'] or not record['manifest'] or not record['states'] or record['alert_hash']!=alert_hash:
                raise AssertionError(f'Missing scenario, state or mismatched alerts: {scenario}')
            definition=record['scenario']
            if (definition['region_id'],parse(definition['window_start']),parse(definition['window_end'])) != (REGION,START,END) or not record['alerts']:
                raise AssertionError(f'Scenario definition or alert coverage mismatch: {scenario}')
            m=record['manifest']
            if (m['source_file'],m['sha256'],m['seed'],m['profile'],m['event_count'],m['overlap_pairs'],m['peak_simultaneous'],m['union_seconds']) != (filename,digest,seed,profile,len(rows),metrics['overlap_pairs'],metrics['peak_simultaneous'],metrics['union_seconds']):
                raise AssertionError(f'Manifest mismatch: {scenario}')
            expected_events=[(r['event_id'],parse(r['started_at']),parse(r['finished_at']),r['source_type']) for r in rows]
            actual_events=[(i,parse(a),parse(b),kind) for i,a,b,kind in record['outages']]
            if expected_events!=actual_events: raise AssertionError(f'Event mismatch: {scenario}')
            alert_intervals=[(parse(a),parse(b)) for a,b in record['alerts']]
            intervals=[(a,b) for _,a,b,_ in actual_events]
            expected=reference(parse(definition['window_start']),parse(definition['window_end']),alert_intervals,intervals)
            actual=[(parse(a),parse(b),x,y) for a,b,x,y in record['states']]
            if expected!=actual: raise AssertionError(f'State mismatch: {scenario}')
            print(f"{scenario}: {len(actual_events)} events, {len(actual)} states, overlap_pairs={metrics['overlap_pairs']}; verified",flush=True)


if __name__=='__main__': main()
