"""Versioned, transactionally activated temporal context for the spatial prototype."""
import csv
import io
import json
import subprocess
from decimal import Decimal
from uuid import UUID

from scripts.district_candidate import COMMIT, SHA256, inspect as inspect_districts
from scripts.load_district_context import FIELDS, SCENARIO, spatial_points_match, validate_rows
from scripts.load_spatial_research import COMMAND, literal
from scripts.prepare_spatial_research import ROOT, inspect as inspect_points
from scripts.verify_district_context import snapshot_hash

VERSION='spatial_research.context_versions'
VERSION_ROWS='spatial_research.context_version_rows'
ACTIVE='spatial_research.context_active_version'
VIEW='spatial_research.delivery_district_context_v2'
NUMERIC={'alert_overlap_seconds','alert_exposure_ratio','temperature_c','wind_speed_kmh','precipitation_previous_hour_mm'}
SPATIAL_SQL="""SELECT json_build_object(
 'districts',(SELECT md5(string_agg(json_build_array(district_id,district_name,encode(ST_AsEWKB(geom),'hex'))::text,'' ORDER BY district_id)) FROM spatial_research.candidate_districts),
 'points',(SELECT md5(string_agg(json_build_array(delivery_id,region_id,started_at,encode(ST_AsEWKB(point),'hex'),synthetic)::text,'' ORDER BY delivery_id)) FROM spatial_research.delivery_points),
 'history',(SELECT md5(string_agg(json_build_array(shape_id,reference_year,encode(ST_AsEWKB(geom),'hex'))::text,'' ORDER BY shape_id)) FROM spatial_research.historical_region),
 'manifest',(SELECT md5(string_agg(row_to_json(m)::text,'' ORDER BY id)) FROM spatial_research.source_manifest m))"""
SPATIAL_LOCK='LOCK TABLE spatial_research.candidate_districts,spatial_research.delivery_points,spatial_research.historical_region,spatial_research.source_manifest IN SHARE MODE;'


def row_pairs(alias=''):
    prefix=f'{alias}.' if alias else ''
    return ','.join(f"'{k}',{prefix}{k}{'::text' if k in NUMERIC else ''}" for k in FIELDS)


def spatial_query(sql):
    p=subprocess.run(COMMAND+['-A','-t','-c',sql],cwd=ROOT,capture_output=True,check=True)
    return json.loads(p.stdout.decode('utf-8'),parse_float=Decimal)


def assert_spatial_sources():
    fingerprint=spatial_query(SPATIAL_SQL)
    features=inspect_districts();geo,points,meta=inspect_points();spatial_points_match(geo,points,meta)
    checks=[]
    for f in features:
        p=f['properties'];ident=literal(p['id']);geom=literal(json.dumps(f['geometry'],ensure_ascii=True,separators=(',',':')))
        checks.append(f"SELECT {ident} AS id, district_name={literal(p['name'])} AND ST_Equals(geom,ST_SetSRID(ST_GeomFromGeoJSON({geom}),4326)) AND ST_IsValid(geom) AS ok FROM spatial_research.candidate_districts WHERE district_id={ident}")
    sql=f"""SELECT json_build_object('count',(SELECT count(*) FROM spatial_research.candidate_districts),
       'checks',(SELECT json_agg(row_to_json(q) ORDER BY id) FROM ({' UNION ALL '.join(checks)}) q),
       'overlaps',(SELECT count(*) FROM spatial_research.candidate_districts a JOIN spatial_research.candidate_districts b ON a.district_id<b.district_id WHERE ST_Relate(a.geom,b.geom,'T********')));"""
    check=spatial_query(sql)
    if check['count']!=10 or len(check['checks'] or [])!=10 or not all(x['ok'] for x in check['checks']) or check['overlaps']:
        raise ValueError('Candidate district data differs from validated source')
    if spatial_query(SPATIAL_SQL)!=fingerprint:raise ValueError('Spatial inputs changed during verification')
    return features,points,meta,fingerprint


def guard_spatial(fingerprint):
    expected=literal(json.dumps(fingerprint,sort_keys=True,separators=(',',':')))
    return f"""{SPATIAL_LOCK}
DO $$ BEGIN
 IF (({SPATIAL_SQL})::jsonb) IS DISTINCT FROM {expected}::jsonb THEN RAISE EXCEPTION 'Spatial inputs changed; retry'; END IF;
END $$;
"""


def legacy():
    return spatial_query(f"""SELECT json_build_object('metadata',(SELECT details FROM spatial_research.district_context_metadata WHERE id=1),
       'rows',(SELECT json_agg(json_build_object({row_pairs()}) ORDER BY delivery_id) FROM spatial_research.temporal_context_snapshot));""")


def active():
    return spatial_query(f"""SELECT json_build_object('version_id',a.version_id,'metadata',v.metadata,'row_count',v.row_count,'spatial_fingerprint',v.spatial_fingerprint,
       'scenario_id',v.scenario_id,'context_sha256',v.context_sha256,'captured_at',v.captured_at,
       'rows',(SELECT json_agg(json_build_object({row_pairs()}) ORDER BY delivery_id) FROM {VERSION_ROWS} WHERE version_id=a.version_id))
       FROM {ACTIVE} a JOIN {VERSION} v USING(version_id) WHERE a.singleton=true;""")


