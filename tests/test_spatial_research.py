from scripts.prepare_spatial_research import points, relation


def test_point_relation_handles_interior_outside_and_boundary():
    square=[[0,0],[2,0],[2,2],[0,2],[0,0]]
    assert relation((1,1),square)=='inside'
    assert relation((3,1),square)=='outside'
    assert relation((0,1),square)=='boundary'
    assert relation((2,2),square)=='boundary'


def test_generation_is_deterministic_and_strictly_inside():
    square=[[0,0],[2,0],[2,2],[0,2],[0,0]]
    geo={'type':'Polygon','coordinates':[square]}
    deliveries=[{'delivery_id':'d1','region_id':'r','started_at':'2026-01-01T00:00:00+00:00'},
                {'delivery_id':'d2','region_id':'r','started_at':'2026-01-01T01:00:00+00:00'}]
    first=points(geo,deliveries,seed=42)
    assert first==points(geo,deliveries,seed=42)
    assert first!=points(geo,deliveries,seed=43)
    assert all(relation((float(r['longitude']),float(r['latitude'])),square)=='inside' for r in first)
