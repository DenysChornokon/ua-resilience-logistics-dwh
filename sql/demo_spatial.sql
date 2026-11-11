-- Демонстрація: виконувати запити по одному в базі spatial_research.
-- Перед переглядом запустити перевірку актуальності з документа 35.
-- Контури неофіційні; точки, доставки та відключення синтетичні.

-- DEMO:active_version
SELECT v.version_id, v.scenario_id, v.row_count,
       v.captured_at AT TIME ZONE 'UTC' AS captured_at_utc,
       v.context_sha256
FROM spatial_research.context_active_version a
JOIN spatial_research.context_versions v USING (version_id)
WHERE a.singleton IS TRUE;

-- DEMO:match_counts
WITH statuses(status, sort_order) AS (
    VALUES ('matched', 1), ('unmatched', 2), ('ambiguous', 3)
)
SELECT s.status AS district_match_status, COUNT(v.delivery_id) AS deliveries
FROM statuses s
LEFT JOIN spatial_research.delivery_district_context_v2 v ON v.district_match_status = s.status
GROUP BY s.status, s.sort_order
ORDER BY s.sort_order;

-- DEMO:district_examples
SELECT delivery_id, started_at AT TIME ZONE 'Europe/Kyiv' AS delivery_started_kyiv,
       longitude, latitude, district_match_status, district_name,
       alert_data_available, alert_overlap_seconds,
       weather_known, temperature_c, precipitation_previous_hour_mm,
       state_known, alert_active, outage_active, scenario_id, version_id
FROM spatial_research.delivery_district_context_v2
WHERE district_match_status = 'matched'
ORDER BY delivery_id
LIMIT 10;

-- DEMO:district_summary
SELECT district_name, COUNT(*) AS synthetic_deliveries,
       COUNT(*) FILTER (WHERE alert_data_available IS TRUE AND alert_overlap_seconds > 0) AS alert_overlap_during_delivery,
       COUNT(*) FILTER (WHERE alert_data_available IS NOT TRUE OR alert_overlap_seconds IS NULL) AS unknown_alert_exposure,
       COUNT(*) FILTER (WHERE state_known IS TRUE AND outage_active IS TRUE) AS synthetic_outage_at_start,
       COUNT(*) FILTER (WHERE state_known IS NOT TRUE OR outage_active IS NULL) AS unknown_outage_state
FROM spatial_research.delivery_district_context_v2
WHERE district_match_status = 'matched'
GROUP BY district_name
ORDER BY synthetic_deliveries DESC, district_name;
