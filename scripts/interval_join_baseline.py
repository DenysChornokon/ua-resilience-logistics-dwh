"""Kyiv interval-join baseline. Run: python scripts/interval_join_baseline.py"""
import argparse
import bisect
import csv
import hashlib
import json
import platform
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

REGION = 'UA80000000000093317'
SIZES = (1000, 10000, 100000)
SEED = 42


def timestamp(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.utcoffset() is None:
        raise ValueError('Expected time with explicit offset')
    return int(d.timestamp())


def overlap(a, b, c, d):
    return max(0, min(b, d) - max(a, c))


def merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            raise ValueError('Invalid alert interval')
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def scan(deliveries, intervals):
    return [sum(overlap(s, e, a, b) for a, b in intervals if a < e and b > s)
            for s, e in deliveries]


def indexed(deliveries, intervals):
    ends = [b for _, b in intervals]
    result = []
    for s, e in deliveries:
        i = bisect.bisect_right(ends, s)
        total = 0
        while i < len(intervals) and intervals[i][0] < e:
            a, b = intervals[i]
            total += overlap(s, e, a, b)
            i += 1
        result.append(total)
    return result


def correctness():
    # Pair results are hand-calculated in seconds.
    cases = [(0, 10, 5, 15, 5), (0, 10, 10, 20, 0),
             (0, 10, 12, 20, 0), (0, 10, 2, 8, 6),
             (2, 8, 0, 10, 6), (0, 10, 0, 10, 10)]
    for a, b, c, d, expected in cases:
        value = overlap(a, b, c, d)
        assert value == expected and 0 <= value <= min(b-a, d-c)
    # Nested intervals and overlapping alerts must count each second once.
    merged = merge([(2, 8), (4, 6), (7, 12), (12, 14), (18, 20)])
    assert merged == [(2, 14), (18, 20)]
    deliveries = [(0, 10), (3, 13), (14, 18), (0, 22)]
    expected = [8, 10, 0, 14]
    assert scan(deliveries, merged) == indexed(deliveries, merged) == expected
    assert all(0 <= x <= e-s for (s, e), x in zip(deliveries, expected))
    return {'pair_cases': len(cases), 'delivery_cases': len(deliveries),
            'merged_intervals': merged, 'expected_exposure_seconds': expected}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root
    report = root / 'reports'
    report.mkdir(parents=True, exist_ok=True)
    with (root / 'data/processed/alerts.csv').open(encoding='utf-8-sig', newline='') as f:
        records = [r for r in csv.DictReader(f) if r['region_id'] == REGION]
    if not records:
        raise ValueError('No Kyiv alerts found')
    intervals = merge([(timestamp(r['started_at']), timestamp(r['finished_at'])) for r in records])
    check = correctness()
    deliveries_path = root / 'data/synthetic/deliveries.csv'
    with deliveries_path.open(encoding='utf-8-sig', newline='') as f:
        observed = list(csv.DictReader(f))
    if not observed or any(r['region_id'] != REGION for r in observed):
        raise ValueError('Synthetic deliveries must belong to Kyiv')
    operations = [(timestamp(r['started_at']), timestamp(r['finished_at'])) for r in observed]
    if any(e <= s for s, e in operations):
        raise ValueError('Invalid delivery interval')
    raw = [(timestamp(r['started_at']), timestamp(r['finished_at'])) for r in records]
    for s, e in operations:
        for a, b in raw:
            value = overlap(s, e, a, b)
            assert 0 <= value <= min(e-s, b-a)
            if a >= e or b <= s:
                assert value == 0
    exposure = scan(operations, intervals)
    assert exposure == indexed(operations, intervals)
    assert all(0 <= x <= e-s for (s, e), x in zip(operations, exposure))
    check['existing_delivery_count'] = len(operations)
    check['existing_exposed_deliveries'] = sum(x > 0 for x in exposure)
    check['existing_pair_checks'] = len(operations) * len(raw)
    rng = random.Random(SEED)
    start = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
    deliveries = []
    for _ in range(max(SIZES)):
        duration = rng.randint(900, 5400)
        s = rng.randint(start, end-duration)
        deliveries.append((s, s+duration))
    rows = []
    for n in SIZES:
        data = deliveries[:n]
        results = []
        for method in (scan, indexed):
            t = perf_counter()
            values = method(data, intervals)
            elapsed = (perf_counter() - t) * 1000
            assert all(0 <= x <= e-s for (s,e), x in zip(data, values))
            digest = hashlib.sha256(','.join(map(str, values)).encode()).hexdigest()
            results.append(values)
            rows.append({'method': method.__name__, 'deliveries': n,
                         'source_alerts': len(records), 'merged_alerts': len(intervals),
                         'output_rows': len(values), 'exposed_deliveries': sum(x > 0 for x in values),
                         'total_exposure_seconds': sum(values), 'elapsed_ms': round(elapsed, 3),
                         'result_sha256': digest, 'memory': 'not measured'})
        if results[0] != results[1]:
            raise AssertionError(f'Methods disagree at size {n}')
        print(n, [(r['method'], r['elapsed_ms'], r['exposed_deliveries']) for r in rows[-2:]], flush=True)
    csv_path = report / 'interval_join_benchmark.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    metadata = {'seed': SEED, 'period_utc': '[2026-07-01, 2026-10-01)',
                'region_id': REGION, 'python': sys.version, 'platform': platform.platform(),
                'processor': platform.processor(), 'correctness': check,
                'timing_scope': 'join including output list allocation; excludes reading, generation, and merging',
                'runs_per_method': 1, 'memory': 'not measured',
                'warning': 'Alert source timestamps are assumed Europe/Kyiv by user instruction; not verified with source. Processed CSV is converted to UTC.'}
    (report / 'interval_join_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved:', csv_path)


if __name__ == '__main__':
    main()
