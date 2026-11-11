-- Демонстрація: виконувати запити по одному в базі logistics_dwh.
-- Час подано як місцевий час Києва; доставки та відключення синтетичні.

-- DEMO:source_counts
SELECT 'alerts' AS dataset, COUNT(*) AS rows_count FROM core.fact_alert
UNION ALL
SELECT 'deliveries_synthetic', COUNT(*) FROM core.fact_delivery
UNION ALL
SELECT 'weather_hourly', COUNT(*) FROM core.fact_weather_hour
ORDER BY dataset;

-- DEMO:overview
SELECT COUNT(*) AS total_deliveries,
       COUNT(*) FILTER (WHERE alert_data_available IS TRUE AND alert_overlap_seconds > 0) AS deliveries_with_alert_overlap,
       COUNT(*) FILTER (WHERE alert_data_available IS NOT TRUE OR alert_overlap_seconds IS NULL) AS unknown_alert_exposure,
       SUM(alert_overlap_seconds) AS total_alert_overlap_seconds,
       COUNT(*) FILTER (WHERE weather_known IS TRUE) AS weather_known,
       COUNT(*) FILTER (WHERE weather_known IS NOT TRUE) AS weather_unknown
FROM mart.delivery_context;

-- DEMO:delivery_examples
SELECT c.delivery_id,
       c.delivery_started_at AT TIME ZONE 'Europe/Kyiv' AS delivery_started_kyiv,
       c.delivery_finished_at AT TIME ZONE 'Europe/Kyiv' AS delivery_finished_kyiv,
       c.alert_data_available, c.alert_overlap_seconds,
       ROUND(c.alert_exposure_ratio * 100, 2) AS alert_exposure_percent,
       c.overlap_alert_count,
       c.weather_observed_at AT TIME ZONE 'Europe/Kyiv' AS weather_observed_kyiv,
       c.weather_known, c.temperature_c, c.wind_speed_kmh, c.precipitation_previous_hour_mm,
       (s.delivery_id IS NOT NULL) AS scenario_row_present,
       s.state_known, s.alert_active, s.outage_active
FROM mart.delivery_context c
LEFT JOIN scenario.delivery_start_state s
  ON s.delivery_id = c.delivery_id AND s.region_id = c.region_id
 AND s.scenario_id = 'density_uniform_s42_v1'
ORDER BY c.alert_overlap_seconds DESC NULLS LAST, c.delivery_id
LIMIT 10;

-- DEMO:scenario_counts
SELECT scenario_id, state_known, alert_active, outage_active, COUNT(*) AS deliveries
FROM scenario.delivery_start_state
WHERE scenario_id = 'density_uniform_s42_v1'
GROUP BY scenario_id, state_known, alert_active, outage_active
ORDER BY state_known DESC NULLS LAST, alert_active NULLS LAST, outage_active NULLS LAST;
