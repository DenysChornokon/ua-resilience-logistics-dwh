"""Load the verified synthetic point experiment into the isolated PostGIS database."""
import csv
import io
import json
import subprocess

from scripts.prepare_spatial_research import ROOT, FIELDS, inspect

COMMAND=['docker','compose','-f','compose.yaml','-f','compose.spatial.yaml','--profile','spatial-research',
         'exec','-T','spatial-db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','spatial_research']


def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def build_sql(geo, rows, meta):
    csv_io=io.StringIO(newline=''); writer=csv.DictWriter(csv_io,fieldnames=FIELDS,lineterminator='\n');writer.writerows(rows)
    geometry=literal(json.dumps(geo,ensure_ascii=True,separators=(',',':')))
    metadata=literal(json.dumps(meta,ensure_ascii=True,separators=(',',':')))
    return f"""\\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(82991361);
CREATE SCHEMA IF NOT EXISTS spatial_research;
DO $$ BEGIN
 IF to_regclass('spatial_research.historical_region') IS NOT NULL
    OR to_regclass('spatial_research.delivery_points') IS NOT NULL
    OR to_regclass('spatial_research.source_manifest') IS NOT NULL
    OR to_regclass('spatial_research.delivery_region') IS NOT NULL
 THEN RAISE EXCEPTION 'Spatial experiment objects already exist; no objects replaced'; END IF;
END $$;
CREATE TABLE spatial_research.source_manifest (id integer PRIMARY KEY CHECK(id=1), details jsonb NOT NULL);
INSERT INTO spatial_research.source_manifest VALUES (1,{metadata}::jsonb);
CREATE TABLE spatial_research.historical_region (
 shape_id text PRIMARY KEY, reference_year integer NOT NULL,
 geom geometry(Polygon,4326) NOT NULL CHECK (ST_IsValid(geom))
);
INSERT INTO spatial_research.historical_region VALUES
 ('{meta['geography_id']}',2006,ST_SetSRID(ST_GeomFromGeoJSON({geometry}),4326));
CREATE INDEX historical_region_geom_gist ON spatial_research.historical_region USING gist (geom);
CREATE TABLE spatial_research.delivery_points (
 delivery_id text PRIMARY KEY, region_id text NOT NULL, started_at timestamptz NOT NULL,
 point geometry(Point,4326) NOT NULL, synthetic boolean NOT NULL CHECK(synthetic)
);
CREATE TEMP TABLE spatial_points_stage (delivery_id text,region_id text,started_at timestamptz,longitude double precision,latitude double precision);
COPY spatial_points_stage (delivery_id,region_id,started_at,longitude,latitude) FROM STDIN WITH (FORMAT CSV);
{csv_io.getvalue()}\\.
INSERT INTO spatial_research.delivery_points
SELECT delivery_id,region_id,started_at,ST_SetSRID(ST_MakePoint(longitude,latitude),4326),true FROM spatial_points_stage;
CREATE INDEX delivery_points_geom_gist ON spatial_research.delivery_points USING gist (point);
ANALYZE spatial_research.historical_region;
ANALYZE spatial_research.delivery_points;
CREATE VIEW spatial_research.delivery_region AS
SELECT d.delivery_id,d.region_id,d.started_at,ST_X(d.point) AS longitude,ST_Y(d.point) AS latitude,
       d.synthetic,r.shape_id AS matched_shape_id,(r.shape_id IS NOT NULL) AS within_historical_shape
FROM spatial_research.delivery_points d
LEFT JOIN spatial_research.historical_region r ON ST_Covers(r.geom,d.point);
DO $$ BEGIN
 IF (SELECT count(*) FROM spatial_research.delivery_points)<>{len(rows)} OR
    (SELECT count(*) FROM spatial_research.delivery_region)<>{len(rows)} OR
    EXISTS (SELECT 1 FROM spatial_research.delivery_region WHERE NOT within_historical_shape)
 THEN RAISE EXCEPTION 'Missing points or spatial mismatch'; END IF;
END $$;
COMMIT;
"""


def main():
    geo,rows,meta=inspect()
    subprocess.run(COMMAND,cwd=ROOT,input=build_sql(geo,rows,meta).encode('utf-8'),check=True)
    print(f"Loaded {len(rows)} synthetic positions into the isolated spatial database.")


if __name__=='__main__': main()
