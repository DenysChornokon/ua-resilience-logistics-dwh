"""Check the active version against source snapshots and the spatial reference."""
import argparse
import hashlib
import json
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path

from scripts.load_district_context import SCENARIO,capture
from scripts.prepare_spatial_research import relation
from scripts.verify_district_context import assert_fresh,assert_temporal_fields
from scripts.verify_multifactor_states import parse
from scripts.versioned_context import SPATIAL_SQL,VIEW,active,assert_spatial_sources,spatial_query,validate_snapshot


def verify():
    features,points,point_meta,spatial=assert_spatial_sources()
    version=active();validate_snapshot(version,points,point_meta)
    if version['spatial_fingerprint']!=spatial:raise RuntimeError('Spatial inputs changed since snapshot')
    assert_fresh(capture(),version['metadata'])
    data=spatial_query(f"""SELECT json_build_object('rows',(SELECT json_agg(row_to_json(v) ORDER BY delivery_id) FROM {VIEW} v),
     'view',pg_get_viewdef('{VIEW}'::regclass,true),
     'version_count',(SELECT count(*) FROM spatial_research.context_versions));""")
    rows=data['rows'] or []
    if len(rows)!=len(points) or len({r['delivery_id'] for r in rows})!=len(points):raise AssertionError('Missing or duplicate deliveries')
    contexts={r['delivery_id']:r for r in version['rows']};counts={'matched':0,'unmatched':0,'ambiguous':0}
    for point,row in zip(points,rows):
        matches=[f for f in features if relation((float(point['longitude']),float(point['latitude'])),f['geometry']['coordinates'][0])!='outside']
        status='unmatched' if not matches else 'matched' if len(matches)==1 else 'ambiguous'
        if (row['delivery_id']!=point['delivery_id'] or row['region_id']!=point['region_id']
            or parse(row['started_at'])!=parse(point['started_at']) or Decimal(str(row['longitude']))!=Decimal(point['longitude'])
            or Decimal(str(row['latitude']))!=Decimal(point['latitude']) or row['matches']!=len(matches)
            or row['district_match_status']!=status or row['synthetic'] is not True or row['temporal_context_present'] is not True
            or row['scenario_id']!=SCENARIO or row['version_id']!=version['version_id']
            or parse(row['temporal_snapshot_utc'])!=parse(version['metadata']['captured_at_utc'])):
            raise AssertionError(f"Versioned result mismatch: {point['delivery_id']}")
        assert_temporal_fields(row,contexts[point['delivery_id']])
        if len(matches)==1:
            if row['district_id']!=matches[0]['properties']['id'] or row['district_name']!=matches[0]['properties']['name']:
                raise AssertionError('Wrong district')
        elif row['district_id'] is not None or row['district_name'] is not None:
            raise AssertionError('Unmatched/ambiguous district must be NULL')
        counts[status]+=1
    after=active();validate_snapshot(after,points,point_meta)
    if after['version_id']!=version['version_id'] or after['spatial_fingerprint']!=spatial or spatial_query(SPATIAL_SQL)!=spatial:
        raise RuntimeError('Active version or spatial data changed during verification')
    assert_fresh(capture(),after['metadata'])
    return {'active_version_id':version['version_id'],'version_count':data['version_count'],
            'delivery_count':len(rows),'match_counts':counts,'scenario_id':SCENARIO,
            'captured_at_utc':version['metadata']['captured_at_utc'],'verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'source_fresh_at_check':True,'context_sha256':version['metadata']['context_sha256'],
            'view_sha256':hashlib.sha256(data['view'].encode('utf-8')).hexdigest(),
            'scope':'experimental, synthetic points, unconfirmed candidate districts; consistency checked at verification times'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    report=verify()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
