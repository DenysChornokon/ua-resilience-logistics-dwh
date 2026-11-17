import csv
from decimal import Decimal

import pytest

from scripts.summarize_research import cache_groups, regular_groups, stats, verify_regular_summary


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def test_regular_series_excludes_warmup_and_requires_complete_unique_cases(tmp_path):
    rows = []
    for method in ('left', 'right'):
        for rep in range(3):
            rows.append({'size': 10, 'method': method, 'repetition': rep, 'phase': 'warmup' if rep == 0 else 'measured',
                         'output_rows': 10, 'execution_ms': 999 if rep == 0 else rep})
    path = tmp_path / 'values.csv'; write_csv(path, rows)
    groups = regular_groups(path, (10,), ('left', 'right'), 2)
    assert stats(groups[(10, 'left')])['median_ms'] == Decimal('1.5')
    write_csv(path, rows[:-1])
    with pytest.raises(ValueError, match='Incomplete'):
        regular_groups(path, (10,), ('left', 'right'), 2)
    write_csv(path, rows + [rows[-1]])
    with pytest.raises(ValueError, match='Duplicate'):
        regular_groups(path, (10,), ('left', 'right'), 2)


def test_cached_total_is_summed_per_paired_trial_before_median(tmp_path):
    rows = []
    for profile in ('sparse', 'uniform', 'clustered'):
        for size in (10000, 100000):
            for rep, (setup, cached) in enumerate([(1, 1), (1, 100), (2, 2), (100, 1)]):
                rows.append({'profile': profile, 'size': size, 'seed': 42,
                             'scenario_id': f'density_{profile}_s42_v1', 'repetition': rep,
                             'phase': 'warmup' if rep == 0 else 'measured', 'baseline_ms': 200,
                             'setup_ms': setup, 'cached_ms': cached})
    path = tmp_path / 'cache.csv'; write_csv(path, rows)
    groups = cache_groups(path)
    assert stats(groups[('uniform', 10000, 'setup_plus_cached')])['median_ms'] == Decimal('101')


def test_inconsistent_saved_summary_is_rejected(tmp_path):
    path = tmp_path / 'summary.csv'
    write_csv(path, [{'size': 10, 'method': 'left', 'runs': 2, 'median_execution_ms': '99',
                      'min_execution_ms': '1', 'max_execution_ms': '2'}])
    with pytest.raises(ValueError, match='disagrees'):
        verify_regular_summary(path, {(10, 'left'): [Decimal('1'), Decimal('2')]})
