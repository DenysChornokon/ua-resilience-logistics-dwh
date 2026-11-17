"""Create reproducible synthetic outage scenarios without overwriting data."""
import argparse
import csv
import hashlib
import json
import platform
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
START=datetime(2026,7,1,tzinfo=timezone.utc)
END=datetime(2026,10,1,tzinfo=timezone.utc)
REGION='UA80000000000093317'
SIZES=(50,250,1000)
FIELDS=['scenario_id','event_id','started_at','finished_at','source_type']


def generate(directory, seed=42):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    paths=[directory/f'outages_{n}.csv' for n in SIZES]
    manifest=directory/'manifest.json'
    if any(p.exists() for p in [*paths,manifest]):
        raise FileExistsError('Choose an empty output directory; files are not overwritten')
    created=[]; streams=[]
    try:
        writers=[]
        for path in paths:
            f=path.open('x',encoding='utf-8',newline=''); streams.append(f); created.append(path)
            writer=csv.writer(f,lineterminator='\n'); writer.writerow(FIELDS); writers.append(writer)
        rng=random.Random(seed)
        seconds=int((END-START).total_seconds())
        for i in range(1,max(SIZES)+1):
            duration=rng.randint(1800,14400)
            start=START+timedelta(seconds=rng.randint(0,seconds-duration))
            end=start+timedelta(seconds=duration)
            for count,writer in zip(SIZES,writers):
                if i<=count:
                    writer.writerow([f'kyiv_synthetic_{count:04d}_v1',f'syn-out-{i:05d}',
                                     start.isoformat(),end.isoformat(),'synthetic'])
        for f in streams: f.close()
        meta={'generator':'outage_scenarios_v1','seed':seed,'python':platform.python_version(),
              'region_id':REGION,'window_start':START.isoformat(),'window_end':END.isoformat(),
              'source_type':'synthetic','generation':'independent uniform start and duration 30–240 minutes; nested prefixes',
              'datasets':[]}
        for n,path in zip(SIZES,paths):
            with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            meta['datasets'].append({'scenario_id':f'kyiv_synthetic_{n:04d}_v1','file':path.name,'rows':n,'sha256':digest})
        with manifest.open('x',encoding='utf-8',newline='\n') as f:
            created.append(manifest); json.dump(meta,f,ensure_ascii=False,indent=2); f.write('\n')
        return meta
    except BaseException:
        for f in streams: f.close()
        for path in created: path.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/synthetic/outage_scenarios')
    parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args()
    for item in generate(args.output_dir,args.seed)['datasets']:
        print(f"{item['scenario_id']}: {item['rows']} events; sha256={item['sha256']}")


if __name__=='__main__': main()
