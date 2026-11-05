"""Generate nested, reproducible synthetic CSV datasets without overwriting files."""
import argparse
import csv
import hashlib
import json
import platform
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, tzinfo=timezone.utc)
REGION = 'UA80000000000093317'
FIELDS = ['delivery_id', 'region_id', 'started_at', 'finished_at', 'distance_km', 'status']
SIZES = (10_000, 100_000, 1_000_000)


def generate(output_dir, sizes=SIZES, seed=42):
    sizes = tuple(sorted(sizes))
    if not sizes or len(set(sizes)) != len(sizes) or any(n <= 0 for n in sizes):
        raise ValueError('Sizes must be unique positive integers')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [output_dir / f'deliveries_{n}.csv' for n in sizes]
    manifest_path = output_dir / 'manifest.json'
    if any(p.exists() for p in [*paths, manifest_path]):
        raise FileExistsError('Output already exists; choose an empty output directory')
    created, streams = [], []
    try:
        writers = []
        for path in paths:
            f = path.open('x', encoding='utf-8', newline='')
            created.append(path)
            streams.append(f)
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(FIELDS)
            writers.append(writer)
        rng = random.Random(seed)
        window = int((END - START).total_seconds())
        for index in range(1, sizes[-1] + 1):
            duration = rng.randint(900, 5400)
            start = START + timedelta(seconds=rng.randint(0, window - duration))
            end = start + timedelta(seconds=duration)
            distance = rng.randint(50, 2000)
            draw = rng.randrange(100)
            status = 'completed' if draw < 85 else 'delayed' if draw < 95 else 'cancelled'
            row = [f'B-{index:08d}', REGION, start.isoformat(), end.isoformat(),
                   f'{distance // 100}.{distance % 100:02d}', status]
            for n, writer in zip(sizes, writers):
                if index <= n:
                    writer.writerow(row)
        for f in streams:
            f.close()
        metadata = {'generator': 'generate_benchmark_deliveries_v1', 'seed': seed,
                    'python': platform.python_version(), 'region_id': REGION,
                    'period_utc': '[2026-07-01T00:00:00Z,2026-10-01T00:00:00Z)',
                    'synthetic': True, 'note': 'Statuses are generated independently of alerts.',
                    'datasets': []}
        for n, path in zip(sizes, paths):
            with path.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            metadata['datasets'].append({'file': path.name, 'rows': n, 'sha256': digest, 'bytes': path.stat().st_size})
        with manifest_path.open('x', encoding='utf-8', newline='\n') as f:
            created.append(manifest_path)
            json.dump(metadata, f, ensure_ascii=False, indent=2)
            f.write('\n')
        return metadata
    except BaseException:
        for f in streams:
            f.close()
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/synthetic/benchmark')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = generate(args.output_dir, seed=args.seed)
    for item in result['datasets']:
        print(f"{item['file']}: {item['rows']} rows, SHA-256 {item['sha256']}")
    print('Saved manifest:', args.output_dir / 'manifest.json')


if __name__ == '__main__':
    main()
