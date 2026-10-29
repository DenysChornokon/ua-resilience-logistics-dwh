BEGIN;

-- Range aggregation takes the union, including overlapping or adjacent alerts.
CREATE OR REPLACE VIEW mart.delivery_alert_exposure AS
WITH merged_alerts AS (
    SELECT region_id, unnest(range_agg(tstzrange(started_at, finished_at, '[)'))) AS alert_range
    FROM core.fact_alert
    WHERE event_type = 'Повітряна тривога'
    GROUP BY region_id
), exposure AS (
    SELECT d.delivery_id,
           COALESCE(SUM(EXTRACT(EPOCH FROM
             LEAST(d.finished_at, upper(m.alert_range)) -
             GREATEST(d.started_at, lower(m.alert_range))))
             FILTER (WHERE m.alert_range IS NOT NULL), 0) AS seconds
    FROM core.fact_delivery d
    LEFT JOIN merged_alerts m ON m.region_id = d.region_id
      AND tstzrange(d.started_at, d.finished_at, '[)') && m.alert_range
    GROUP BY d.delivery_id
), event_counts AS (
    SELECT d.delivery_id, count(a.event_id)::integer AS events
    FROM core.fact_delivery d
    LEFT JOIN core.fact_alert a ON a.region_id = d.region_id
      AND a.event_type = 'Повітряна тривога'
      AND d.started_at < a.finished_at AND a.started_at < d.finished_at
    GROUP BY d.delivery_id
)
SELECT d.delivery_id, e.seconds AS alert_overlap_seconds,
       e.seconds / EXTRACT(EPOCH FROM (d.finished_at - d.started_at)) AS alert_exposure_ratio,
       c.events AS overlap_alert_count
FROM core.fact_delivery d
JOIN exposure e USING (delivery_id)
JOIN event_counts c USING (delivery_id)
WHERE EXISTS (SELECT 1 FROM control.load_batch b
              WHERE b.source_type = 'alerts' AND b.status = 'success'
                AND b.rows_accepted > 0
                AND b.rows_accepted = (SELECT count(*) FROM core.fact_alert));

COMMIT;
