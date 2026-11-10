"""Create an experimental district join and a verified temporal snapshot."""
import csv
import hashlib
import io
import json
import subprocess
from datetime import datetime, timezone

from scripts.district_candidate import COMMIT, SHA256, inspect as inspect_districts
from scripts.load_spatial_research import COMMAND, literal
from scripts.prepare_spatial_research import ROOT, inspect as inspect_points
from scripts.run_delivery_density_benchmark import SNAPSHOT
from scripts.run_postgres_benchmark import query
from scripts.verify_delivery_weather import SQL as WEATHER_SQL, check as check_weather, read_csv
from scripts.verify_density_delivery_states import verify_all
from scripts.verify_multifactor_states import parse

SCENARIO='density_uniform_s42_v1'
FIELDS=('delivery_id','region_id','delivery_started_at','delivery_finished_at',
        'alert_data_available','alert_overlap_seconds','alert_exposure_ratio','overlap_alert_count',
        'weather_known','weather_observed_at','temperature_c','wind_speed_kmh','precipitation_previous_hour_mm',
        'state_known','state_started_at','state_finished_at','alert_active','outage_active')


def capture():
    base=SNAPSHOT.strip().rstrip(';')
    sql=f"""SELECT json_build_object(
      'base',({base}),
      'weather_md5',(SELECT md5(string_agg(row_to_json(w)::text,'' ORDER BY weather_location_id,observed_at)) FROM core.fact_weather_hour w),
      'weather_view',pg_get_viewdef('mart.delivery_start_weather'::regclass,true),
      'context_view',pg_get_viewdef('mart.delivery_context'::regclass,true),
      'alert_view',pg_get_viewdef('mart.delivery_alert_exposure'::regclass,true),
      'rows',(SELECT json_agg(json_build_object(
       'delivery_id',c.delivery_id,'region_id',c.region_id,'delivery_started_at',c.delivery_started_at,'delivery_finished_at',c.delivery_finished_at,
       'alert_data_available',c.alert_data_available,'alert_overlap_seconds',c.alert_overlap_seconds::text,'alert_exposure_ratio',c.alert_exposure_ratio::text,'overlap_alert_count',c.overlap_alert_count,
       'weather_known',c.weather_known,'weather_observed_at',c.weather_observed_at,'temperature_c',c.temperature_c::text,'wind_speed_kmh',c.wind_speed_kmh::text,'precipitation_previous_hour_mm',c.precipitation_previous_hour_mm::text,
       'state_known',s.state_known,'state_started_at',s.state_started_at,'state_finished_at',s.state_finished_at,'alert_active',s.alert_active,'outage_active',s.outage_active
      ) ORDER BY c.delivery_id) FROM mart.delivery_context c JOIN scenario.delivery_start_state s
       ON s.delivery_id=c.delivery_id AND s.region_id=c.region_id WHERE s.scenario_id='{SCENARIO}')
    );"""
    return json.loads(query(sql))


def validate_rows(rows,points):
    if len(rows)!=len(points) or len({r['delivery_id'] for r in rows})!=len(points):
        raise ValueError('Context rows missing or duplicated')
    for r,p in zip(rows,points):
        if (r['delivery_id']!=p['delivery_id'] or r['region_id']!=p['region_id']
            or parse(r['delivery_started_at'])!=parse(p['started_at'])):
            raise ValueError('Context and synthetic point identities or starts differ')


def spatial_points_match(geo,points,meta):
    geom=literal(json.dumps(geo,separators=(',',':')))
    sql=f"""SELECT json_build_object('manifest',(SELECT details FROM spatial_research.source_manifest WHERE id=1),
      'same_geometry',(SELECT ST_Equals(geom,ST_SetSRID(ST_GeomFromGeoJSON({geom}),4326)) FROM spatial_research.historical_region),
      'rows',(SELECT json_agg(json_build_array(delivery_id,region_id,started_at,ST_X(point)::text,ST_Y(point)::text,synthetic) ORDER BY delivery_id) FROM spatial_research.delivery_points));"""
    result=subprocess.run(COMMAND+['-A','-t','-c',sql],cwd=ROOT,capture_output=True,check=True)
    data=json.loads(result.stdout.decode('utf-8'))
    if data['manifest']!=meta or not data['same_geometry'] or len(data['rows'] or [])!=len(points):
        raise ValueError('Existing spatial inputs do not match their sources')
    from decimal import Decimal
    for actual,p in zip(data['rows'],points):
        if (actual[0]!=p['delivery_id'] or actual[1]!=p['region_id'] or parse(actual[2])!=parse(p['started_at'])
            or Decimal(actual[3])!=Decimal(p['longitude']) or Decimal(actual[4])!=Decimal(p['latitude']) or actual[5] is not True):
            raise ValueError('Existing spatial point differs from source')


def verify_sources(points):
    first=capture()
    paths={'weather':ROOT/'data/processed/kyiv_weather.csv','deliveries':ROOT/'data/synthetic/deliveries.csv'}
    hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}
    from decimal import Decimal
    weather=json.loads(query(WEATHER_SQL),parse_float=Decimal)
    weather_counts=check_weather(weather,read_csv(paths['deliveries']),read_csv(paths['weather']),hashes)
    outcomes=verify_all()
    if not any(x['scenario_id']==SCENARIO for x in outcomes): raise ValueError('Selected scenario not verified')
    if capture()!=first: raise RuntimeError('Source changed during verification')
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=hashes[k] for k,p in paths.items()): raise RuntimeError('Source file changed during verification')
    validate_rows(first['rows'] or [],points)
    return first,weather_counts


