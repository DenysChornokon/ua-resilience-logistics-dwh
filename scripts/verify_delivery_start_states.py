"""Compare delivery-start states with raw events and an independent interval partition."""
import json
import subprocess
from pathlib import Path

from scripts.verify_multifactor_states import parse, reference

ROOT=Path(__file__).resolve().parents[1]
SQL="""SELECT json_build_object(
 'scenario',(SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='kyiv_demo_v1'),
 'deliveries',(SELECT coalesce(json_agg(json_build_array(d.delivery_id,d.started_at) ORDER BY d.delivery_id),'[]'::json)
  FROM core.fact_delivery d JOIN scenario.definitions s ON d.region_id=s.region_id
  WHERE s.scenario_id='kyiv_demo_v1' AND d.started_at>=s.window_start AND d.started_at<s.window_end),
 'alerts',(SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json)
  FROM core.fact_alert a JOIN scenario.definitions s ON a.region_id=s.region_id
  WHERE s.scenario_id='kyiv_demo_v1' AND a.event_type='Повітряна тривога'),
 'outages',(SELECT coalesce(json_agg(json_build_array(started_at,finished_at)),'[]'::json)
  FROM scenario.outage_events WHERE scenario_id='kyiv_demo_v1'),
 'results',(SELECT coalesce(json_agg(json_build_array(delivery_id,delivery_started_at,state_started_at,state_finished_at,state_known,alert_active,outage_active) ORDER BY delivery_id),'[]'::json)
  FROM scenario.delivery_start_state WHERE scenario_id='kyiv_demo_v1')
);"""


def main():
    cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
         '-U','dwh_user','-d','logistics_dwh','-A','-t','-c',SQL]
    proc=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True)
    data=json.loads(proc.stdout.decode('utf-8'))
    if not data['scenario'] or not data['deliveries']:
        raise ValueError('Scenario or in-scope deliveries missing')
    alerts=[(parse(a),parse(b)) for a,b in data['alerts']]
    outages=[(parse(a),parse(b)) for a,b in data['outages']]
    states=reference(parse(data['scenario']['window_start']),parse(data['scenario']['window_end']),alerts,outages)
    expected={delivery_id:parse(t) for delivery_id,t in data['deliveries']}
    results=data['results']
    if len(results)!=len(expected) or len({r[0] for r in results})!=len(expected):
        raise AssertionError('Missing or duplicate delivery results')
    counts={}
    for delivery_id,t,left,right,known,alert,outage in results:
        moment=parse(t)
        if delivery_id not in expected or moment!=expected[delivery_id] or not known:
            raise AssertionError(f'Unknown or mismatched delivery: {delivery_id}')
        matches=[state for state in states if state[0]<=moment<state[1]]
        if len(matches)!=1 or matches[0]!=(parse(left),parse(right),alert,outage):
            raise AssertionError(f'State does not match independent reference: {delivery_id}')
        if alert != any(a<=moment<b for a,b in alerts) or outage != any(a<=moment<b for a,b in outages):
            raise AssertionError(f'Factor flags disagree with raw intervals: {delivery_id}')
        counts[(alert,outage)]=counts.get((alert,outage),0)+1
    print(f'Verified {len(results)} in-scope deliveries against states and source events')
    for key,value in sorted(counts.items()):
        print(f'alert={key[0]}, synthetic_outage={key[1]}: {value}')


if __name__=='__main__':
    main()
