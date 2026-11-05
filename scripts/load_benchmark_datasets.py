"""Load the three generated benchmark CSVs into isolated PostgreSQL tables."""
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/synthetic/benchmark'
SIZES = (10000, 100000, 1000000)
FIELDS = ['delivery_id', 'region_id', 'started_at', 'finished_at', 'distance_km', 'status']


def inspect_sources(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('generator') != 'generate_benchmark_deliveries_v1' or manifest.get('synthetic') is not True:
        raise ValueError('Unexpected generator or dataset type')
    seed = manifest.get('seed')
    if type(seed) is not int or not -(2**63) <= seed < 2**63:
        raise ValueError('Invalid seed')
    items = manifest.get('datasets')
    if not isinstance(items, list) or len(items) != len(SIZES):
        raise ValueError('Manifest must contain exactly three datasets')
    found = {}
    for item in items:
        size = item.get('rows')
        if type(size) is not int or size not in SIZES or size in found:
            raise ValueError('Unexpected or duplicate dataset size')
        if item.get('file') != f'deliveries_{size}.csv':
            raise ValueError('Unexpected dataset filename')
        digest = item.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid SHA-256')
        path = directory / item['file']
        with path.open('rb') as f:
            actual = hashlib.file_digest(f, 'sha256').hexdigest()
        if actual != digest or path.stat().st_size != item.get('bytes'):
            raise ValueError(f'Checksum or size mismatch: {path.name}')
        with path.open(encoding='utf-8', newline='') as f:
            if next(csv.reader(f), None) != FIELDS:
                raise ValueError(f'Unexpected CSV header: {path.name}')
        found[size] = (path, digest)
    return seed, found


def table_sql(size):
    return f'''CREATE TABLE benchmark.deliveries_{size} (
 delivery_id text PRIMARY KEY CHECK (delivery_id ~ '^B-[0-9]{{8}}$'),
 region_id text NOT NULL CHECK (region_id = 'UA80000000000093317'),
 started_at timestamptz NOT NULL, finished_at timestamptz NOT NULL,
 distance_km numeric NOT NULL CHECK (distance_km BETWEEN 0.5 AND 20),
 status text NOT NULL CHECK (status IN ('completed','delayed','cancelled')),
 CHECK (started_at >= '2026-07-01 00:00:00+00'::timestamptz),
 CHECK (finished_at <= '2026-10-01 00:00:00+00'::timestamptz),
 CHECK (finished_at > started_at),
 CHECK (finished_at - started_at BETWEEN interval '15 minutes' AND interval '90 minutes')
);\n'''


def load(directory=DATA):
    seed, datasets = inspect_sources(directory)
    cmd = ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
           '-U','dwh_user','-d','logistics_dwh']
    proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        def send(text): proc.stdin.write(text.encode('utf-8'))
        send("\\set ON_ERROR_STOP on\nBEGIN;\nSELECT pg_advisory_xact_lock(82991352);\nCREATE SCHEMA IF NOT EXISTS benchmark;\n")
        send('CREATE TABLE benchmark.dataset_manifest (rows_expected integer PRIMARY KEY, source_file text NOT NULL, sha256 text NOT NULL, seed bigint NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now());\n')
        for size in SIZES:
            path, digest = datasets[size]
            send(table_sql(size))
            send(f"COPY benchmark.deliveries_{size} ({','.join(FIELDS)}) FROM STDIN WITH (FORMAT csv, HEADER true);\n")
            # COPY input is streamed: avoid buffering a 98 MB CSV in memory.
            streamed_hash = hashlib.sha256()
            last_byte = b''
            with path.open('rb') as f:
                while True:
                    block = f.read(1024 * 1024)
                    if not block: break
                    streamed_hash.update(block)
                    last_byte = block[-1:]
                    proc.stdin.write(block)
            if streamed_hash.hexdigest() != digest or last_byte != b'\n':
                raise ValueError(f'File changed during loading or lacks final newline: {path.name}')
            send('\\.\n')
            send(f"DO $$ BEGIN IF (SELECT count(*) FROM benchmark.deliveries_{size}) <> {size} THEN RAISE EXCEPTION 'Wrong row count for {size}'; END IF; END $$;\n")
            send(f"INSERT INTO benchmark.dataset_manifest(rows_expected,source_file,sha256,seed) VALUES ({size},'deliveries_{size}.csv','{digest}',{seed});\n")
            send(f'ANALYZE benchmark.deliveries_{size};\n')
        send('COMMIT;\n')
        proc.stdin.close()
        proc.stdin = None
        stdout, stderr = proc.communicate()
    except BrokenPipeError as exc:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        proc.stdin = None
        _, stderr = proc.communicate()
        detail = stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError('PostgreSQL stopped loading; transaction rolled back: ' + detail) from exc
    except BaseException:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        proc.stdin = None
        stdout, stderr = proc.communicate()
        if stderr:
            print(stderr.decode('utf-8', errors='replace'), file=sys.stderr)
        raise
    print(stdout.decode('utf-8', errors='replace'), end='')
    if proc.returncode:
        raise RuntimeError('PostgreSQL load failed; transaction rolled back: ' + stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    try:
        load()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f'Benchmark load failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
