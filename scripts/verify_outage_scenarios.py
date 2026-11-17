"""Verify each stored synthetic event and state against source files and Python."""
import json
import subprocess
from scripts.load_outage_scenarios import DATA, ROOT, SIZES, inspect
from scripts.verify_multifactor_states import parse, reference


def main():
    seed,datasets=inspect(DATA)
    for size in SIZES:
        scenario,filename,digest,rows=datasets[size]
        sql=f"""SELECT json_build_object(
         'scenario',(SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='{scenario}'),
         'manifest',(SELECT row_to_json(m) FROM scenario.synthetic_source_manifest m WHERE scenario_id='{scenario}'),
         'alerts',(SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json)
                   FROM core.fact_alert a JOIN scenario.definitions s ON s.region_id=a.region_id
                   WHERE s.scenario_id='{scenario}' AND a.event_type='Повітряна тривога'),
         'outages',(SELECT coalesce(json_agg(json_build_array(event_id,started_at,finished_at,source_type) ORDER BY event_id),'[]'::json)
                    FROM scenario.outage_events WHERE scenario_id='{scenario}'),
         'states',(SELECT coalesce(json_agg(json_build_array(started_at,finished_at,alert_active,outage_active) ORDER BY started_at),'[]'::json)
                   FROM scenario.region_state WHERE scenario_id='{scenario}')
        );"""
        command=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
                 '-U','dwh_user','-d','logistics_dwh','-A','-t','-c',sql]
        result=subprocess.run(command,cwd=ROOT,capture_output=True,check=True)
        data=json.loads(result.stdout.decode('utf-8'))
        if not data['scenario'] or not data['manifest'] or not data['states']:
            raise AssertionError(f'Missing data for {scenario}')
        manifest=data['manifest']
        if (manifest['sha256'],manifest['seed'],manifest['event_count'],manifest['source_file']) != (digest,seed,size,filename):
            raise AssertionError(f'Provenance mismatch: {scenario}')
        expected_events=[(r['event_id'],parse(r['started_at']),parse(r['finished_at']),r['source_type']) for r in rows]
        actual_events=[(i,parse(a),parse(b),kind) for i,a,b,kind in data['outages']]
        if expected_events != actual_events: raise AssertionError(f'Event mismatch: {scenario}')
        alerts=[(parse(a),parse(b)) for a,b in data['alerts']]
        outages=[(a,b) for _,a,b,_ in actual_events]
        state_data=data['scenario']
        expected=reference(parse(state_data['window_start']),parse(state_data['window_end']),alerts,outages)
        actual=[(parse(a),parse(b),x,y) for a,b,x,y in data['states']]
        if expected!=actual: raise AssertionError(f'State mismatch: {scenario}')
        print(f'{scenario}: {size} events, {len(actual)} states, all rows match independent reference',flush=True)


if __name__=='__main__': main()
