import json

import pandas as pd
import pytest

from scripts import prepare_alerts
from scripts.prepare_alerts import local_to_utc
from scripts.profile_air_alerts import local_to_utc as profile_local_to_utc


@pytest.mark.parametrize(('source', 'expected'), [
    ('2026-07-01T19:52:38', '2026-07-01T16:52:38+00:00'),
    ('2026-01-15T12:00:00', '2026-01-15T10:00:00+00:00'),
])
def test_local_time_to_utc(source, expected):
    result = local_to_utc(pd.Series([source]))
    assert result.iloc[0] == pd.Timestamp(expected)


@pytest.mark.parametrize('source', ['2026-10-25T03:15:00', '2026-03-29T03:15:00'])
def test_dst_ambiguous_or_nonexistent_time_is_rejected(source):
    with pytest.raises(ValueError):
        local_to_utc(pd.Series([source]))


def test_profile_excludes_ambiguous_and_nonexistent_times():
    assert profile_local_to_utc('2026-10-25T03:15:00')[1] == 'ambiguous'
    assert profile_local_to_utc('2026-03-29T03:15:00')[1] == 'nonexistent'
    assert profile_local_to_utc('2026-07-01T19:52:38')[0].isoformat() == '2026-07-01T16:52:38+00:00'


def test_boundary_is_selected_before_clipping_and_unrelated_dst_is_ignored(tmp_path, monkeypatch):
    source = tmp_path / 'alerts.json'
    output = tmp_path / 'alerts.csv'
    def record(uid, start, end):
        return {'uid': uid, 'CATUTTC': 'UA80000000000093317',
                'addressAdminUnitL2': 'місто Київ', 'type': 'Повітряна тривога',
                'dateTimeStart': start, 'dateTimeEnd': end}
    source.write_text(json.dumps([
        record('2553', '2026-10-01T02:51:02', '2026-10-01T03:00:46'),
        record('outside', '2026-10-25T03:06:37', '2026-10-25T03:20:43'),
    ], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(prepare_alerts, 'SOURCE', source)
    monkeypatch.setattr(prepare_alerts, 'OUTPUT', output)
    result = prepare_alerts.prepare_alerts()
    assert result['event_id'].tolist() == ['2553']
    assert result.iloc[0]['started_at'] == pd.Timestamp('2026-09-30T23:51:02Z')
    assert result.iloc[0]['finished_at'] == pd.Timestamp('2026-10-01T00:00:00Z')
