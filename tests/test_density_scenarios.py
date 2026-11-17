from datetime import timedelta

import pytest
from scripts.generate_density_scenarios import START, END, SEEDS, events, generate, overlap_metrics
from scripts.load_density_scenarios import inspect, build_sql


def test_overlap_metrics_nested_and_touching():
    t=lambda s: START+timedelta(seconds=s)
    # [0,10), [2,3), [4,12), [12,15): two overlap pairs; touch is excluded.
    assert overlap_metrics([(t(0),t(10)),(t(2),t(3)),(t(4),t(12)),(t(12),t(15))]) == {
        'overlap_pairs':2,'peak_simultaneous':2,'union_seconds':15}


def test_reproducible_paired_profiles_and_overlap(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b'
    first,second=generate(a),generate(b)
    assert first['datasets']==second['datasets']
    for seed in SEEDS:
        profiles=[events(p,seed) for p in ('sparse','uniform','clustered')]
        assert all(START<=s<e<=END for data in profiles for s,e in data)
        assert [b-a for a,b in profiles[0]] == [b-a for a,b in profiles[1]] == [b-a for a,b in profiles[2]]
        assert overlap_metrics(profiles[0])['overlap_pairs']==0
        assert overlap_metrics(profiles[2])['overlap_pairs'] > overlap_metrics(profiles[1])['overlap_pairs']
    data=inspect(a)
    assert len(data)==9 and build_sql(data).count('COPY scenario.outage_events')==9
    with pytest.raises(FileExistsError): generate(a)


def test_rejects_modified_file(tmp_path):
    generate(tmp_path)
    p=tmp_path/'density_sparse_s41_v1.csv'; p.write_bytes(p.read_bytes()+b'x')
    with pytest.raises(ValueError,match='Checksum mismatch'): inspect(tmp_path)
