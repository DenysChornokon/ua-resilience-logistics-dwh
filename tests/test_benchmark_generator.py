import csv
from datetime import datetime

import pytest

from scripts.generate_benchmark_deliveries import END, FIELDS, REGION, START, generate


def test_reproducibility_prefix_and_contract(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    first = generate(a, (10, 20), seed=42)
    second = generate(b, (10, 20), seed=42)
    assert first['datasets'] == second['datasets']
    with (a / 'deliveries_10.csv').open(encoding='utf-8', newline='') as f:
        small = list(csv.DictReader(f))
    with (a / 'deliveries_20.csv').open(encoding='utf-8', newline='') as f:
        big = list(csv.DictReader(f))
    assert small == big[:10]
    assert list(big[0]) == FIELDS
    assert len({r['delivery_id'] for r in big}) == 20
    for row in big:
        start, end = datetime.fromisoformat(row['started_at']), datetime.fromisoformat(row['finished_at'])
        assert START <= start < end <= END
        assert 900 <= (end - start).total_seconds() <= 5400
        assert row['region_id'] == REGION
        assert 0.5 <= float(row['distance_km']) <= 20
        assert row['status'] in {'completed', 'delayed', 'cancelled'}


def test_no_overwrite(tmp_path):
    generate(tmp_path, (2,), seed=42)
    original = (tmp_path / 'deliveries_2.csv').read_bytes()
    with pytest.raises(FileExistsError):
        generate(tmp_path, (2,), seed=42)
    assert (tmp_path / 'deliveries_2.csv').read_bytes() == original


def test_invalid_sizes(tmp_path):
    with pytest.raises(ValueError):
        generate(tmp_path, (1, 1))
