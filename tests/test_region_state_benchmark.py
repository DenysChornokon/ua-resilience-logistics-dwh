import json
import pytest
from scripts import run_region_state_benchmark as bench


def test_input_inspection_requires_all_consistent_scenarios(monkeypatch):
    state={'alerts_count':371,'alerts_md5':'hash','view_definition':'select 1',
           'scenario_data':[{'scenario_id':s,'outage_count':n,'state_count':10,
                             'manifest':{'event_count':n}} for s,n in zip(bench.SCENARIOS,bench.EXPECTED)]}
    monkeypatch.setattr(bench,'query',lambda sql:json.dumps(state))
    assert bench.inspect()==state
    state['scenario_data'][0]['outage_count']=51
    with pytest.raises(ValueError,match='unexpected'):
        bench.inspect()


def test_missing_states_are_rejected(monkeypatch):
    state={'alerts_count':371,'alerts_md5':'hash','view_definition':'select 1',
           'scenario_data':[{'scenario_id':s,'outage_count':n,'state_count':10,
                             'manifest':{'event_count':n}} for s,n in zip(bench.SCENARIOS,bench.EXPECTED)]}
    state['scenario_data'][2]['state_count']=0
    monkeypatch.setattr(bench,'query',lambda sql:json.dumps(state))
    with pytest.raises(ValueError,match='Missing region states'):
        bench.inspect()
