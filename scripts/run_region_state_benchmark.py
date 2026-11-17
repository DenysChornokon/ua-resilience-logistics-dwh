"""Measure dynamic region-state construction for three synthetic scenarios."""
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

from scripts.run_postgres_benchmark import plan_metrics, query

SCENARIOS=('kyiv_synthetic_0050_v1','kyiv_synthetic_0250_v1','kyiv_synthetic_1000_v1')
EXPECTED=(50,250,1000)
STATE_SQL="""SELECT json_build_object(
 'view_definition',pg_get_viewdef('scenario.region_state'::regclass,true),
 'alerts_md5',(SELECT md5(string_agg(row_to_json(a)::text,'' ORDER BY a.event_id)) FROM core.fact_alert a),
 'alerts_count',(SELECT count(*) FROM core.fact_alert),
 'scenario_data',(SELECT json_agg(json_build_object(
   'scenario_id',s.scenario_id,'definition',row_to_json(s),'manifest',row_to_json(m),
   'outage_count',(SELECT count(*) FROM scenario.outage_events o WHERE o.scenario_id=s.scenario_id),
   'outages_md5',(SELECT md5(string_agg(row_to_json(o)::text,'' ORDER BY o.event_id)) FROM scenario.outage_events o WHERE o.scenario_id=s.scenario_id),
   'state_count',(SELECT count(*) FROM scenario.region_state r WHERE r.scenario_id=s.scenario_id)
  ) ORDER BY s.scenario_id) FROM scenario.definitions s JOIN scenario.synthetic_source_manifest m USING(scenario_id)
  WHERE s.scenario_id IN ('kyiv_synthetic_0050_v1','kyiv_synthetic_0250_v1','kyiv_synthetic_1000_v1')),
 'load_records',(SELECT json_agg(row_to_json(b) ORDER BY b.load_id) FROM control.load_batch b WHERE b.source_type='alerts')
);"""


def inspect():
    state=json.loads(query(STATE_SQL))
    scenarios=state['scenario_data'] or []
    if [s['scenario_id'] for s in scenarios]!=list(SCENARIOS) or [s['outage_count'] for s in scenarios]!=list(EXPECTED):
        raise ValueError('Missing or unexpected scenario records')
    if any(not s['state_count'] or s['manifest']['event_count']!=s['outage_count'] for s in scenarios):
        raise ValueError('Missing region states or inconsistent manifest')
    if not state['alerts_count'] or not state['alerts_md5'] or not state['view_definition']:
        raise ValueError('Missing alert data or state view')
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=5)
    args=parser.parse_args()
    if args.repetitions<1: parser.error('repetitions must be positive')
    before=inspect()
    expected={s['scenario_id']:s['state_count'] for s in before['scenario_data']}
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'plans').mkdir()
    metadata={'status':'running','started_at_utc':datetime.now(timezone.utc).isoformat(),
        'repetitions':args.repetitions,'warmups_per_scenario':1,'order_seed':20261106,
        'scope':'EXPLAIN ANALYZE SELECT all states for one scenario; excludes client result transfer and data preparation',
        'cache':'preflight and warmup; cache not cleared','memory':'overall process memory not measured',
        'python':sys.version,'platform':platform.platform(),'processor':platform.processor(),
        'postgres_version':query('SELECT version();'),
        'postgres_settings':json.loads(query("SELECT json_object_agg(name, setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
        'input_state':before,
        'view_definition_sha256':hashlib.sha256(before['view_definition'].encode('utf-8')).hexdigest()}
    meta_path=args.output/'metadata.json'
    def save(): meta_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    fields=['order','phase','repetition','scenario_id','outage_count','state_count','planning_ms','execution_ms',
            'shared_hit_blocks','shared_read_blocks','temp_read_blocks','temp_written_blocks','indexes']
    rng=random.Random(metadata['order_seed']); order=0
    try:
        with (args.output/'measurements.csv').open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
            for rep in range(args.repetitions+1):
                cases=list(zip(SCENARIOS,EXPECTED)); rng.shuffle(cases)
                for scenario,n in cases:
                    phase='warmup' if rep==0 else 'measured'
                    sql=f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM scenario.region_state WHERE scenario_id='{scenario}';"
                    plan=json.loads(query(sql)); metrics=plan_metrics(plan[0],expected[scenario])
                    order+=1
                    (args.output/'plans'/f'{order:03d}_{phase}_{n}.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    metrics['state_count']=metrics.pop('output_rows')
                    writer.writerow({'order':order,'phase':phase,'repetition':rep,'scenario_id':scenario,'outage_count':n,**metrics})
                    f.flush()
                    print(f"{order}: {phase} outages={n} states={expected[scenario]} {metrics['execution_ms']:.3f} ms",flush=True)
        if inspect()!=before:
            raise RuntimeError('Input records or view definition changed during measurement')
        with (args.output/'measurements.csv').open(encoding='utf-8',newline='') as f:
            rows=list(csv.DictReader(f))
        with (args.output/'summary.csv').open('x',encoding='utf-8',newline='') as f:
            fields=['scenario_id','outage_count','state_count','runs','median_ms','min_ms','max_ms']
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
            for scenario,n in zip(SCENARIOS,EXPECTED):
                values=[float(r['execution_ms']) for r in rows if r['scenario_id']==scenario and r['phase']=='measured']
                writer.writerow({'scenario_id':scenario,'outage_count':n,'state_count':expected[scenario],'runs':len(values),
                                 'median_ms':round(median(values),3),'min_ms':min(values),'max_ms':max(values)})
        metadata['status']='complete'; metadata['finished_at_utc']=datetime.now(timezone.utc).isoformat(); save()
    except BaseException as exc:
        metadata['status']='failed'; metadata['error']=str(exc); save(); raise


if __name__=='__main__': main()