def validate_snapshot(data,points,point_meta):
    meta=data['metadata'];rows=data['rows'] or []
    if (meta['candidate_commit']!=COMMIT or meta['candidate_sha256']!=SHA256 or meta['scenario_id']!=SCENARIO
        or meta['point_source_manifest']!=point_meta or snapshot_hash(rows)!=meta['context_sha256']):
        raise ValueError('Snapshot metadata, source, or hash mismatch')
    validate_rows(rows,points)
    if 'row_count' in data and data['row_count']!=len(rows):raise ValueError('Snapshot row count mismatch')
    if 'version_id' in data and (data['scenario_id']!=meta['scenario_id'] or data['context_sha256']!=meta['context_sha256']):
        raise ValueError('Version columns differ from metadata')
    if 'captured_at' in data:
        from scripts.verify_multifactor_states import parse
        if parse(data['captured_at'])!=parse(meta['captured_at_utc']):raise ValueError('Version timestamp differs from metadata')


def id_literal(value):
    return literal(str(UUID(str(value))))+'::uuid'


def row_copy(rows):
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=FIELDS,lineterminator='\n');w.writerows(rows);return f.getvalue()


def insertion_sql(version_id, rows, metadata, spatial_fingerprint):
    if snapshot_hash(rows)!=metadata['context_sha256']:raise ValueError('Snapshot rows do not match metadata hash')
    ident=id_literal(version_id);meta=literal(json.dumps(metadata,ensure_ascii=True,separators=(',',':')))
    spatial=literal(json.dumps(spatial_fingerprint,sort_keys=True,separators=(',',':')))
    expected=literal(json.dumps(rows,ensure_ascii=True,separators=(',',':')));fields=','.join(FIELDS)
    return f"""INSERT INTO {VERSION}(version_id,captured_at,scenario_id,context_sha256,row_count,metadata,spatial_fingerprint)
VALUES ({ident},({meta}::jsonb->>'captured_at_utc')::timestamptz,{literal(SCENARIO)},({meta}::jsonb->>'context_sha256'),{len(rows)},{meta}::jsonb,{spatial}::jsonb);
CREATE TEMP TABLE version_stage (LIKE spatial_research.temporal_context_snapshot INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
COPY version_stage ({','.join(FIELDS)}) FROM STDIN WITH (FORMAT CSV);
{row_copy(rows)}\\.
INSERT INTO {VERSION_ROWS}(version_id,{','.join(FIELDS)}) SELECT {ident},{','.join(FIELDS)} FROM version_stage;
DO $$ BEGIN
 IF (SELECT count(*) FROM {VERSION_ROWS} WHERE version_id={ident})<>{len(rows)} OR
    EXISTS ((SELECT delivery_id,region_id,started_at FROM spatial_research.delivery_points
             EXCEPT SELECT delivery_id,region_id,delivery_started_at FROM {VERSION_ROWS} WHERE version_id={ident})
      UNION ALL (SELECT delivery_id,region_id,delivery_started_at FROM {VERSION_ROWS} WHERE version_id={ident}
             EXCEPT SELECT delivery_id,region_id,started_at FROM spatial_research.delivery_points))
 THEN RAISE EXCEPTION 'Version rows do not exactly match the synthetic points'; END IF;
 IF EXISTS ((SELECT {fields} FROM {VERSION_ROWS} WHERE version_id={ident}
             EXCEPT ALL SELECT {fields} FROM jsonb_populate_recordset(NULL::spatial_research.temporal_context_snapshot,{expected}::jsonb))
      UNION ALL (SELECT {fields} FROM jsonb_populate_recordset(NULL::spatial_research.temporal_context_snapshot,{expected}::jsonb)
             EXCEPT ALL SELECT {fields} FROM {VERSION_ROWS} WHERE version_id={ident}))
 THEN RAISE EXCEPTION 'Version contents differ from captured data'; END IF;
END $$;
"""


def view_sql():
    return f"""CREATE VIEW {VIEW} AS
SELECT p.delivery_id,p.region_id,p.started_at,ST_X(p.point) AS longitude,ST_Y(p.point) AS latitude,p.synthetic,
 m.matches,CASE WHEN m.matches=1 THEN m.ids[1] END AS district_id,CASE WHEN m.matches=1 THEN m.names[1] END AS district_name,
 CASE WHEN m.matches=0 THEN 'unmatched' WHEN m.matches=1 THEN 'matched' ELSE 'ambiguous' END AS district_match_status,
 c.delivery_finished_at,c.alert_data_available,c.alert_overlap_seconds,c.alert_exposure_ratio,c.overlap_alert_count,
 c.weather_known,c.weather_observed_at,c.temperature_c,c.wind_speed_kmh,c.precipitation_previous_hour_mm,
 c.state_known,c.state_started_at,c.state_finished_at,c.alert_active,c.outage_active,
 (c.delivery_id IS NOT NULL) AS temporal_context_present,v.scenario_id,v.captured_at AS temporal_snapshot_utc,v.version_id
FROM spatial_research.delivery_points p
LEFT JOIN {ACTIVE} a ON a.singleton=true
LEFT JOIN {VERSION} v ON v.version_id=a.version_id
LEFT JOIN {VERSION_ROWS} c ON c.version_id=v.version_id AND c.delivery_id=p.delivery_id AND c.region_id=p.region_id AND c.delivery_started_at=p.started_at
CROSS JOIN LATERAL (SELECT count(*)::int AS matches,array_agg(district_id ORDER BY district_id) AS ids,array_agg(district_name ORDER BY district_id) AS names
 FROM spatial_research.candidate_districts d WHERE ST_Covers(d.geom,p.point)) m;"""


