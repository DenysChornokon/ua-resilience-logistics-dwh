"""Verify district matches, temporal snapshot, and its current freshness."""
import argparse
import hashlib
import json
import subprocess
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from scripts.district_candidate import COMMIT, SHA256, inspect as inspect_districts
from scripts.load_district_context import FIELDS, SCENARIO, capture, spatial_points_match
from scripts.load_spatial_research import COMMAND, literal
from scripts.prepare_spatial_research import ROOT, inspect as inspect_points, relation
from scripts.verify_multifactor_states import parse


def snapshot_hash(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()


def assert_fresh(current, metadata):
    current=dict(current);rows=current.pop('rows')
    if current!=metadata['source_fingerprint'] or snapshot_hash(rows)!=metadata['context_sha256']:
        raise RuntimeError('Temporal snapshot is stale: source database has changed')


def assert_temporal_fields(result, source):
    timestamps={'delivery_finished_at','weather_observed_at','state_started_at','state_finished_at'}
    numeric={'alert_overlap_seconds','alert_exposure_ratio','temperature_c','wind_speed_kmh','precipitation_previous_hour_mm'}
    for key in FIELDS[3:]:
        left,right=result[key],source[key]
        if left is None or right is None:
            if left is not right: raise AssertionError(f'Temporal field differs: {key}')
        elif key in timestamps:
            if parse(left)!=parse(right): raise AssertionError(f'Temporal timestamp differs: {key}')
        elif key in numeric:
            if Decimal(left)!=Decimal(right): raise AssertionError(f'Temporal value differs: {key}')
        elif left!=right: raise AssertionError(f'Temporal field differs: {key}')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    features=inspect_districts();geo,points,point_meta=inspect_points();spatial_points_match(geo,points,point_meta)
    checks=[]
    for f in features:
        ident=literal(f['properties']['id']); geom=literal(json.dumps(f['geometry'],ensure_ascii=True,separators=(',',':')))
        checks.append(f"SELECT {ident} AS id,ST_Equals(geom,ST_SetSRID(ST_GeomFromGeoJSON({geom}),4326)) AS eq FROM spatial_research.candidate_districts WHERE district_id={ident}")
    numeric={'alert_overlap_seconds','alert_exposure_ratio','temperature_c','wind_speed_kmh','precipitation_previous_hour_mm'}
    pairs=','.join(f"'{k}',{k}{'::text' if k in numeric else ''}" for k in FIELDS)
    sql=f"""SELECT json_build_object(
      'metadata',(SELECT details FROM spatial_research.district_context_metadata WHERE id=1),
      'districts',(SELECT json_agg(json_build_object('id',district_id,'name',district_name,'valid',ST_IsValid(geom),'srid',ST_SRID(geom)) ORDER BY district_id) FROM spatial_research.candidate_districts),
      'geometry_checks',(SELECT json_agg(row_to_json(q) ORDER BY id) FROM ({' UNION ALL '.join(checks)}) q),
      'overlap_pairs',(SELECT count(*) FROM spatial_research.candidate_districts a JOIN spatial_research.candidate_districts b ON a.district_id<b.district_id WHERE ST_Relate(a.geom,b.geom,'T********')),
      'snapshot_rows',(SELECT json_agg(json_build_object({pairs}) ORDER BY delivery_id) FROM spatial_research.temporal_context_snapshot),
      'rows',(SELECT json_agg(row_to_json(v) ORDER BY delivery_id) FROM spatial_research.delivery_district_context v),
      'view_definition',pg_get_viewdef('spatial_research.delivery_district_context'::regclass,true)
    );"""
    result=subprocess.run(COMMAND+['-A','-t','-c',sql],cwd=ROOT,capture_output=True,check=True)
    data=json.loads(result.stdout.decode('utf-8'),parse_float=Decimal);meta=data['metadata']
    if meta['candidate_commit']!=COMMIT or meta['candidate_sha256']!=SHA256 or meta['scenario_id']!=SCENARIO or meta['point_source_manifest']!=point_meta:
        raise AssertionError('Source metadata mismatch')
    expected_ids={f['properties']['id']:f['properties']['name'] for f in features}
    actual_districts=data['districts'] or []
    if len(actual_districts)!=10 or {x['id']:x['name'] for x in actual_districts}!=expected_ids or any(not x['valid'] or x['srid']!=4326 for x in actual_districts):
        raise AssertionError('Invalid, missing, or changed district geometries')
    if len(data['geometry_checks'] or [])!=10 or not all(x['eq'] for x in data['geometry_checks']):
        raise AssertionError('Database geometries differ from pinned candidate')
    if data['overlap_pairs']!=0: raise AssertionError('Candidate districts overlap in their interiors')
    if snapshot_hash(data['snapshot_rows'])!=meta['context_sha256']:
        raise AssertionError('Stored temporal snapshot differs from captured data')
    assert_fresh(capture(),meta)
    results=data['rows'] or []
    if len(results)!=len(points) or len({r['delivery_id'] for r in results})!=len(points):
        raise AssertionError('Missing or duplicate delivery results')
    context={r['delivery_id']:r for r in data['snapshot_rows']}
    counts={'matched':0,'unmatched':0,'ambiguous':0}
    for p,r in zip(points,results):
        position=(float(p['longitude']),float(p['latitude']))
        matches=[f for f in features if relation(position,f['geometry']['coordinates'][0])!='outside']
        status='unmatched' if not matches else 'matched' if len(matches)==1 else 'ambiguous'
        if (r['delivery_id']!=p['delivery_id'] or r['region_id']!=p['region_id'] or parse(r['started_at'])!=parse(p['started_at'])
            or Decimal(r['longitude'])!=Decimal(p['longitude']) or Decimal(r['latitude'])!=Decimal(p['latitude'])
            or r['matches']!=len(matches) or r['district_match_status']!=status
            or r['synthetic'] is not True or r['temporal_context_present'] is not True
            or r['scenario_id']!=SCENARIO or r['temporal_snapshot_utc']!=meta['captured_at_utc']):
            raise AssertionError(f"District match differs from independent reference: {p['delivery_id']}")
        assert_temporal_fields(r,context[p['delivery_id']])
        if len(matches)==1:
            if r['district_id']!=matches[0]['properties']['id'] or r['district_name']!=matches[0]['properties']['name']:
                raise AssertionError(f"Wrong district identity: {p['delivery_id']}")
        elif r['district_id'] is not None or r['district_name'] is not None:
            raise AssertionError('Ambiguous/unmatched result must not pick a district')
        counts[status]+=1
    report={'source_commit':COMMIT,'source_sha256':SHA256,'candidate_licence':meta['candidate_licence'],
            'source_note':'OSM-derived simplified experimental candidate; date of underlying geometry and authorisation for redistribution not established',
            'district_count':10,'valid_districts':10,'interior_overlap_pairs':data['overlap_pairs'],
            'delivery_count':len(points),'match_counts':counts,'scenario_id':SCENARIO,
            'temporal_snapshot_utc':meta['captured_at_utc'],'verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'temporal_snapshot_fresh_at_check':True,
            'temporal_context_sha256':meta['context_sha256'],'view_sha256':hashlib.sha256(data['view_definition'].encode('utf-8')).hexdigest(),
            'limitations':'synthetic points from historical 2006 outline; candidate boundaries unconfirmed and simplified; no real delivery conclusions'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
