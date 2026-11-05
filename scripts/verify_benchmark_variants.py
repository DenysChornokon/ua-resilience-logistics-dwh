"""Stream SQL results and compare every row with the Python interval baseline."""
import bisect
import csv
import io
import subprocess
import tempfile
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path

from scripts.interval_join_baseline import merge, overlap, timestamp

ROOT = Path(__file__).resolve().parents[1]
SIZES = (10000, 100000, 1000000)


def verify(method, size, raw):
    intervals = merge(raw)
    merged_ends = [end for _, end in intervals]
    starts, ends = sorted(s for s, _ in raw), sorted(e for _, e in raw)
    sql = f'COPY (SELECT * FROM benchmark.{method}_{size} ORDER BY delivery_id) TO STDOUT WITH CSV HEADER'
    command = ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
               '-U','dwh_user','-d','logistics_dwh','-c',sql]
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=errors)
        try:
            sql_rows = csv.DictReader(io.TextIOWrapper(proc.stdout, encoding='utf-8', newline=''))
            with (ROOT / f'data/synthetic/benchmark/deliveries_{size}.csv').open(encoding='utf-8', newline='') as f:
                rows = csv.DictReader(f)
                count = 0
                for expected, actual in zip_longest(rows, sql_rows):
                    if expected is None or actual is None:
                        raise AssertionError(f'{method}_{size}: row count differs')
                    count += 1
                    start, end = timestamp(expected['started_at']), timestamp(expected['finished_at'])
                    i = bisect.bisect_right(merged_ends, start)
                    seconds = 0
                    while i < len(intervals) and intervals[i][0] < end:
                        seconds += overlap(start, end, *intervals[i])
                        i += 1
                    events = bisect.bisect_left(starts, end) - bisect.bisect_right(ends, start)
                    if actual['delivery_id'] != expected['delivery_id'] or Decimal(actual['alert_overlap_seconds']) != seconds or int(actual['overlap_alert_count']) != events:
                        raise AssertionError(f'{method}_{size}: difference at row {count}')
                    if abs(Decimal(actual['alert_exposure_ratio']) - Decimal(seconds)/Decimal(end-start)) > Decimal('1e-15'):
                        raise AssertionError(f'{method}_{size}: ratio differs at row {count}')
            if proc.wait() != 0:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', errors='replace'))
            if count != size:
                raise AssertionError(f'{method}_{size}: expected {size} rows, got {count}')
            print(f'{method}_{size}: {count} rows match Python', flush=True)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def main():
    with (ROOT / 'data/processed/alerts.csv').open(encoding='utf-8-sig', newline='') as f:
        raw = [(timestamp(a['started_at']), timestamp(a['finished_at'])) for a in csv.DictReader(f)
               if a['region_id']=='UA80000000000093317' and a['event_type']=='Повітряна тривога']
    if not raw:
        raise ValueError('No alerts in source CSV')
    for size in SIZES:
        for method in ('baseline', 'range'):
            verify(method, size, raw)


if __name__ == '__main__':
    main()