def setup_sql(version_id,rows,metadata,spatial_fingerprint):
    ident=id_literal(version_id);meta=literal(json.dumps(metadata,ensure_ascii=True,separators=(',',':')))
    fields=','.join(FIELDS)
    return f"""\\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(82991363);
LOCK TABLE spatial_research.temporal_context_snapshot,spatial_research.district_context_metadata,spatial_research.delivery_points IN SHARE MODE;
{guard_spatial(spatial_fingerprint)}
DO $$ BEGIN
 IF to_regclass('{VERSION}') IS NOT NULL OR to_regclass('{VERSION_ROWS}') IS NOT NULL OR to_regclass('{ACTIVE}') IS NOT NULL OR to_regclass('{VIEW}') IS NOT NULL
 THEN RAISE EXCEPTION 'Versioned objects already exist; nothing replaced'; END IF;
 IF (SELECT details FROM spatial_research.district_context_metadata WHERE id=1) IS DISTINCT FROM {meta}::jsonb
 THEN RAISE EXCEPTION 'Legacy metadata changed during setup'; END IF;
END $$;
CREATE TABLE {VERSION} (
 version_id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),captured_at timestamptz NOT NULL,
 scenario_id text NOT NULL,context_sha256 text NOT NULL CHECK(context_sha256 ~ '^[0-9a-f]{{64}}$'),
 row_count integer NOT NULL CHECK(row_count>0), metadata jsonb NOT NULL, spatial_fingerprint jsonb NOT NULL
);
CREATE TABLE {VERSION_ROWS} (version_id uuid NOT NULL REFERENCES {VERSION}(version_id),
 LIKE spatial_research.temporal_context_snapshot INCLUDING DEFAULTS INCLUDING CONSTRAINTS,PRIMARY KEY(version_id,delivery_id));
CREATE TABLE {ACTIVE} (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),version_id uuid NOT NULL REFERENCES {VERSION}(version_id));
{insertion_sql(version_id,rows,metadata,spatial_fingerprint)}
DO $$ BEGIN
 IF EXISTS ((SELECT {fields} FROM spatial_research.temporal_context_snapshot EXCEPT ALL SELECT {fields} FROM {VERSION_ROWS} WHERE version_id={ident})
 UNION ALL (SELECT {fields} FROM {VERSION_ROWS} WHERE version_id={ident} EXCEPT ALL SELECT {fields} FROM spatial_research.temporal_context_snapshot))
 THEN RAISE EXCEPTION 'Legacy data changed during setup'; END IF;
END $$;
INSERT INTO {ACTIVE}(singleton,version_id) VALUES(true,{ident});
{view_sql()}
DO $$ BEGIN
 IF (SELECT count(*) FROM {VIEW})<>{len(rows)} OR EXISTS(SELECT 1 FROM {VIEW} WHERE NOT temporal_context_present)
 THEN RAISE EXCEPTION 'Versioned view missing data'; END IF;
END $$;
COMMIT;
"""


def refresh_sql(expected_id,version_id,rows,metadata,spatial_fingerprint,finish='COMMIT;'):
    expected=id_literal(expected_id);ident=id_literal(version_id)
    if finish not in ('COMMIT;','ROLLBACK;'): raise ValueError('Unsupported transaction end')
    return f"""\\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(82991363);
{guard_spatial(spatial_fingerprint)}
DO $$ DECLARE current_id uuid; BEGIN
 SELECT version_id INTO current_id FROM {ACTIVE} WHERE singleton=true FOR UPDATE;
 IF current_id IS DISTINCT FROM {expected} THEN RAISE EXCEPTION 'Active version changed; retry refresh'; END IF;
END $$;
{insertion_sql(version_id,rows,metadata,spatial_fingerprint)}
UPDATE {ACTIVE} SET version_id={ident} WHERE singleton=true AND version_id={expected};
DO $$ BEGIN
 IF (SELECT version_id FROM {ACTIVE} WHERE singleton=true) IS DISTINCT FROM {ident}
 OR (SELECT count(*) FROM {VIEW})<>{len(rows)} OR EXISTS(SELECT 1 FROM {VIEW} WHERE NOT temporal_context_present)
 THEN RAISE EXCEPTION 'New version failed activation validation'; END IF;
END $$;
{finish}
"""
