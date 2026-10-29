import csv
from datetime import timedelta

import pytest

from scripts.load_dwh import (
    PERIOD_START, REGION, SOURCES, ValidationError, instant, make_sql, read_sources,
)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def source_files(tmp_path):
    rows = {
        'alerts': [{'event_id': '1', 'region_id': REGION, 'region_name': 'місто Київ',
                    'event_type': 'Повітряна тривога', 'started_at': '2026-07-01T01:00:00+00:00',
                    'finished_at': '2026-07-01T02:00:00+00:00'}],
        'deliveries': [{'delivery_id': 'D-1', 'region_id': REGION, 'started_at': '2026-07-01T01:10:00+00:00',
                        'finished_at': '2026-07-01T01:20:00+00:00', 'distance_km': '5', 'status': 'completed'}],
        'weather': [{'time': (PERIOD_START + timedelta(hours=i)).isoformat(),
                     'temperature_2m': '20', 'precipitation': '0', 'wind_speed_10m': '10'} for i in range(2208)],
    }
    for name, (path, fields) in SOURCES.items():
        write_csv(tmp_path / path, fields, rows[name])
    return tmp_path, rows


def test_valid_sources_generate_transaction_and_provenance(source_files):
    root, _ = source_files
    datasets = read_sources(root)
    sql = make_sql(datasets)
    assert [len(datasets[name].rows) for name in ('alerts', 'deliveries', 'weather')] == [1, 1, 2208]
    assert 'BEGIN;' in sql and 'COMMIT;' in sql and 'pg_advisory_xact_lock' in sql
    assert sql.count('\\.\n') == 3
    assert datasets['alerts'].digest in sql
    assert "'Europe/Kyiv', false" in sql
    assert 'WHERE (SELECT do_load FROM load_decision)' in sql


def test_rejects_missing_hour(source_files):
    root, rows = source_files
    path, fields = SOURCES['weather']
    write_csv(root / path, fields, rows['weather'][:-1])
    with pytest.raises(ValidationError, match='неповне або повторне'):
        read_sources(root)


def test_rejects_invalid_interval_and_duplicate_id(source_files):
    root, rows = source_files
    path, fields = SOURCES['deliveries']
    rows['deliveries'][0]['finished_at'] = '2026-07-01T01:00:00+00:00'
    write_csv(root / path, fields, rows['deliveries'] * 2)
    with pytest.raises(ValidationError) as exc:
        read_sources(root)
    assert 'некоректний інтервал' in str(exc.value)
    assert 'повторний ключ' in str(exc.value)


def test_rejects_naive_timestamps_and_nonfinite_values(source_files):
    root, rows = source_files
    path, fields = SOURCES['deliveries']
    rows['deliveries'][0]['started_at'] = '2026-07-01T01:10:00'
    write_csv(root / path, fields, rows['deliveries'])
    with pytest.raises(ValidationError, match='часовий пояс'):
        read_sources(root)
    rows['deliveries'][0]['started_at'] = '2026-07-01T01:10:00+00:00'
    rows['deliveries'][0]['distance_km'] = 'NaN'
    write_csv(root / path, fields, rows['deliveries'])
    with pytest.raises(ValidationError, match='скінченним'):
        read_sources(root)


def test_rejects_multiline_fields(source_files):
    root, rows = source_files
    path, fields = SOURCES['alerts']
    rows['alerts'][0]['event_id'] = '1\n\\.'
    write_csv(root / path, fields, rows['alerts'])
    with pytest.raises(ValidationError, match='перенесення рядка'):
        read_sources(root)


def test_equivalent_offsets_represent_same_instant():
    assert instant('2026-07-01T03:00:00+03:00') == PERIOD_START
