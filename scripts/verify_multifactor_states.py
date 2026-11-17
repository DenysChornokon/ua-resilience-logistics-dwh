"""Independently verify every segment of the synthetic scenario against source intervals."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = 'kyiv_demo_v1'
SQL = """SELECT json_build_object(
 'scenario', (SELECT row_to_json(s) FROM scenario.definitions s WHERE scenario_id='kyiv_demo_v1'),
 'alerts', (SELECT coalesce(json_agg(json_build_array(a.started_at,a.finished_at)),'[]'::json)
            FROM core.fact_alert a JOIN scenario.definitions s ON a.region_id=s.region_id
            WHERE s.scenario_id='kyiv_demo_v1' AND a.event_type='Повітряна тривога'),
 'outages', (SELECT coalesce(json_agg(json_build_array(started_at,finished_at)),'[]'::json)
             FROM scenario.outage_events WHERE scenario_id='kyiv_demo_v1'),
 'states', (SELECT coalesce(json_agg(json_build_array(started_at,finished_at,alert_active,outage_active) ORDER BY started_at),'[]'::json)
            FROM scenario.region_state WHERE scenario_id='kyiv_demo_v1')
);"""


def parse(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Expected timezone-aware timestamp')
    return result


def reference(start, end, alerts, outages):
    """Partition at every boundary, check each segment independently, merge equal neighbours."""
    if start >= end:
        raise ValueError('Invalid scenario window')
    clipped = []
    for factor, rows in (('alert',alerts),('outage',outages)):
        for left,right in rows:
            if left >= right:
                raise ValueError('Invalid event interval')
            if left < end and right > start:
                clipped.append((max(start,left),min(end,right),factor))
    boundaries = sorted({start,end,*(x for left,right,_ in clipped for x in (left,right))})
    result = []
    for left,right in zip(boundaries,boundaries[1:]):
        alert = any(a <= left < b and f=='alert' for a,b,f in clipped)
        outage = any(a <= left < b and f=='outage' for a,b,f in clipped)
        if result and result[-1][1]==left and result[-1][2:]==(alert,outage):
            result[-1] = (result[-1][0],right,alert,outage)
        else:
            result.append((left,right,alert,outage))
    return result


def main():
    cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
         '-U','dwh_user','-d','logistics_dwh','-A','-t','-c',SQL]
    result=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True)
    data=json.loads(result.stdout.decode('utf-8'))
    if not data['scenario'] or not data['states']:
        raise ValueError('Scenario or validated alert source is missing')
    start,end=parse(data['scenario']['window_start']),parse(data['scenario']['window_end'])
    alerts=[(parse(a),parse(b)) for a,b in data['alerts']]
    outages=[(parse(a),parse(b)) for a,b in data['outages']]
    expected=reference(start,end,alerts,outages)
    actual=[(parse(a),parse(b),x,y) for a,b,x,y in data['states']]
    if expected!=actual:
        raise AssertionError('State intervals or factor flags differ from independent reference')
    print(f'Verified {len(actual)} states, {len(alerts)} source alerts and {len(outages)} synthetic outages')
    for label, predicate in [('alert',lambda a,o:a),('outage',lambda a,o:o),('both',lambda a,o:a and o)]:
        seconds=sum((b-a).total_seconds() for a,b,x,y in actual if predicate(x,y))
        print(f'{label}: {seconds:.0f} seconds')


if __name__=='__main__':
    main()
