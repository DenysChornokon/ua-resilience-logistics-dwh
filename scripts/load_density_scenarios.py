"""Validate and atomically load synthetic density scenarios without overwrites."""
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from scripts.generate_density_scenarios import (ROOT, START, END, REGION, SEEDS, PROFILES,
    COUNT, FIELDS, events, overlap_metrics)

DATA=ROOT/'data/synthetic/density_scenarios'


def inspect(directory):
    directory=Path(directory); meta=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if (meta.get('generator')!='density_scenarios_v1' or meta.get('source_type')!='synthetic'
        or meta.get('region_id')!=REGION or meta.get('window_start')!=START.isoformat()
        or meta.get('window_end')!=END.isoformat()): raise ValueError('Invalid manifest')
    entries=meta.get('datasets')
    if not isinstance(entries,list) or len(entries)!=len(SEEDS)*len(PROFILES): raise ValueError('Expected nine scenarios')
    result={}
    for entry in entries:
        profile,seed=entry.get('profile'),entry.get('seed')
        if profile not in PROFILES or type(seed) is not int or seed not in SEEDS or (profile,seed) in result: raise ValueError('Invalid profile or seed')
        scenario=f'density_{profile}_s{seed}_v1'; filename=f'{scenario}.csv'; digest=entry.get('sha256')
        if (entry.get('scenario_id')!=scenario or entry.get('file')!=filename or entry.get('rows')!=COUNT
            or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)): raise ValueError('Invalid identity or hash')
        raw=(directory/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError(f'Checksum mismatch: {filename}')
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline=''),strict=True)
        if tuple(reader.fieldnames or ())!=FIELDS: raise ValueError('Invalid CSV header')
        rows=list(reader); expected=events(profile,seed)
        if len(rows)!=COUNT: raise ValueError('Invalid row count')
        for i,(row,(start,end)) in enumerate(zip(rows,expected),1):
            if None in row or any(row.get(field) is None for field in FIELDS): raise ValueError('Invalid CSV row')
            if row != {'scenario_id':scenario,'event_id':f'syn-{i:05d}',
                       'started_at':start.isoformat(),'finished_at':end.isoformat(),'source_type':'synthetic'}:
                raise ValueError(f'Row {i} differs from reproducible generator')
        if entry.get('metrics')!=overlap_metrics(expected): raise ValueError('Overlap metrics mismatch')
        result[(profile,seed)]=(scenario,filename,digest,rows,entry['metrics'])
    return result


def build_sql(datasets):
    ids=[datasets[(p,s)][0] for p in PROFILES for s in SEEDS]
    ids_sql=','.join("'"+x+"'" for x in ids)
    parts=["\\set ON_ERROR_STOP on\nBEGIN;\nSELECT pg_advisory_xact_lock(82991353);\n",
      f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM scenario.definitions WHERE scenario_id IN ({ids_sql})) THEN RAISE EXCEPTION 'Density scenario already exists; no data replaced'; END IF; END $$;\n",
      "CREATE TABLE IF NOT EXISTS scenario.density_manifest (scenario_id text PRIMARY KEY REFERENCES scenario.definitions(scenario_id), source_file text NOT NULL, sha256 text NOT NULL, seed integer NOT NULL, profile text NOT NULL, event_count integer NOT NULL, overlap_pairs bigint NOT NULL, peak_simultaneous integer NOT NULL, union_seconds bigint NOT NULL, generator text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now());\n"]
    for profile in PROFILES:
        for seed in SEEDS:
            scenario,filename,digest,rows,metrics=datasets[(profile,seed)]
            parts.append(f"INSERT INTO scenario.definitions VALUES ('{scenario}','{REGION}','{START.isoformat()}','{END.isoformat()}','Synthetic density scenario: {profile}, seed {seed}');\n")
            parts.append('COPY scenario.outage_events (scenario_id,event_id,started_at,finished_at,source_type) FROM STDIN WITH (FORMAT csv);\n')
            buf=io.StringIO(newline=''); w=csv.DictWriter(buf,fieldnames=FIELDS,lineterminator='\n'); w.writerows(rows)
            parts.append(buf.getvalue()+'\\.\n')
            parts.append(f"INSERT INTO scenario.density_manifest(scenario_id,source_file,sha256,seed,profile,event_count,overlap_pairs,peak_simultaneous,union_seconds,generator) VALUES ('{scenario}','{filename}','{digest}',{seed},'{profile}',{COUNT},{metrics['overlap_pairs']},{metrics['peak_simultaneous']},{metrics['union_seconds']},'density_scenarios_v1');\n")
            parts.append(f"DO $$ BEGIN IF (SELECT count(*) FROM scenario.outage_events WHERE scenario_id='{scenario}') <> {COUNT} THEN RAISE EXCEPTION 'Incorrect event count'; END IF; END $$;\n")
    parts.append('COMMIT;\n')
    return ''.join(parts)


def main():
    datasets=inspect(DATA)
    cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh']
    result=subprocess.run(cmd,cwd=ROOT,input=build_sql(datasets).encode('utf-8'),capture_output=True)
    if result.returncode: raise RuntimeError('Import failed; transaction rolled back: '+result.stderr.decode('utf-8',errors='replace'))
    print(result.stdout.decode('utf-8',errors='replace'),end='')


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'Density load failed: {exc}',file=sys.stderr); raise SystemExit(1)
