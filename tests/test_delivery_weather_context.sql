-- Test records and source-status changes are rolled back on success or error.
BEGIN;
SET LOCAL TIME ZONE 'Pacific/Chatham';
INSERT INTO core.dim_region(region_id,region_name) VALUES ('weather-test-other','weather-test-other');
INSERT INTO core.fact_weather_hour(weather_location_id,observed_at,temperature_2m,precipitation,wind_speed_10m,load_id)
SELECT 'kyiv_open_meteo',t::timestamptz,temp,rain,wind,b.load_id
FROM (VALUES ('2099-01-01 00:00:00+00',10,1.2,5),('2099-01-01 01:00:00+00',20,0.0,7)) AS v(t,temp,rain,wind)
CROSS JOIN control.load_batch b WHERE b.source_type='weather' AND b.status='success';
UPDATE control.load_batch SET rows_accepted=(SELECT count(*) FROM core.fact_weather_hour)
WHERE source_type='weather' AND status='success';
INSERT INTO core.fact_delivery(delivery_id,region_id,started_at,finished_at,distance_km,status,load_id)
SELECT id,region,t::timestamptz,t::timestamptz+interval '1 minute',1,'completed',b.load_id
FROM (VALUES
 ('weather-test-start','UA80000000000093317','2099-01-01 00:00:00+00'),
 ('weather-test-before','UA80000000000093317','2099-01-01 00:59:59.999999+00'),
 ('weather-test-boundary','UA80000000000093317','2099-01-01 01:00:00+00'),
 ('weather-test-missing','UA80000000000093317','2099-01-01 02:00:00+00'),
 ('weather-test-other','weather-test-other','2099-01-01 00:30:00+00')
) AS v(id,region,t) CROSS JOIN control.load_batch b
WHERE b.source_type='deliveries' AND b.status='success';

DO $$ BEGIN
 IF (SELECT count(*) FROM mart.delivery_start_weather WHERE delivery_id LIKE 'weather-test-%')<>5 THEN
   RAISE EXCEPTION 'Deliveries missing or duplicated';
 END IF;
 IF (SELECT count(*) FROM mart.delivery_start_weather WHERE delivery_id IN ('weather-test-start','weather-test-before')
     AND weather_known AND weather_observed_at='2099-01-01 00:00:00+00'::timestamptz
     AND temperature_c=10 AND precipitation_previous_hour_mm=1.2 AND wind_speed_kmh=5)<>2 THEN
   RAISE EXCEPTION 'Beginning or last instant of hour incorrect';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM mart.delivery_start_weather WHERE delivery_id='weather-test-boundary'
     AND weather_known AND weather_observed_at='2099-01-01 01:00:00+00'::timestamptz
     AND temperature_c=20 AND precipitation_previous_hour_mm=0 AND wind_speed_kmh=7) THEN
   RAISE EXCEPTION 'Next-hour boundary or zero precipitation incorrect';
 END IF;
 IF (SELECT count(*) FROM mart.delivery_start_weather WHERE delivery_id IN ('weather-test-missing','weather-test-other')
     AND NOT weather_known AND weather_observed_at IS NULL AND temperature_c IS NULL
     AND precipitation_previous_hour_mm IS NULL AND wind_speed_kmh IS NULL)<>2 THEN
   RAISE EXCEPTION 'Missing hour or other region must remain unknown';
 END IF;
 IF (SELECT count(*) FROM mart.delivery_context WHERE delivery_id LIKE 'weather-test-%')<>5 THEN
   RAISE EXCEPTION 'Context must retain each delivery';
 END IF;
END $$;

UPDATE control.load_batch SET status='failed' WHERE source_type IN ('weather','alerts') AND status='success';
DO $$ BEGIN
 IF (SELECT count(*) FROM mart.delivery_context WHERE delivery_id LIKE 'weather-test-%'
     AND NOT weather_known AND weather_observed_at IS NULL AND precipitation_previous_hour_mm IS NULL
     AND NOT alert_data_available AND alert_overlap_seconds IS NULL)<>5 THEN
   RAISE EXCEPTION 'Unavailable input must remain unknown and deliveries retained';
 END IF;
END $$;
ROLLBACK;
