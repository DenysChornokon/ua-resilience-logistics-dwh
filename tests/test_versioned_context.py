from uuid import uuid4
import copy

import pytest

from scripts.district_candidate import COMMIT,SHA256
from scripts.load_district_context import SCENARIO
from scripts.verify_district_context import snapshot_hash
from scripts.versioned_context import id_literal,validate_snapshot
from scripts import refresh_versioned_context as refresh


def test_version_id_rejects_non_uuid_input():
    ident=str(uuid4());assert id_literal(ident)==f"'{ident}'::uuid"
    with pytest.raises(ValueError):id_literal("x'; DROP TABLE x;--")


def test_snapshot_must_match_data_and_spatial_provenance():
    start='2026-07-01T00:00:00+00:00'
    rows=[{'delivery_id':'d','region_id':'r','delivery_started_at':start}]
    points=[{'delivery_id':'d','region_id':'r','started_at':start}]
    point_meta={'points_sha256':'original'}
    meta={'candidate_commit':COMMIT,'candidate_sha256':SHA256,'scenario_id':SCENARIO,
          'point_source_manifest':point_meta,'context_sha256':snapshot_hash(rows)}
    validate_snapshot({'metadata':meta,'rows':rows},points,point_meta)
    with pytest.raises(ValueError,match='hash'):
        validate_snapshot({'metadata':meta,'rows':[{**rows[0],'region_id':'wrong'}]},points,point_meta)
    with pytest.raises(ValueError,match='source'):
        validate_snapshot({'metadata':{**meta,'candidate_sha256':'bad'},'rows':rows},points,point_meta)


def test_refresh_without_changes_does_not_create_version(monkeypatch,capsys):
    rows=[{'delivery_id':'d'}];spatial={'points':'p'}
    old={'version_id':str(uuid4()),'spatial_fingerprint':spatial,
         'metadata':{'source_fingerprint':{'data':'same'},'context_sha256':snapshot_hash(rows)}}
    current={'data':'same','rows':rows}
    monkeypatch.setattr(refresh,'assert_spatial_sources',lambda:([],[],{},spatial))
    monkeypatch.setattr(refresh,'active',lambda:old)
    monkeypatch.setattr(refresh,'validate_snapshot',lambda *a:None)
    monkeypatch.setattr(refresh,'verify_sources',lambda p:(copy.deepcopy(current),{}))
    monkeypatch.setattr(refresh,'capture',lambda:copy.deepcopy(current))
    monkeypatch.setattr(refresh,'spatial_query',lambda sql:spatial)
    monkeypatch.setattr(refresh.subprocess,'run',lambda *a,**k:pytest.fail('unchanged snapshot must not write'))
    refresh.main();assert '"unchanged"' in capsys.readouterr().out


def test_changed_source_activates_verified_new_version(monkeypatch,capsys):
    rows=[{'delivery_id':'d'}];spatial={'points':'p'};old_id=str(uuid4());new_id=uuid4();saved={}
    old={'version_id':old_id,'spatial_fingerprint':spatial,
         'metadata':{'source_fingerprint':{'data':'old'},'context_sha256':snapshot_hash([])}}
    current={'data':'new','rows':rows}
    monkeypatch.setattr(refresh,'assert_spatial_sources',lambda:([],[],{},spatial))
    def active():
        return old if 'metadata' not in saved else {'version_id':str(new_id),'spatial_fingerprint':spatial,'metadata':saved['metadata']}
    monkeypatch.setattr(refresh,'active',active)
    monkeypatch.setattr(refresh,'validate_snapshot',lambda *a:None)
    monkeypatch.setattr(refresh,'verify_sources',lambda p:(copy.deepcopy(current),{}))
    monkeypatch.setattr(refresh,'capture',lambda:copy.deepcopy(current))
    monkeypatch.setattr(refresh,'spatial_query',lambda sql:spatial)
    monkeypatch.setattr(refresh,'uuid4',lambda:new_id)
    def make_sql(expected,ident,actual_rows,metadata,fingerprint):
        assert expected==old_id and ident==str(new_id) and actual_rows==rows and fingerprint==spatial
        saved['metadata']=metadata
        return 'transaction'
    monkeypatch.setattr(refresh,'refresh_sql',make_sql)
    monkeypatch.setattr(refresh.subprocess,'run',lambda *a,**k:None)
    refresh.main();assert '"refreshed"' in capsys.readouterr().out
