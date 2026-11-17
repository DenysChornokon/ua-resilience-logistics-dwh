import copy

import pytest

from scripts.generate_density_scenarios import REGION, START, END
from scripts.verify_density_delivery_states import check_record


def example():
    start, end = START.isoformat(), END.isoformat()
    metrics = {'overlap_pairs': 0, 'peak_simultaneous': 1, 'union_seconds': int((END - START).total_seconds())}
    rows = [{'event_id': 'O1', 'started_at': start, 'finished_at': end, 'source_type': 'synthetic'}]
    entry = ('density_sparse_s41_v1', 'events.csv', 'outage-hash', rows, metrics)
    data = {'scenario': {'region_id': REGION, 'window_start': start, 'window_end': end},
            'manifest': {'source_file': 'events.csv', 'sha256': 'outage-hash', 'seed': 41,
                         'profile': 'sparse', 'event_count': 1, **metrics},
            'alert_hash': 'alerts-hash', 'delivery_hash': 'deliveries-hash',
            'deliveries': [['D1', start]], 'alerts': [[start, end]],
            'outages': [['O1', start, end, 'synthetic']],
            'results': [['D1', start, start, end, True, True, True]]}
    return data, entry


def test_valid_record_matches_independent_reference():
    data, entry = example()
    result = check_record(data, entry, 'sparse', 41, 'alerts-hash', 'deliveries-hash', [('D1', START)])
    assert result['both'] == 1 and result['deliveries'] == 1


@pytest.mark.parametrize('error', ['wrong_delivery', 'wrong_flag', 'wrong_manifest', 'wrong_source'])
def test_rejects_inconsistent_delivery_or_provenance(error):
    data, entry = example()
    data = copy.deepcopy(data)
    if error == 'wrong_delivery':
        data['deliveries'][0][1] = END.isoformat()
    elif error == 'wrong_flag':
        data['results'][0][-1] = False
    elif error == 'wrong_manifest':
        data['manifest']['overlap_pairs'] = 2
    else:
        data['delivery_hash'] = 'bad'
    with pytest.raises(AssertionError):
        check_record(data, entry, 'sparse', 41, 'alerts-hash', 'deliveries-hash', [('D1', START)])
