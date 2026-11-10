"""Audit candidate districts against a pinned Kyiv portal reference and public point data."""
import argparse
import csv
import hashlib
import io
import json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import subprocess

from scripts.capture_boundary_reference import OUTPUT,RESOURCE_URL
from scripts.district_candidate import SHA256 as CANDIDATE_SHA256
from scripts.load_spatial_research import COMMAND
from scripts.prepare_spatial_research import ROOT
from scripts.versioned_context import SPATIAL_SQL,assert_spatial_sources,spatial_query

INDEX_SHA='d0eebec0cd610f2c68b09e2b94bd2fb5e1a2edf897f0a016ad3e153448f60bd1'
FACILITIES_SHA='af48b25ddadf2eae046b2dc901bb4a72e0427f7c4868779a1256f0cbc72efb27'
INDEX_RETRIEVED='2026-11-10T13:57:02.001702+00:00'
INDEX_PATH=ROOT/'reports/kyiv_district_code_reference_20261110.json'


def index_digest(raw):
    # Git on Windows may check out text with CRLF; provenance is pinned to LF.
    return hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()


def norm(text):
    return str(text).strip().casefold().replace('’',"'").replace('ʼ',"'").replace('`',"'").removesuffix(' район').strip()


def read_references(directory=OUTPUT):
    directory=Path(directory);index=INDEX_PATH.read_bytes();facilities=(directory/'sportplaygrounds.json').read_bytes()
    meta=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if (meta.get('generator')!='kyiv_official_boundary_reference_v1' or meta.get('resource_page')!=RESOURCE_URL
        or index_digest(index)!=INDEX_SHA
        or hashlib.sha256(facilities).hexdigest()!=FACILITIES_SHA or meta.get('facilities_sha256')!=FACILITIES_SHA):
        raise ValueError('Reference snapshot checksum or provenance mismatch')
    index=json.loads(index)['records'];facilities=json.loads(facilities)
    if len(index)!=10 or len(facilities)!=meta['facilities_records']:raise ValueError('Reference count mismatch')
    return index,facilities,meta


def check_codes(features,index):
    official={r['code']:r for r in index}
    if len(official)!=10:raise ValueError('Official district codes duplicate')
    result=[]
    for f in features:
        p=f['properties'];code=p['id'];name=p['name'];ref=official.get(code)
        stem=norm(name)[:-2]
        if not ref or not norm(ref['organization']).startswith(stem) or not ref['url'].startswith('https://data.kyivcity.gov.ua/organization/'):
            raise ValueError(f'District name or code differs from official portal: {code}')
        result.append({'id':code,'candidate_name':name,'official_organization':ref['organization'],'official_url':ref['url']})
    if set(official)!={x['id'] for x in result}:raise ValueError('Official district set differs')
    return result


def prepare_points(facilities,features):
    names={norm(f['properties']['name']):f['properties']['id'] for f in features}
    if len(names)!=10:raise ValueError('Candidate names are not unique')
    groups=defaultdict(Counter);source_rows=defaultdict(list);invalid=unknown=0
    for source_row,row in enumerate(facilities,1):
        try:
            lat=Decimal(str(row.get('lat')));lon=Decimal(str(row.get('lng')))
            if not lat.is_finite() or not lon.is_finite() or not (49<lat<52 and 29<lon<32):raise ValueError('coordinates')
        except (InvalidOperation,ValueError,TypeError):invalid+=1;continue
        label=norm(row.get('district'))
        if label not in names:unknown+=1;continue
        groups[(lon,lat)][names[label]]+=1
        source_rows[(lon,lat)].append(source_row)
    conflicting=sum(len(labels)>1 for labels in groups.values())
    points=[{'seq':i,'lon':str(coords[0]),'lat':str(coords[1]),'expected_id':next(iter(labels)),
             'source_rows':source_rows[coords]}
            for i,(coords,labels) in enumerate(sorted(groups.items()),1) if len(labels)==1]
    return points,{'records':len(facilities),'invalid_coordinates':invalid,'unknown_district_labels':unknown,
                   'valid_labelled_records':len(facilities)-invalid-unknown,'unique_coordinates':len(groups),
                   'duplicate_records_collapsed':len(facilities)-invalid-unknown-len(groups),
                   'conflicting_label_coordinates_excluded':conflicting,
                   'records_at_conflicting_coordinates':sum(sum(labels.values()) for labels in groups.values() if len(labels)>1),
                   'unique_points_checked':len(points)}


def classify(expected, matches):
    if not matches:return 'outside_all'
    if len(matches)>1:return 'ambiguous'
    return 'match' if matches[0]==expected else 'other_district'


