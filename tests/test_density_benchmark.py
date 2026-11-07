import copy

import pytest

from scripts.run_density_benchmark import validate_state


def example():
    metrics = {'overlap_pairs': 2, 'peak_simultaneous': 3, 'union_seconds': 100}
    files = {('uniform', 42): ('density_uniform_s42_v1', 'sample.csv', 'abc', [], metrics)}
    manifest = {'profile': 'uniform', 'seed': 42, 'event_count': 250,
                'source_file': 'sample.csv', 'sha256': 'abc', **metrics}
    item = {'scenario_id': 'density_uniform_s42_v1', 'manifest': manifest,
            'outage_count': 250, 'outages_md5': 'hash', 'state_count': 10}
    state = {'view_definition': 'SELECT 1', 'alerts_md5': 'hash', 'alerts_count': 1,
             'scenario_data': [item]}
    return state, files


def test_accepts_matching_state():
    state, files = example()
    assert list(validate_state(state, files)) == ['density_uniform_s42_v1']


@pytest.mark.parametrize('alteration', ['checksum', 'missing', 'duplicate', 'empty_states'])
def test_rejects_inconsistent_state(alteration):
    state, files = example()
    if alteration == 'checksum':
        state['scenario_data'][0]['manifest']['sha256'] = 'wrong'
    elif alteration == 'missing':
        state['scenario_data'].clear()
    elif alteration == 'duplicate':
        state['scenario_data'].append(copy.deepcopy(state['scenario_data'][0]))
    else:
        state['scenario_data'][0]['state_count'] = 0
    with pytest.raises(ValueError):
        validate_state(state, files)
