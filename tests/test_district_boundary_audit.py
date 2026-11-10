import pytest

from scripts.audit_district_boundaries import classify,index_digest,mismatch_details,prepare_points
from scripts.capture_boundary_reference import parse_official_page


def test_facility_points_deduplicate_and_exclude_conflicting_labels():
    features=[{'properties':{'id':f'id-{i}','name':f'name-{i} район'}} for i in range(10)]
    rows=[{'lat':'50.10','lng':'30.20','district':'name-0'},
          {'lat':'50.100','lng':'30.200','district':'name-0'},
          {'lat':'50.11','lng':'30.21','district':'name-0'},
          {'lat':'50.11','lng':'30.21','district':'name-1'},
          {'lat':'bad','lng':'30.22','district':'name-0'},
          {'lat':'50.13','lng':'30.23','district':'unknown'}]
    points,quality=prepare_points(rows,features)
    assert len(points)==1 and points[0]['expected_id']=='id-0'
    assert points[0]['source_rows']==[1,2]
    assert quality['invalid_coordinates']==1 and quality['unknown_district_labels']==1
    assert quality['duplicate_records_collapsed']==2
    assert quality['conflicting_label_coordinates_excluded']==1 and quality['records_at_conflicting_coordinates']==2


def test_classification_distinguishes_no_match_and_shared_boundaries():
    assert classify('a',[])=='outside_all'
    assert classify('a',['a'])=='match'
    assert classify('a',['b'])=='other_district'
    assert classify('a',['a','b'])=='ambiguous'


def test_mismatch_details_keep_reference_to_source_rows():
    point={'seq':1,'lon':'30.1','lat':'50.1','expected_id':'a','source_rows':[5,9]}
    result={'points':[{'seq':1,'matches':['b'],'expected_boundary_distance_m':12.5}]}
    details=mismatch_details([point],result)
    assert details[0]['source_record_numbers']=='5;9' and details[0]['polygon_district_ids']=='b'
    assert mismatch_details([point],{'points':[{'seq':1,'matches':['a'],'expected_boundary_distance_m':1}]})==[]


def test_index_checksum_accepts_git_crlf_conversion_only():
    assert index_digest(b'{\n"x":1\n}\n')==index_digest(b'{\r\n"x":1\r\n}\r\n')
    assert index_digest(b'{\n"x":1\n}\n')!=index_digest(b'{\n"x":2\n}\n')


def test_official_portal_parser_rejects_ambiguous_identity():
    h='<h4>Тестова районна в місті Києві державна адміністрація</h4><p>Код КАТОТТГ: UA80000000000126643</p>'
    assert parse_official_page(h)['code']=='UA80000000000126643'
    with pytest.raises(ValueError):parse_official_page(h+h)
