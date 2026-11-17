"""Validate point-in-interval joins for all synthetic outage scenarios."""
import hashlib
import json
import subprocess
from scripts.load_outage_scenarios import DATA, ROOT, SIZES, inspect
from scripts.verify_multifactor_states import parse, reference


def verify(data, expected_rows, expected_hash, seed, size):
    if not data['scenario'] or not data['manifest'] or not data['deliveries'] or not data['alerts']:
        raise ValueError('Missing scenario, provenance, deliveries or alerts')
    meta=data['manifest']
    if (meta['sha256'],meta['seed'],meta['event_count']) != (expected_hash,seed,size):
        raise AssertionError('Scenario provenance differs from file')
    expected_events=[(r['event_id'],parse(r['started_at']),parse(r['finished_at']),r['source_type']) for r in expected_rows]
    outages=[(i,parse(a),parse(b),k) for i,a,b,k in data['outages']]
    if expected_events!=outages:
        raise AssertionError('Stored outage events differ from file')
    alerts=[(parse(a),parse(b)) for a,b in data['alerts']]
    definition=data['scenario']
    states=reference(parse(definition['window_start']),parse(definition['window_end']),alerts,[(a,b) for _,a,b,_ in outages])
    expected={i:parse(t) for i,t in data['deliveries']}
    results=data['results']
    if len(results)!=len(expected) or len({r[0] for r in results})!=len(expected):
        raise AssertionError('Missing or duplicate delivery results')
    counts={(False,False):0,(True,False):0,(False,True):0,(True,True):0}
    for delivery_id,t,left,right,known,alert,outage in results:
        moment=parse(t)
        if delivery_id not in expected or expected[delivery_id]!=moment or not known:
            raise AssertionError(f'Missing or incorrect state: {delivery_id}')
        matches=[s for s in states if s[0]<=moment<s[1]]
        if len(matches)!=1 or matches[0]!=(parse(left),parse(right),alert,outage):
            raise AssertionError(f'Incorrect interval or flags: {delivery_id}')
        if alert != any(a<=moment<b for a,b in alerts) or outage != any(a<=moment<b for _,a,b,_ in outages):
            raise AssertionError(f'Flags differ from raw events: {delivery_id}')
        counts[(alert,outage)]+=1
    return counts


def main():
    seed,datasets=inspect(DATA)
    source_hash=hashlib.sha256((ROOT/'data/processed/alerts.csv').read_bytes()).hexdigest()
    for size in SIZES:
        scenario,_,digest,rows=datasets[size]
        sql=f"""SELECT json_build_object(
          'scenario',(SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='{scenario}'),
          'manifest',(SELECT row_to_json(m) FROM scenario.synthetic_source_manifest m WHERE scenario_id='{scenario}'),
          'alert_hash',(SELECT b.source_sha256 FROM control.load_batch b WHERE b.source_type='alerts' AND b.status='success' AND b.rows_accepted=(SELECT count(*) FROM core.fact_alert) AND NOT EXISTS (SELECT 1 FROM core.fact_alert a WHERE a.load_id<>b.load_id)),
          'deliveries',(SELECT coalesce(json_agg(json_build_array(d.delivery_id,d.started_at) ORDER BY d.delivery_id),'[]'::json) FROM core.fact_delivery d JOIN scenario.definitions s ON d.region_id=s.region_id WHERE s.scenario_id='{scenario}' AND d.started_at>=s.window_start AND d.started_at<s.window_end),
          'alerts',(SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json) FROM core.fact_alert a JOIN scenario.definitions s ON a.region_id=s.region_id WHERE s.scenario_id='{scenario}' AND a.event_type='Повітряна тривога'),
          'outages',(SELECT coalesce(json_agg(json_build_array(event_id,started_at,finished_at,source_type) ORDER BY event_id),'[]'::json) FROM scenario.outage_events WHERE scenario_id='{scenario}'),
          'results',(SELECT coalesce(json_agg(json_build_array(delivery_id,delivery_started_at,state_started_at,state_finished_at,state_known,alert_active,outage_active) ORDER BY delivery_id),'[]'::json) FROM scenario.delivery_start_state WHERE scenario_id='{scenario}')
        );"""
        cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh','-A','-t','-c',sql]
        result=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True)
        data=json.loads(result.stdout.decode('utf-8'))
        if data['alert_hash']!=source_hash:
            raise AssertionError('Alert source hash does not match the loaded CSV')
        counts=verify(data,rows,digest,seed,size)
        print(f'{scenario}: deliveries={sum(counts.values())}; counts={counts}; differences=0',flush=True)


if __name__=='__main__': main()
