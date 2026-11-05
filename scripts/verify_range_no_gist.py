"""Check each no-GiST result independently against the Python interval reference."""
import csv
from scripts.verify_benchmark_variants import ROOT, SIZES, timestamp, verify


def main():
    with (ROOT/'data/processed/alerts.csv').open(encoding='utf-8-sig',newline='') as file:
        raw=[(timestamp(r['started_at']), timestamp(r['finished_at'])) for r in csv.DictReader(file)
             if r['region_id']=='UA80000000000093317' and r['event_type']=='Повітряна тривога']
    if not raw:
        raise ValueError('No alerts in source CSV')
    for size in SIZES:
        verify('range_no_gist',size,raw)


if __name__ == '__main__':
    main()
