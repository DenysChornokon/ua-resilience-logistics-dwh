import csv
from datetime import datetime, timezone

import pytest

from scripts import spatiotemporal_benchmark as bench


def feature(ident, ring):
    return {'properties': {'id': ident}, 'geometry': {'coordinates': [ring]}}


def test_district_reference_keeps_boundaries_and_ambiguity():
    left = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    right = [[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]
    rings = bench.district_rings([feature('left', left), feature('right', right)])
    assert bench.match_districts((.5, .5), rings) == (1, 'left')
    assert bench.match_districts((1, .5), rings) == (2, None)
    assert bench.match_districts((3, .5), rings) == (0, None)


def test_alert_reference_uses_half_open_intervals():
    t = lambda minute: datetime(2026, 7, 1, 0, minute, tzinfo=timezone.utc)
    events = [(t(10), t(20)), (t(30), t(40))]
    starts = [start for start, _ in events]
    assert not bench.in_alert(t(9), events, starts)
    assert bench.in_alert(t(10), events, starts)
    assert not bench.in_alert(t(20), events, starts)
    assert bench.in_alert(t(30), events, starts)


def test_generator_is_reproducible_and_does_not_change_source(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, 'SIZES', (3,))
    source = tmp_path / 'deliveries.csv'
    fields = ['delivery_id', 'region_id', 'started_at', 'finished_at', 'distance_km', 'status']
    with source.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for i in range(1, 4):
            writer.writerow({'delivery_id': f'B-{i:08d}', 'region_id': bench.REGION,
                             'started_at': '2026-07-01T00:10:00+00:00', 'finished_at': '2026-07-01T00:20:00+00:00',
                             'distance_km': '2.00', 'status': 'completed'})
    original = source.read_bytes()
    ring = [[30, 50], [31, 50], [31, 51], [30, 51], [30, 50]]
    events = [(datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc), datetime(2026, 7, 1, 0, 15, tzinfo=timezone.utc))]
    one, two = tmp_path / 'one', tmp_path / 'two'
    one.mkdir(); two.mkdir()
    a = bench.make_inputs(source, events, [feature('x', ring)], one, 3)
    b = bench.make_inputs(source, events, [feature('x', ring)], two, 3)
    assert a[0].read_bytes() == b[0].read_bytes() and a[1].read_bytes() == b[1].read_bytes()
    assert a[3] == b[3] and a[3][3]['alert_active'] == 3
    assert source.read_bytes() == original


def test_query_rejects_unsupported_cases_and_changes_only_spatial_table():
    with pytest.raises(ValueError): bench.query_sql(10000, 'other')
    with pytest.raises(ValueError): bench.query_sql(10, 'indexed')
    assert bench.query_sql(10000, 'indexed').replace('points_indexed', 'points_unindexed') == bench.query_sql(10000, 'unindexed')
