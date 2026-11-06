import copy
import pytest
from scripts.verify_scenario_delivery_states import verify


def example():
    a='2026-07-01T00:00:00+00:00'; b='2026-07-01T01:00:00+00:00'
    data={'scenario':{'window_start':a,'window_end':b},
          'manifest':{'sha256':'hash','seed':42,'event_count':1},
          'deliveries':[['D1',a]],'alerts':[[a,b]],
          'outages':[['O1',a,b,'synthetic']],
          'results':[['D1',a,a,b,True,True,True]]}
    rows=[{'event_id':'O1','started_at':a,'finished_at':b,'source_type':'synthetic'}]
    return data,rows


def test_checks_flags_and_source():
    data,rows=example()
    assert verify(data,rows,'hash',42,1)[(True,True)]==1
    bad=copy.deepcopy(data); bad['results'][0][-1]=False
    with pytest.raises(AssertionError,match='interval or flags'):
        verify(bad,rows,'hash',42,1)
    bad=copy.deepcopy(data); bad['outages'][0][3]='observed'
    with pytest.raises(AssertionError,match='outage events'):
        verify(bad,rows,'hash',42,1)


def test_rejects_duplicate_or_unknown():
    data,rows=example()
    bad=copy.deepcopy(data); bad['results'].append(bad['results'][0])
    with pytest.raises(AssertionError,match='duplicate'):
        verify(bad,rows,'hash',42,1)
    bad=copy.deepcopy(data); bad['results'][0][4]=False
    with pytest.raises(AssertionError,match='Missing or incorrect'):
        verify(bad,rows,'hash',42,1)
