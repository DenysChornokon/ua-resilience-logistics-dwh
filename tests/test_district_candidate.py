import hashlib
import json

import pytest

from scripts import district_candidate
from scripts.load_district_context import validate_rows
from scripts.verify_district_context import assert_fresh, assert_temporal_fields, snapshot_hash
from scripts.load_district_context import FIELDS


def sample():
    ring=[[30,50],[31,50],[31,51],[30,51],[30,50]]
    return {'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'id':f'UA80{i:015d}','name':f'district-{i}','oblast':'UA80'},
         'geometry':{'type':'Polygon','coordinates':[ring]}} for i in range(10)]}


def test_candidate_rejects_changed_hash_and_duplicate(monkeypatch):
    obj=sample();raw=json.dumps(obj).encode();monkeypatch.setattr(district_candidate,'SHA256',hashlib.sha256(raw).hexdigest())
    assert len(district_candidate.validate(raw))==10
    with pytest.raises(ValueError,match='checksum'):
        district_candidate.validate(raw+b' ')
    obj['features'][1]['properties']['id']=obj['features'][0]['properties']['id']
    raw=json.dumps(obj).encode();monkeypatch.setattr(district_candidate,'SHA256',hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError,match='Duplicate'):
        district_candidate.validate(raw)


def test_context_identity_and_snapshot_hash():
    start='2026-07-01T00:00:00+00:00'
    point={'delivery_id':'d','region_id':'r','started_at':start}
    row={'delivery_id':'d','region_id':'r','delivery_started_at':start}
    validate_rows([row],[point])
    with pytest.raises(ValueError): validate_rows([{**row,'region_id':'other'}],[point])
    assert snapshot_hash([{'a':1,'b':2}])==snapshot_hash([{'b':2,'a':1}])
    assert snapshot_hash([{'a':1}])!=snapshot_hash([{'a':2}])


def test_stale_snapshot_and_altered_context_are_detected():
    rows=[{'id':'d'}];metadata={'source_fingerprint':{'input_hash':'one'},'context_sha256':snapshot_hash(rows)}
    assert_fresh({'input_hash':'one','rows':rows},metadata)
    with pytest.raises(RuntimeError,match='stale'):
        assert_fresh({'input_hash':'changed','rows':rows},metadata)
    source={key:None for key in FIELDS[3:]};source['alert_data_available']=False
    assert_temporal_fields(dict(source),source)
    with pytest.raises(AssertionError,match='alert_data_available'):
        assert_temporal_fields({**source,'alert_data_available':True},source)
