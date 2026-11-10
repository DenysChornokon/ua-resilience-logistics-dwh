"""Compare all PostGIS matches with the independent synthetic-point reference."""
import argparse
import hashlib
import json
import subprocess
from decimal import Decimal
from pathlib import Path

from scripts.load_spatial_research import COMMAND, literal
from scripts.prepare_spatial_research import ROOT, inspect, relation
from scripts.verify_multifactor_states import parse


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    geo,rows,meta=inspect(); geometry=literal(json.dumps(geo,separators=(',',':')))
    sql=f"""SELECT json_build_object(
      'manifest',(SELECT details FROM spatial_research.source_manifest WHERE id=1),
      'version',postgis_full_version(),
      'view_definition',pg_get_viewdef('spatial_research.delivery_region'::regclass,true),
      'region_count',(SELECT count(*) FROM spatial_research.historical_region),
      'valid',(SELECT ST_IsValid(geom) FROM spatial_research.historical_region),
      'srid',(SELECT ST_SRID(geom) FROM spatial_research.historical_region),
      'equals_source',(SELECT ST_Equals(geom,ST_SetSRID(ST_GeomFromGeoJSON({geometry}),4326)) FROM spatial_research.historical_region),
      'covers_vertex',(SELECT ST_Covers(geom,ST_PointN(ST_ExteriorRing(geom),1)) FROM spatial_research.historical_region),
      'contains_vertex',(SELECT ST_Contains(geom,ST_PointN(ST_ExteriorRing(geom),1)) FROM spatial_research.historical_region),
      'covers_outside',(SELECT ST_Covers(geom,ST_SetSRID(ST_MakePoint(0,0),4326)) FROM spatial_research.historical_region),
      'weather_point_covered',(SELECT ST_Covers(geom,ST_SetSRID(ST_MakePoint(30.5234,50.4501),4326)) FROM spatial_research.historical_region),
      'rows',(SELECT json_agg(row_to_json(v) ORDER BY delivery_id) FROM spatial_research.delivery_region v)
    );"""
    proc=subprocess.run(COMMAND+['-A','-t','-c',sql],cwd=ROOT,capture_output=True,check=True)
    data=json.loads(proc.stdout.decode('utf-8'),parse_float=Decimal)
    if (data['manifest']!=meta or data['region_count']!=1 or not data['valid'] or data['srid']!=4326
        or not data['equals_source'] or not data['covers_vertex'] or data['contains_vertex'] or data['covers_outside']):
        raise AssertionError('Spatial source, geometry, or boundary checks failed')
    actual=data['rows'] or []
    if len(actual)!=len(rows) or len({r['delivery_id'] for r in actual})!=len(rows):
        raise AssertionError('Missing or duplicated deliveries')
    ring=geo['coordinates'][0]
    for source,row in zip(rows,actual):
        position=(float(source['longitude']),float(source['latitude']))
        if (row['delivery_id']!=source['delivery_id'] or row['region_id']!=source['region_id']
            or parse(row['started_at'])!=parse(source['started_at']) or Decimal(row['longitude'])!=Decimal(source['longitude'])
            or Decimal(row['latitude'])!=Decimal(source['latitude']) or not row['synthetic']
            or row['matched_shape_id']!=meta['geography_id'] or not row['within_historical_shape']
            or relation(position,ring)!='inside'):
            raise AssertionError(f"Spatial mismatch: {source['delivery_id']}")
    report={'source_manifest':meta,'postgis_version':data['version'],'region_valid':data['valid'],'srid':data['srid'],
            'source_geometry_equals_database':data['equals_source'],'matched_synthetic_deliveries':len(rows),
            'boundary_covered':data['covers_vertex'],'boundary_contained':data['contains_vertex'],
            'outside_covered':data['covers_outside'],'weather_point_in_historical_shape':data['weather_point_covered'],
            'view_sha256':hashlib.sha256(data['view_definition'].encode('utf-8')).hexdigest(),
            'scope':'historical 2006 geoBoundaries geometry and reproducible synthetic coordinates; not current district analysis'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
