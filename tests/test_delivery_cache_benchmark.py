import pytest

from scripts.run_delivery_cache_benchmark import break_even, parse_sections, statements, summarize_runs


def test_break_even_accounts_for_setup_and_non_faster_queries():
    assert break_even(15, 10, 4) == 3
    assert break_even(12, 10, 4) == 2
    assert break_even(12, 4, 4) is None
    assert break_even(12, 4, 5) is None


def test_parse_sections_requires_all_markers_and_parses_json():
    raw = '__SETUP__\n{"state_rows":2}\n__CHECK__\n{"differences":0}\n__CACHED__\n[{}]\n__BASELINE__\n[{}]\n__END__\n'
    assert parse_sections(raw)['setup']['state_rows'] == 2
    with pytest.raises(ValueError):
        parse_sections(raw.replace('__CHECK__', '__SETUP__'))
    with pytest.raises(ValueError):
        parse_sections(raw.replace('__END__', ''))


def test_query_rejects_unexpected_identifiers():
    with pytest.raises(ValueError):
        statements("density_sparse_s41_v1'; SELECT 1; --")


def test_total_uses_each_run_before_taking_median():
    case = {'profile':'sparse', 'seed':41, 'scenario_id':'s', 'deliveries':1}
    measurements = [dict(phase='measured', scenario_id='s', state_rows=1,
                         materialize_ms=setup, index_ms=0, analyze_ms=0,
                         setup_ms=setup, baseline_ms=110, cached_ms=cached)
                    for setup, cached in [(1,100), (10,10), (100,1)]]
    assert summarize_runs([case], measurements, 3)[0]['setup_plus_cached_median_ms'] == 101
