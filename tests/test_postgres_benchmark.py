import pytest
from scripts.run_postgres_benchmark import plan_metrics


def test_extracts_rows_buffers_and_index_from_nested_plan():
    plan={'Planning Time': 1.0, 'Execution Time': 12.5,
          'Plan': {'Actual Rows': 10, 'Actual Loops': 1, 'Shared Hit Blocks': 7,
                   'Plans':[{'Index Name':'benchmark_alert_range_gist'}]}}
    result=plan_metrics(plan,10)
    assert result['execution_ms']==12.5
    assert result['shared_hit_blocks']==7
    assert result['indexes']=='benchmark_alert_range_gist'


def test_fails_on_unexpected_output_count():
    plan={'Planning Time': 1.0, 'Execution Time': 12.5, 'Plan': {'Actual Rows': 0}}
    with pytest.raises(ValueError,match='Expected 10'):
        plan_metrics(plan,10)