def query_points(points):
    buff=io.StringIO(newline='');writer=csv.DictWriter(buff,fieldnames=('seq','lon','lat','expected_id'),lineterminator='\n',extrasaction='ignore');writer.writerows(points)
    sql=f"""\\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE audit_points(seq integer,lon double precision,lat double precision,expected_id text);
COPY audit_points (seq,lon,lat,expected_id) FROM STDIN WITH(FORMAT CSV);
{buff.getvalue()}\\.
SELECT json_build_object(
 'points',(SELECT json_agg(row_to_json(z) ORDER BY seq) FROM (
   SELECT p.seq,COALESCE(m.matches,ARRAY[]::text[]) AS matches,
     ST_Distance(ST_Transform(ST_SetSRID(ST_MakePoint(p.lon,p.lat),4326),32636),ST_Boundary(ST_Transform(e.geom,32636))) AS expected_boundary_distance_m
   FROM audit_points p JOIN spatial_research.candidate_districts e ON e.district_id=p.expected_id
   CROSS JOIN LATERAL (SELECT array_agg(d.district_id ORDER BY d.district_id) AS matches FROM spatial_research.candidate_districts d
    WHERE ST_Covers(d.geom,ST_SetSRID(ST_MakePoint(p.lon,p.lat),4326))) m) z),
 'shapes',(WITH u AS (SELECT ST_Union(geom) AS g FROM spatial_research.candidate_districts), h AS (SELECT geom AS g FROM spatial_research.historical_region)
  SELECT json_build_object('historical_km2',round((ST_Area(ST_Transform(h.g,32636))/1000000)::numeric,3),
   'candidate_km2',round((ST_Area(ST_Transform(u.g,32636))/1000000)::numeric,3),
   'intersection_km2',round((ST_Area(ST_Transform(ST_Intersection(h.g,u.g),32636))/1000000)::numeric,3),
   'historical_only_km2',round((ST_Area(ST_Transform(ST_Difference(h.g,u.g),32636))/1000000)::numeric,3),
   'candidate_only_km2',round((ST_Area(ST_Transform(ST_Difference(u.g,h.g),32636))/1000000)::numeric,3)) FROM u,h)
);
ROLLBACK;
"""
    proc=subprocess.run(COMMAND+['-q','-A','-t'],cwd=ROOT,input=sql.encode('utf-8'),capture_output=True,check=True)
    return json.loads(proc.stdout.decode('utf-8'),parse_float=Decimal)


def summarize(points,result):
    actual=result['points'] or []
    if len(actual)!=len(points):raise ValueError('Missing facility audit results')
    counts=Counter();by_district=defaultdict(Counter);distance=Counter();confusion=Counter()
    for point,row in zip(points,actual):
        if point['seq']!=row['seq']:raise ValueError('Facility result ordering differs')
        status=classify(point['expected_id'],row['matches']);counts[status]+=1;by_district[point['expected_id']][status]+=1
        if status in ('other_district','outside_all'):
            d=Decimal(row['expected_boundary_distance_m'])
            distance['lt_100m' if d<100 else '100_to_500m' if d<500 else 'at_least_500m']+=1
        if status=='other_district':confusion[(point['expected_id'],row['matches'][0])]+=1
    return {'status_counts':{k:counts[k] for k in ('match','other_district','outside_all','ambiguous')},
            'district_counts':{k:dict(v) for k,v in by_district.items()},
            'other_district_pairs':[{'expected_id':a,'polygon_id':b,'points':n} for (a,b),n in sorted(confusion.items())],
            'mismatch_distance_from_expected_boundary':{k:distance[k] for k in ('lt_100m','100_to_500m','at_least_500m')}}


def mismatch_details(points,result):
    result_rows=result['points'] or []
    if len(points)!=len(result_rows):raise ValueError('Missing facility audit results')
    details=[]
    for point,row in zip(points,result_rows):
        if point['seq']!=row['seq']:raise ValueError('Facility result ordering differs')
        status=classify(point['expected_id'],row['matches'])
        if status!='match':
            details.append({'source_record_numbers':';'.join(map(str,point['source_rows'])),
               'longitude':point['lon'],'latitude':point['lat'],'official_district_id':point['expected_id'],
               'polygon_district_ids':';'.join(row['matches']),'status':status,
               'distance_to_expected_boundary_m':str(row['expected_boundary_distance_m'])})
    return details


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);parser.add_argument('--details',type=Path);args=parser.parse_args()
    if args.output and args.details and args.output.resolve()==args.details.resolve():raise ValueError('Output paths must differ')
    for path in (args.output,args.details):
        if path and path.exists():raise FileExistsError(f'Output already exists: {path}')
    features,_,_,fingerprint=assert_spatial_sources();index,facilities,meta=read_references()
    codes=check_codes(features,index);points,quality=prepare_points(facilities,features)
    if not points:raise ValueError('No comparable official points')
    result=query_points(points)
    if spatial_query(SPATIAL_SQL)!=fingerprint:raise RuntimeError('Spatial source changed during audit')
    summary=summarize(points,result)
    present={p['expected_id'] for p in points}
    summary['districts_without_reference_points']=[{'id':f['properties']['id'],'name':f['properties']['name']} for f in features if f['properties']['id'] not in present]
    result['shapes']={k:float(v) for k,v in result['shapes'].items()}
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'candidate_sha256':CANDIDATE_SHA256,
            'official_code_reference':{'retrieved_at_utc':INDEX_RETRIEVED,'index_sha256':INDEX_SHA,'districts':codes},
            'facility_source':{'url':RESOURCE_URL,'dataset_sha256':FACILITIES_SHA,'retrieved_at_utc':meta['retrieved_at_utc'],
                               'scope':'separately published public municipal facilities; coordinate and label errors possible'},
            'facility_quality':quality,'facility_comparison':summary,'shape_comparison_km2':result['shapes'],
            'limits':'not authoritative boundary certification; source point coverage is incomplete and not random; historical comparison is not a ground truth'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    if args.details:
        details=mismatch_details(points,result);args.details.parent.mkdir(parents=True,exist_ok=True)
        with args.details.open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=('source_record_numbers','longitude','latitude','official_district_id','polygon_district_ids','status','distance_to_expected_boundary_m'))
            writer.writeheader();writer.writerows(details)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
