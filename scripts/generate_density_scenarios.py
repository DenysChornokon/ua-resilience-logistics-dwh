"""Generate paired synthetic outage scenarios with different overlap profiles."""
import argparse
import csv
import hashlib
import heapq
import json
import platform
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
START=datetime(2026,7,1,tzinfo=timezone.utc)
END=datetime(2026,10,1,tzinfo=timezone.utc)
REGION='UA80000000000093317'
SEEDS=(41,42,43)
PROFILES=('sparse','uniform','clustered')
COUNT=250
FIELDS=('scenario_id','event_id','started_at','finished_at','source_type')


def overlap_metrics(intervals):
    events=sorted(intervals)
    active=[]; pairs=0; peak=0; union=0; left=right=None
    for start,end in events:
        if start>=end: raise ValueError('Invalid interval')
        while active and active[0]<=start: heapq.heappop(active)
        pairs+=len(active); heapq.heappush(active,end); peak=max(peak,len(active))
        if right is None: left,right=start,end
        elif start<=right: right=max(right,end)
        else: union+=(right-left).total_seconds(); left,right=start,end
    if right is not None: union+=(right-left).total_seconds()
    return {'overlap_pairs':pairs,'peak_simultaneous':peak,'union_seconds':int(union)}


def events(profile,seed):
    if profile not in PROFILES or seed not in SEEDS: raise ValueError('Unknown profile or seed')
    lengths=random.Random(seed).choices(range(1800,7201),k=COUNT)
    rng=random.Random(f'density-v1:{profile}:{seed}')
    if profile=='sparse':
        # Distinct 2-hour slots guarantee non-overlap, with duration <= 2 hours.
        slots=rng.sample(range(int((END-START).total_seconds())//7200),COUNT)
        offsets=[slot*7200 for slot in slots]
    elif profile=='uniform':
        total=int((END-START).total_seconds())
        offsets=[rng.randint(0,total-duration) for duration in lengths]
    else:
        # Ten independent clusters over the period, each spanning at most three hours.
        offsets=[(i%10)*9*86400+rng.randint(0,10800) for i in range(COUNT)]
    return [(START+timedelta(seconds=o), START+timedelta(seconds=o+d)) for o,d in zip(offsets,lengths)]


def generate(directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    paths=[directory/f'density_{profile}_s{seed}_v1.csv' for profile in PROFILES for seed in SEEDS]
    manifest=directory/'manifest.json'
    if any(p.exists() for p in [*paths,manifest]): raise FileExistsError('Choose an empty output directory')
    created=[]
    try:
        entries=[]
        for profile in PROFILES:
            for seed in SEEDS:
                scenario=f'density_{profile}_s{seed}_v1'; path=directory/f'{scenario}.csv'; intervals=events(profile,seed)
                with path.open('x',encoding='utf-8',newline='') as f:
                    created.append(path); writer=csv.writer(f,lineterminator='\n'); writer.writerow(FIELDS)
                    for i,(start,end) in enumerate(intervals,1):
                        writer.writerow([scenario,f'syn-{i:05d}',start.isoformat(),end.isoformat(),'synthetic'])
                with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
                entries.append({'scenario_id':scenario,'file':path.name,'profile':profile,'seed':seed,
                                'rows':COUNT,'sha256':digest,'metrics':overlap_metrics(intervals)})
        meta={'generator':'density_scenarios_v1','source_type':'synthetic','python':platform.python_version(),
              'region_id':REGION,'window_start':START.isoformat(),'window_end':END.isoformat(),
              'note':'Same per-event durations within each seed across profiles; positions vary by profile.',
              'datasets':entries}
        with manifest.open('x',encoding='utf-8',newline='\n') as f:
            created.append(manifest); json.dump(meta,f,ensure_ascii=False,indent=2); f.write('\n')
        return meta
    except BaseException:
        for path in created: path.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/synthetic/density_scenarios')
    args=parser.parse_args()
    for item in generate(args.output_dir)['datasets']:
        print(item['scenario_id'],item['metrics'],flush=True)


if __name__=='__main__': main()
