-- Fixtures are independent of the candidate polygons and are rolled back.
BEGIN;
INSERT INTO spatial_research.candidate_districts(district_id,district_name,geom) VALUES
 ('district-test-a','test-a',ST_GeomFromText('POLYGON((0 0,2 0,2 2,0 2,0 0))',4326)),
 ('district-test-b','test-b',ST_GeomFromText('POLYGON((2 0,4 0,4 2,2 2,2 0))',4326));
CREATE TEMP TABLE district_fixture_context AS SELECT * FROM spatial_research.temporal_context_snapshot ORDER BY delivery_id LIMIT 1;
INSERT INTO spatial_research.temporal_context_snapshot
SELECT v.id,c.region_id,c.delivery_started_at,c.delivery_finished_at,c.alert_data_available,c.alert_overlap_seconds,c.alert_exposure_ratio,c.overlap_alert_count,
       c.weather_known,c.weather_observed_at,c.temperature_c,c.wind_speed_kmh,c.precipitation_previous_hour_mm,
       c.state_known,c.state_started_at,c.state_finished_at,c.alert_active,c.outage_active
FROM district_fixture_context c CROSS JOIN (VALUES ('district-test-inside'),('district-test-border'),('district-test-outside')) v(id);
INSERT INTO spatial_research.delivery_points(delivery_id,region_id,started_at,point,synthetic)
SELECT v.id,c.region_id,c.delivery_started_at,ST_SetSRID(ST_MakePoint(v.x,v.y),4326),true
FROM district_fixture_context c CROSS JOIN (VALUES ('district-test-inside',1.0,1.0),('district-test-border',2.0,1.0),('district-test-outside',10.0,10.0)) v(id,x,y);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_district_context WHERE delivery_id='district-test-inside'
                AND matches=1 AND district_match_status='matched' AND district_id='district-test-a') THEN
   RAISE EXCEPTION 'Inside point must have one district'; END IF;
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_district_context WHERE delivery_id='district-test-border'
                AND matches=2 AND district_match_status='ambiguous' AND district_id IS NULL AND district_name IS NULL) THEN
   RAISE EXCEPTION 'Shared boundary must remain ambiguous'; END IF;
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_district_context WHERE delivery_id='district-test-outside'
                AND matches=0 AND district_match_status='unmatched' AND district_id IS NULL AND temporal_context_present) THEN
   RAISE EXCEPTION 'Outside delivery must be retained with its context'; END IF;
END $$;
DELETE FROM spatial_research.temporal_context_snapshot WHERE delivery_id='district-test-outside';
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM spatial_research.delivery_district_context WHERE delivery_id='district-test-outside'
                AND NOT temporal_context_present AND district_match_status='unmatched') THEN
   RAISE EXCEPTION 'Delivery without temporal context must remain visible'; END IF;
END $$;
ROLLBACK;
