BEGIN;

-- Latest UTC hour at the start of a delivery, for the one documented Kyiv point.
-- Precipitation is the source's sum over the hour before its observation time.
CREATE VIEW mart.delivery_start_weather AS
SELECT d.delivery_id, d.region_id, d.started_at AS delivery_started_at,
       w.weather_location_id, w.observed_at AS weather_observed_at,
       (w.observed_at IS NOT NULL) AS weather_known,
       w.temperature_2m AS temperature_c,
       w.wind_speed_10m AS wind_speed_kmh,
       w.precipitation AS precipitation_previous_hour_mm,
       w.load_id AS weather_load_id
FROM core.fact_delivery d
LEFT JOIN core.fact_weather_hour w
  ON d.region_id = 'UA80000000000093317'
 AND w.weather_location_id = 'kyiv_open_meteo'
 AND w.observed_at = (date_trunc('hour', d.started_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
 AND EXISTS (
   SELECT 1 FROM control.load_batch b
   WHERE b.load_id = w.load_id AND b.source_type = 'weather' AND b.status = 'success'
     AND b.rows_accepted > 0 AND b.rows_accepted = (SELECT count(*) FROM core.fact_weather_hour)
     AND NOT EXISTS (SELECT 1 FROM core.fact_weather_hour x WHERE x.load_id <> b.load_id)
 );

-- Left joins retain deliveries even if a source is unavailable.
CREATE VIEW mart.delivery_context AS
SELECT d.delivery_id, d.region_id, d.started_at AS delivery_started_at,
       d.finished_at AS delivery_finished_at,
       (a.delivery_id IS NOT NULL) AS alert_data_available,
       a.alert_overlap_seconds, a.alert_exposure_ratio, a.overlap_alert_count,
       w.weather_location_id, w.weather_observed_at, w.weather_known,
       w.temperature_c, w.wind_speed_kmh, w.precipitation_previous_hour_mm,
       w.weather_load_id
FROM core.fact_delivery d
LEFT JOIN mart.delivery_alert_exposure a ON a.delivery_id = d.delivery_id
JOIN mart.delivery_start_weather w ON w.delivery_id = d.delivery_id;

COMMIT;
