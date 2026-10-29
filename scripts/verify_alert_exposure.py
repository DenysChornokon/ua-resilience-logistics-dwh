"""Compare every PostgreSQL mart result with the independent Python baseline."""
import csv
import io
import subprocess
from decimal import Decimal
from pathlib import Path

from scripts.interval_join_baseline import indexed, merge, timestamp

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with (ROOT / path).open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def main():
    deliveries = read_csv('data/synthetic/deliveries.csv')
    alerts = read_csv('data/processed/alerts.csv')
    result = subprocess.run(
        ['docker', 'compose', 'exec', '-T', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
         '-U', 'dwh_user', '-d', 'logistics_dwh', '-c',
         'COPY (SELECT * FROM mart.delivery_alert_exposure ORDER BY delivery_id) TO STDOUT WITH CSV HEADER'],
        cwd=ROOT, capture_output=True, check=True,
    )
    output = list(csv.DictReader(io.StringIO(result.stdout.decode('utf-8'))))
    by_id = {r['delivery_id']: r for r in output}
    if len(by_id) != len(output) or len(output) != len(deliveries):
        raise AssertionError('Missing or duplicated deliveries')
    total = exposed = 0
    for d in deliveries:
        relevant = [(timestamp(a['started_at']), timestamp(a['finished_at'])) for a in alerts
                    if a['region_id'] == d['region_id'] and a['event_type'] == 'Повітряна тривога']
        s, e = timestamp(d['started_at']), timestamp(d['finished_at'])
        expected = indexed([(s, e)], merge(relevant))[0]
        count = sum(a < e and b > s for a, b in relevant)
        actual = by_id[d['delivery_id']]
        seconds = Decimal(actual['alert_overlap_seconds'])
        ratio = Decimal(actual['alert_exposure_ratio'])
        if seconds != expected or int(actual['overlap_alert_count']) != count:
            raise AssertionError(f"Exposure or event count differs: {d['delivery_id']}")
        if abs(ratio - Decimal(expected) / Decimal(e-s)) > Decimal('1e-15'):
            raise AssertionError(f"Exposure ratio differs: {d['delivery_id']}")
        if not 0 <= seconds <= e-s or not 0 <= ratio <= 1:
            raise AssertionError(f"Exposure bounds failed: {d['delivery_id']}")
        total += expected
        exposed += expected > 0
    print(f'Compared {len(output)} deliveries; exposed={exposed}; total_seconds={total}; differences=0')


if __name__ == '__main__':
    main()
