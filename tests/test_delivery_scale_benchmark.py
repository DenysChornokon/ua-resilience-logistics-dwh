import pytest

from scripts.run_delivery_scale_benchmark import case_sql, scaled_statements


def test_scaled_sql_uses_the_same_dataset_for_both_methods():
    sql = case_sql('density_sparse_s42_v1', 10000, 'baseline', True)
    assert 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in sql
    assert 'ON COMMIT DROP' in sql and 'EXCEPT ALL' in sql
    assert 'benchmark.deliveries_10000 d' in sql
    assert 'core.fact_delivery d' not in sql
    assert 'SELECT * FROM scenario.delivery_start_state' not in sql
    assert sql.rstrip().endswith('ROLLBACK;')


def test_measured_trial_omits_correctness_check_and_rejects_bad_size():
    sql = case_sql('density_sparse_s42_v1', 10000, 'cached', False)
    assert 'EXCEPT ALL' not in sql
    assert "SELECT 'null';" in sql
    with pytest.raises(ValueError):
        scaled_statements('density_sparse_s42_v1', 10001)
