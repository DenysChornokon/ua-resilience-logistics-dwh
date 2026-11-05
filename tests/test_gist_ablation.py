import json
import pytest
from scripts import run_gist_ablation as ablation


def test_inspect_state_rejects_missing_or_wrong_indexes(monkeypatch):
    state={'alert_copies_equal':True,'definitions_match':True,'alert_rows':371,
           'indexes':['CREATE INDEX benchmark_alert_range_gist ON benchmark.alert_events USING gist (x)']}
    monkeypatch.setattr(ablation.benchmark,'query',lambda sql: json.dumps(state))
    assert ablation.inspect_state() == state
    state['indexes'].append('CREATE INDEX bad ON benchmark.alert_events_no_gist USING gist (x)')
    with pytest.raises(ValueError,match='Control table'):
        ablation.inspect_state()
    state['alert_copies_equal']=False
    with pytest.raises(ValueError,match='do not match'):
        ablation.inspect_state()
