"""Generate reproducible synthetic delivery points inside the historical Kyiv polygon."""
import csv
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT/'data/raw/geography/geoBoundaries-UKR-ADM2.geojson'
DELIVERIES = ROOT/'data/synthetic/deliveries.csv'
OUTPUT = ROOT/'data/synthetic/spatial_research'
GEO_SHA256 = '34f1a1486c1b6126a3d79c010dbb8398edf495d1688066e3e975c25573dbad10'
DELIVERY_SHA256 = '498918feda47a06358198a73c8ac0eef86a7c1f2899e1431d0e43d4a36b94608'
KYIV_ID = '74538382B60890943260132'
REGION = 'UA80000000000093317'
FIELDS = ('delivery_id','region_id','started_at','longitude','latitude')
SEED = 42


def digest(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def source():
    if digest(GEO) != GEO_SHA256: raise ValueError('Geography source checksum mismatch')
    if digest(DELIVERIES) != DELIVERY_SHA256: raise ValueError('Synthetic delivery source checksum mismatch')
    obj=json.loads(GEO.read_text(encoding='utf-8-sig'))
    if obj.get('type')!='FeatureCollection' or obj.get('crs',{}).get('properties',{}).get('name')!='urn:ogc:def:crs:OGC:1.3:CRS84':
        raise ValueError('Unexpected geographic structure or CRS')
    features=[f for f in obj.get('features',[]) if f.get('properties',{}).get('shapeID')==KYIV_ID]
    if len(features)!=1: raise ValueError('Expected one historical Kyiv feature')
    feature=features[0]; geo=feature['geometry']; props=feature['properties']
    if props.get('shapeName')!='Kyiv' or geo.get('type')!='Polygon' or len(geo.get('coordinates',[]))!=1:
        raise ValueError('Unexpected historical polygon')
    ring=geo['coordinates'][0]
    if len(ring)<4 or ring[0]!=ring[-1] or any(len(p)!=2 or not (-180<=p[0]<=180 and -90<=p[1]<=90) for p in ring):
        raise ValueError('Invalid geographic ring')
    with DELIVERIES.open(encoding='utf-8-sig',newline='') as f: deliveries=list(csv.DictReader(f))
    if len(deliveries)!=500 or len({d['delivery_id'] for d in deliveries})!=len(deliveries): raise ValueError('Expected 500 unique synthetic deliveries')
    for d in deliveries:
        if d['region_id']!=REGION or datetime.fromisoformat(d['started_at'].replace('Z','+00:00')).utcoffset() is None:
            raise ValueError('Unexpected delivery region or timestamp')
    return geo, sorted(deliveries,key=lambda x:x['delivery_id'])


def relation(point, ring):
    """Planar ray-casting reference with boundary reported separately."""
    x,y=point; inside=False
    for (ax,ay),(bx,by) in zip(ring,ring[1:]):
        cross=(x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross)<1e-12 and min(ax,bx)-1e-12<=x<=max(ax,bx)+1e-12 and min(ay,by)-1e-12<=y<=max(ay,by)+1e-12:
            return 'boundary'
        if (ay>y)!=(by>y) and x < ax+(bx-ax)*(y-ay)/(by-ay): inside=not inside
    return 'inside' if inside else 'outside'


def points(geo, deliveries, seed=SEED):
    ring=geo['coordinates'][0]; xs=[p[0] for p in ring]; ys=[p[1] for p in ring]
    rng=random.Random(seed); result=[]; attempts=0
    for d in deliveries:
        while True:
            attempts+=1
            if attempts>len(deliveries)*10000: raise ValueError('Point generation did not converge')
            lon=round(rng.uniform(min(xs),max(xs)),8); lat=round(rng.uniform(min(ys),max(ys)),8)
            if relation((lon,lat),ring)=='inside': break
        result.append({'delivery_id':d['delivery_id'],'region_id':d['region_id'],'started_at':d['started_at'],
                       'longitude':f'{lon:.8f}','latitude':f'{lat:.8f}'})
    return result


def prepare(directory=OUTPUT):
    geo,deliveries=source(); rows=points(geo,deliveries)
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    csv_path=directory/'delivery_points.csv'; meta_path=directory/'manifest.json'
    if csv_path.exists() or meta_path.exists(): raise FileExistsError('Spatial output already exists')
    created=[]
    try:
        with csv_path.open('x',encoding='utf-8',newline='') as f:
            created.append(csv_path); writer=csv.DictWriter(f,fieldnames=FIELDS,lineterminator='\n');writer.writeheader();writer.writerows(rows)
        meta={'generator':'historical_kyiv_spatial_v1','seed':SEED,'synthetic_points':True,'region_id':REGION,
              'geography_sha256':GEO_SHA256,'geography_id':KYIV_ID,'geography_reference_year':2006,
              'delivery_source_sha256':digest(DELIVERIES),'points_sha256':digest(csv_path),'count':len(rows)}
        with meta_path.open('x',encoding='utf-8',newline='\n') as f:
            created.append(meta_path);json.dump(meta,f,ensure_ascii=False,indent=2);f.write('\n')
    except BaseException:
        for path in created: path.unlink(missing_ok=True)
        raise
    return meta


def inspect(directory=OUTPUT):
    directory=Path(directory); geo,deliveries=source(); meta=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    expected={'generator':'historical_kyiv_spatial_v1','seed':SEED,'synthetic_points':True,'region_id':REGION,
              'geography_sha256':GEO_SHA256,'geography_id':KYIV_ID,'geography_reference_year':2006,
              'delivery_source_sha256':digest(DELIVERIES),'points_sha256':digest(directory/'delivery_points.csv'),'count':len(deliveries)}
    if meta!=expected: raise ValueError('Spatial manifest mismatch')
    with (directory/'delivery_points.csv').open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or ())!=FIELDS: raise ValueError('Invalid point CSV header')
        rows=list(reader)
    if rows!=points(geo,deliveries): raise ValueError('Points differ from reproducible generator')
    return geo,rows,meta


if __name__=='__main__': print(json.dumps(prepare(),ensure_ascii=False,indent=2))
