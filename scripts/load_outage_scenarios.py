"""Validate and atomically import synthetic outage scenarios into PostgreSQL."""
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from scripts.generate_outage_scenarios import ROOT, START, END, REGION, SIZES, FIELDS
from scripts.verify_multifactor_states import parse

DATA=ROOT/'data/synthetic/outage_scenarios'


def inspect(directory):
    directory=Path(directory)
    meta=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if (meta.get('generator')!='outage_scenarios_v1' or meta.get('source_type')!='synthetic'
        or meta.get('region_id')!=REGION or type(meta.get('seed')) is not int
        or not -(2**63)<=meta['seed']<2**63
        or meta.get('window_start')!=START.isoformat() or meta.get('window_end')!=END.isoformat()):
        raise ValueError('Invalid scenario manifest')
    entries=meta.get('datasets')
    if not isinstance(entries,list) or len(entries)!=len(SIZES): raise ValueError('Expected three scenarios')
    datasets={}
    for entry in entries:
        count=entry.get('rows')
        if type(count) is not int or count not in SIZES or count in datasets: raise ValueError('Invalid or duplicate size')
        scenario=f'kyiv_synthetic_{count:04d}_v1'; filename=f'outages_{count}.csv'
        digest=entry.get('sha256')
        if entry.get('scenario_id')!=scenario or entry.get('file')!=filename or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Invalid scenario identity or checksum')
        raw=(directory/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError(f'Checksum mismatch: {filename}')
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline=''),strict=True)
        if reader.fieldnames!=FIELDS: raise ValueError(f'Invalid header: {filename}')
        rows=list(reader)
        if len(rows)!=count: raise ValueError(f'Wrong row count: {filename}')
        for i,row in enumerate(rows,1):
            if None in row or any(row.get(field) is None or '\n' in row[field] or '\r' in row[field] for field in FIELDS): raise ValueError(f'Invalid row {i}: {filename}')
            if row['scenario_id']!=scenario or row['event_id']!=f'syn-out-{i:05d}' or row['source_type']!='synthetic': raise ValueError(f'Invalid identity at row {i}: {filename}')
            left,right=parse(row['started_at']),parse(row['finished_at'])
            if not (START<=left<right<=END) or not 1800<=(right-left).total_seconds()<=14400: raise ValueError(f'Invalid interval at row {i}: {filename}')
        datasets[count]=(scenario,filename,digest,rows)
    return meta['seed'],datasets


def build_sql(seed,datasets):
    parts=["\\set ON_ERROR_STOP on\nBEGIN;\nSELECT pg_advisory_xact_lock(82991353);\n",
      "DO $$ BEGIN IF EXISTS (SELECT 1 FROM scenario.definitions WHERE scenario_id IN ('kyiv_synthetic_0050_v1','kyiv_synthetic_0250_v1','kyiv_synthetic_1000_v1')) THEN RAISE EXCEPTION 'Scenario already exists; existing data will not be replaced'; END IF; END $$;\n",
      "CREATE TABLE IF NOT EXISTS scenario.synthetic_source_manifest (scenario_id text PRIMARY KEY REFERENCES scenario.definitions(scenario_id), source_file text NOT NULL, sha256 text NOT NULL, seed bigint NOT NULL, event_count integer NOT NULL, generator text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now());\n"]
    for n in SIZES:
        scenario,filename,digest,rows=datasets[n]
        parts.append(f"INSERT INTO scenario.definitions VALUES ('{scenario}','{REGION}','{START.isoformat()}','{END.isoformat()}','Synthetic random outages; independent of alerts; n={n}');\n")
        parts.append('COPY scenario.outage_events (scenario_id,event_id,started_at,finished_at,source_type) FROM STDIN WITH (FORMAT csv);\n')
        buf=io.StringIO(newline=''); writer=csv.DictWriter(buf,fieldnames=FIELDS,lineterminator='\n'); writer.writerows(rows)
        parts.append(buf.getvalue()+'\\.\n')
        parts.append(f"INSERT INTO scenario.synthetic_source_manifest(scenario_id,source_file,sha256,seed,event_count,generator) VALUES ('{scenario}','{filename}','{digest}',{seed},{n},'outage_scenarios_v1');\n")
        parts.append(f"DO $$ BEGIN IF (SELECT count(*) FROM scenario.outage_events WHERE scenario_id='{scenario}') <> {n} THEN RAISE EXCEPTION 'Incorrect event count for {scenario}'; END IF; END $$;\n")
    parts.append('COMMIT;\n')
    return ''.join(parts)


def main():
    seed,datasets=inspect(DATA)
    cmd=['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh']
    result=subprocess.run(cmd,cwd=ROOT,input=build_sql(seed,datasets).encode('utf-8'),capture_output=True)
    if result.returncode: raise RuntimeError('Import failed and transaction rolled back: '+result.stderr.decode('utf-8',errors='replace'))
    print(result.stdout.decode('utf-8',errors='replace'),end='')


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'Scenario load failed: {exc}',file=sys.stderr)
        raise SystemExit(1)