def build_sql(features,rows,metadata):
    data=io.StringIO(newline='');writer=csv.DictWriter(data,fieldnames=FIELDS,lineterminator='\n');writer.writerows(rows)
    statements=[]
    for f in features:
        p=f['properties'];geom=literal(json.dumps(f['geometry'],ensure_ascii=True,separators=(',',':')))
        statements.append(f"INSERT INTO spatial_research.candidate_districts VALUES ({literal(p['id'])},{literal(p['name'])},ST_SetSRID(ST_GeomFromGeoJSON({geom}),4326));\n")
    districts=''.join(statements);meta=literal(json.dumps(metadata,ensure_ascii=True,separators=(',',':')))
    return f"""\\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(82991362);
DO $$ BEGIN
 IF to_regclass('spatial_research.candidate_districts') IS NOT NULL OR
    to_regclass('spatial_research.temporal_context_snapshot') IS NOT NULL OR
    to_regclass('spatial_research.district_context_metadata') IS NOT NULL OR
    to_regclass('spatial_research.delivery_district_context') IS NOT NULL
 THEN RAISE EXCEPTION 'District context objects already exist; no objects replaced'; END IF;
END $$;
CREATE TABLE spatial_research.candidate_districts (
 district_id text PRIMARY KEY, district_name text NOT NULL,
 geom geometry(Polygon,4326) NOT NULL CHECK(ST_IsValid(geom))
);
{districts}
CREATE INDEX candidate_districts_geom_gist ON spatial_research.candidate_districts USING gist(geom);
CREATE TABLE spatial_research.district_context_metadata (id integer PRIMARY KEY CHECK(id=1),details jsonb NOT NULL);
INSERT INTO spatial_research.district_context_metadata VALUES (1,{meta}::jsonb);
CREATE TABLE spatial_research.temporal_context_snapshot (
 delivery_id text PRIMARY KEY,region_id text NOT NULL,delivery_started_at timestamptz NOT NULL,delivery_finished_at timestamptz NOT NULL,
 alert_data_available boolean NOT NULL,alert_overlap_seconds numeric,alert_exposure_ratio numeric,overlap_alert_count integer,
 weather_known boolean NOT NULL,weather_observed_at timestamptz,temperature_c numeric,wind_speed_kmh numeric,precipitation_previous_hour_mm numeric,
 state_known boolean NOT NULL,state_started_at timestamptz,state_finished_at timestamptz,alert_active boolean,outage_active boolean
);
COPY spatial_research.temporal_context_snapshot ({','.join(FIELDS)}) FROM STDIN WITH (FORMAT CSV);
{data.getvalue()}\\.
CREATE VIEW spatial_research.delivery_district_context AS
SELECT p.delivery_id,p.region_id,p.started_at,ST_X(p.point) AS longitude,ST_Y(p.point) AS latitude,p.synthetic,
       m.matches, CASE WHEN m.matches=1 THEN m.ids[1] END AS district_id,
       CASE WHEN m.matches=1 THEN m.names[1] END AS district_name,
       CASE WHEN m.matches=0 THEN 'unmatched' WHEN m.matches=1 THEN 'matched' ELSE 'ambiguous' END AS district_match_status,
       c.delivery_finished_at,c.alert_data_available,c.alert_overlap_seconds,c.alert_exposure_ratio,c.overlap_alert_count,
       c.weather_known,c.weather_observed_at,c.temperature_c,c.wind_speed_kmh,c.precipitation_previous_hour_mm,
       c.state_known,c.state_started_at,c.state_finished_at,c.alert_active,c.outage_active,
       (c.delivery_id IS NOT NULL) AS temporal_context_present,
       meta.details->>'scenario_id' AS scenario_id,meta.details->>'captured_at_utc' AS temporal_snapshot_utc
FROM spatial_research.delivery_points p
LEFT JOIN spatial_research.temporal_context_snapshot c ON c.delivery_id=p.delivery_id AND c.region_id=p.region_id AND c.delivery_started_at=p.started_at
LEFT JOIN spatial_research.district_context_metadata meta ON meta.id=1
CROSS JOIN LATERAL (
 SELECT count(*)::int AS matches,array_agg(district_id ORDER BY district_id) AS ids,array_agg(district_name ORDER BY district_id) AS names
 FROM spatial_research.candidate_districts d WHERE ST_Covers(d.geom,p.point)
) m;
ANALYZE spatial_research.candidate_districts;
ANALYZE spatial_research.temporal_context_snapshot;
DO $$ BEGIN
 IF (SELECT count(*) FROM spatial_research.candidate_districts)<>10 OR
    (SELECT count(*) FROM spatial_research.temporal_context_snapshot)<>{len(rows)} OR
    (SELECT count(*) FROM spatial_research.delivery_district_context)<>{len(rows)} OR
    EXISTS (SELECT 1 FROM spatial_research.delivery_district_context WHERE NOT temporal_context_present)
 THEN RAISE EXCEPTION 'District or context row count mismatch'; END IF;
END $$;
COMMIT;
"""


def main():
    features=inspect_districts();geo,points,point_meta=inspect_points()
    spatial_points_match(geo,points,point_meta)
    snapshot,weather_counts=verify_sources(points)
    rows=snapshot.pop('rows')
    canonical=json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    metadata={'candidate_commit':COMMIT,'candidate_sha256':SHA256,'candidate_licence':'OSM-derived; transformation and redistribution terms not confirmed',
              'scenario_id':SCENARIO,'captured_at_utc':datetime.now(timezone.utc).isoformat(),
              'context_sha256':hashlib.sha256(canonical).hexdigest(),'source_fingerprint':snapshot,
              'point_source_manifest':point_meta,'weather_verification':weather_counts}
    subprocess.run(COMMAND,cwd=ROOT,input=build_sql(features,rows,metadata).encode('utf-8'),check=True)
    print(f'Created experimental district context for {len(rows)} synthetic deliveries.')


if __name__=='__main__':main()
