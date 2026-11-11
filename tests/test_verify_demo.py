import pytest

from scripts.verify_demo import read_queries, validate_results


def test_query_sections_are_read_only_and_exactly_named(tmp_path):
    path = tmp_path / 'demo.sql'
    path.write_text('-- DEMO:first\nSELECT 1;\n-- DEMO:second\nWITH x AS (SELECT 1) SELECT * FROM x;\n', encoding='utf-8')
    assert list(read_queries(path, ('first', 'second'))) == ['first', 'second']
    path.write_text('-- DEMO:first\nSELECT 1; DELETE FROM important;\n', encoding='utf-8')
    with pytest.raises(ValueError, match='one SELECT'):
        read_queries(path, ('first',))
    with pytest.raises(ValueError, match='names or order'):
        read_queries(path, ('wrong',))
    path.write_text('DELETE FROM important;\n-- DEMO:first\nSELECT 1;\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Only comments'):
        read_queries(path, ('first',))


def valid_results():
    scenario = 'density_uniform_s42_v1'
    logistics = {
        'source_counts': [{'dataset': 'alerts', 'rows_count': '1'}, {'dataset': 'deliveries_synthetic', 'rows_count': '1'},
                          {'dataset': 'weather_hourly', 'rows_count': '1'}],
        'overview': [{'total_deliveries': '1', 'deliveries_with_alert_overlap': '1', 'unknown_alert_exposure': '0',
                      'total_alert_overlap_seconds': '60', 'weather_known': '1', 'weather_unknown': '0'}],
        'delivery_examples': [{'delivery_id': 'd1', 'scenario_row_present': 't'}],
        'scenario_counts': [{'scenario_id': scenario, 'deliveries': '1'}],
    }
    spatial = {'active_version': [{'version_id': 'v1', 'scenario_id': scenario, 'row_count': '1'}],
               'match_counts': [{'district_match_status': 'matched', 'deliveries': '1'},
                                {'district_match_status': 'unmatched', 'deliveries': '0'},
                                {'district_match_status': 'ambiguous', 'deliveries': '0'}],
               'district_examples': [{'delivery_id': 'd1', 'district_match_status': 'matched', 'version_id': 'v1'}],
               'district_summary': [{'synthetic_deliveries': '1'}]}
    alert = {'deliveries': 1, 'exposed': 1, 'total_seconds': 60}
    weather = {'deliveries': 1, 'weather_known': 1, 'weather_unknown': 0}
    version = {'active_version_id': 'v1', 'match_counts': {'matched': 1, 'unmatched': 0, 'ambiguous': 0}}
    return logistics, spatial, alert, weather, version


def test_validation_detects_stale_or_incomplete_demonstration():
    data = valid_results()
    assert validate_results(*data)['district_match_counts']['matched'] == 1
    data[1]['active_version'][0]['version_id'] = 'stale'
    with pytest.raises(AssertionError, match='active version'):
        validate_results(*data)
    data = valid_results()
    data[0]['scenario_counts'][0]['deliveries'] = '0'
    with pytest.raises(AssertionError, match='Scenario'):
        validate_results(*data)
