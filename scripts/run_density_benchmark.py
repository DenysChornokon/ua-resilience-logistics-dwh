"""Measure region-state construction across paired synthetic density scenarios."""
import argparse
import csv
import hashlib
import json
import platform
import random
import sys
from datetime import datetime,timezone
from pathlib import Path
from statistics import median

from scripts.load_density_scenarios import DATA, PROFILES, SEEDS, COUNT, inspect as inspect_files
from scripts.run_postgres_benchmark import plan_metrics, query

STATE_SQL="""SELECT json_build_object(
 'view_definition',pg_get_viewdef('scenario.region_state'::regclass,true),
 'alerts_md5',(SELECT md5(string_agg(row_to_json(a)::text,'' ORDER BY a.event_id)) FROM core.fact_alert a),
 'alerts_count',(SELECT count(*) FROM core.fact_alert),
 'load_records',(SELECT json_agg(row_to_json(b) ORDER BY b.load_id) FROM control.load_batch b WHERE b.source_type='alerts'),
 'scenario_data',(SELECT json_agg(json_build_object(
   'scenario_id',s.scenario_id,'definition',row_to_json(s),'manifest',row_to_json(m),
   'outage_count',(SELECT count(*) FROM scenario.outage_events o WHERE o.scenario_id=s.scenario_id),
   'outages_md5',(SELECT md5(string_agg(row_to_json(o)::text,'' ORDER BY o.event_id)) FROM scenario.outage_events o WHERE o.scenario_id=s.scenario_id),
   'state_count',(SELECT count(*) FROM scenario.region_state r WHERE r.scenario_id=s.scenario_id)
  ) ORDER BY s.scenario_id) FROM scenario.definitions s JOIN scenario.density_manifest m USING(scenario_id)
  WHERE m.generator='density_scenarios_v1')
);"""


def validate_state(state,files):
    items=state.get('scenario_data') or []
    if not state.get('view_definition') or not state.get('alerts_md5') or not state.get('alerts_count'):
        raise ValueError('Missing state definition or alerts')
    expected={value[0]:(profile,seed,value) for (profile,seed),value in files.items()}
    found={item['scenario_id']:item for item in items}
    if len(found)!=len(items) or set(found)!=set(expected):
        raise ValueError('Unexpected or missing density scenarios')
    for scenario,item in found.items():
        profile,seed,(_,filename,digest,rows,metrics)=expected[scenario]
        manifest=item['manifest']
        if (manifest['profile'],manifest['seed'],manifest['event_count'],manifest['source_file'],manifest['sha256'],
            manifest['overlap_pairs'],manifest['peak_simultaneous'],manifest['union_seconds']) != (
            profile,seed,COUNT,filename,digest,metrics['overlap_pairs'],metrics['peak_simultaneous'],metrics['union_seconds']):
            raise ValueError(f'Manifest mismatch: {scenario}')
        if item['outage_count']!=COUNT or not item['outages_md5'] or not item['state_count']:
            raise ValueError(f'Missing events or states: {scenario}')
    return found


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=5)
    args=parser.parse_args()
    if args.repetitions<1: parser.error('repetitions must be positive')
    files=inspect_files(DATA)
    before=json.loads(query(STATE_SQL))
    scenarios=validate_state(before,files)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'plans').mkdir()
    meta={'status':'running','started_at_utc':datetime.now(timezone.utc).isoformat(),
          'repetitions':args.repetitions,'warmups_per_scenario':1,'order_seed':20261107,
          'scope':'EXPLAIN ANALYZE SELECT all states for one scenario; excludes client result transfer and data preparation',
          'cache':'preflight and warmup; cache not cleared','memory':'overall process memory not measured',
          'python':sys.version,'platform':platform.platform(),'processor':platform.processor(),
          'postgres_version':query('SELECT version();'),
          'postgres_settings':json.loads(query("SELECT json_object_agg(name, setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
          'input_state':before,'view_definition_sha256':hashlib.sha256(before['view_definition'].encode('utf-8')).hexdigest()}
    path=args.output/'metadata.json'
    def save(): path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    fields=['order','phase','repetition','scenario_id','profile','seed','outage_count','overlap_pairs','state_count',
            'planning_ms','execution_ms','shared_hit_blocks','shared_read_blocks','temp_read_blocks','temp_written_blocks','indexes']
    rng=random.Random(meta['order_seed']); order=0
    try:
        with (args.output/'measurements.csv').open('x',encoding='utf-8',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=fields); writer.writeheader()
            for repetition in range(args.repetitions+1):
                order_scenarios=list(sorted(scenarios)); rng.shuffle(order_scenarios)
                for scenario in order_scenarios:
                    item=scenarios[scenario]; m=item['manifest']; phase='warmup' if repetition==0 else 'measured'
                    sql=f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM scenario.region_state WHERE scenario_id='{scenario}';"
                    plan=json.loads(query(sql)); metrics=plan_metrics(plan[0],item['state_count'])
                    order+=1
                    (args.output/'plans'/f'{order:03d}_{phase}_{scenario}.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    metrics['state_count']=metrics.pop('output_rows')
                    writer.writerow({'order':order,'phase':phase,'repetition':repetition,'scenario_id':scenario,
                                     'profile':m['profile'],'seed':m['seed'],'outage_count':m['event_count'],
                                     'overlap_pairs':m['overlap_pairs'],**metrics})
                    file.flush()
                    print(f"{order}: {phase} {scenario} states={item['state_count']} {metrics['execution_ms']:.3f} ms",flush=True)
        after=json.loads(query(STATE_SQL))
        validate_state(after,inspect_files(DATA))
        if before!=after: raise RuntimeError('Input data or view definition changed during benchmark')
        with (args.output/'measurements.csv').open(encoding='utf-8',newline='') as file:
            rows=list(csv.DictReader(file))
        records=[]
        with (args.output/'summary.csv').open('x',encoding='utf-8',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=['profile','seed','scenario_id','overlap_pairs','state_count','runs','median_ms','min_ms','max_ms']); writer.writeheader()
            for profile in PROFILES:
                for seed in SEEDS:
                    scenario=files[(profile,seed)][0]; item=scenarios[scenario]
                    values=[float(r['execution_ms']) for r in rows if r['phase']=='measured' and r['scenario_id']==scenario]
                    if len(values)!=args.repetitions: raise ValueError('Incomplete measurement series')
                    record={'profile':profile,'seed':seed,'scenario_id':scenario,'overlap_pairs':item['manifest']['overlap_pairs'],
                            'state_count':item['state_count'],'runs':len(values),'median_ms':round(median(values),3),
                            'min_ms':min(values),'max_ms':max(values)}
                    writer.writerow(record); records.append(record)
        with (args.output/'profile_summary.csv').open('x',encoding='utf-8',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=['profile','seeds','median_of_seed_medians_ms','min_seed_median_ms','max_seed_median_ms']); writer.writeheader()
            for profile in PROFILES:
                values=[r['median_ms'] for r in records if r['profile']==profile]
                writer.writerow({'profile':profile,'seeds':len(values),'median_of_seed_medians_ms':round(median(values),3),
                                 'min_seed_median_ms':min(values),'max_seed_median_ms':max(values)})
        meta['status']='complete'; meta['finished_at_utc']=datetime.now(timezone.utc).isoformat(); save()
    except BaseException as exc:
        meta['status']='failed'; meta['error']=str(exc); save(); raise


if __name__=='__main__': main()
