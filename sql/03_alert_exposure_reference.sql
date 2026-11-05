BEGIN;
-- Baseline: ordinary comparisons; clip each alert to its delivery before merging.
CREATE OR REPLACE VIEW mart.delivery_alert_exposure_reference AS
WITH candidates AS (
    SELECT d.delivery_id, a.event_id,
           greatest(d.started_at, a.started_at) AS segment_start,
           least(d.finished_at, a.finished_at) AS segment_end
    FROM core.fact_delivery d
    JOIN core.fact_alert a ON a.region_id = d.region_id
      AND a.event_type = 'Повітряна тривога'
      AND d.started_at < a.finished_at AND a.started_at < d.finished_at
), marked AS (
    SELECT *, max(segment_end) OVER (
        PARTITION BY delivery_id ORDER BY segment_start, segment_end, event_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS preceding_end
    FROM candidates
), groups AS (
    SELECT *, sum(CASE WHEN preceding_end IS NULL OR segment_start > preceding_end THEN 1 ELSE 0 END)
        OVER (PARTITION BY delivery_id ORDER BY segment_start, segment_end, event_id) AS group_id
    FROM marked
), segments AS (
    SELECT delivery_id, group_id, min(segment_start) AS segment_start, max(segment_end) AS segment_end
    FROM groups GROUP BY delivery_id, group_id
), durations AS (
    SELECT delivery_id, sum(extract(epoch FROM segment_end - segment_start)) AS seconds
    FROM segments GROUP BY delivery_id
), counts AS (
    SELECT delivery_id, count(*)::integer AS events FROM candidates GROUP BY delivery_id
)
SELECT d.delivery_id, coalesce(u.seconds, 0) AS alert_overlap_seconds,
       coalesce(u.seconds, 0) / extract(epoch FROM d.finished_at - d.started_at) AS alert_exposure_ratio,
       coalesce(c.events, 0) AS overlap_alert_count
FROM core.fact_delivery d
LEFT JOIN durations u USING (delivery_id)
LEFT JOIN counts c USING (delivery_id)
WHERE EXISTS (SELECT 1 FROM control.load_batch b
              WHERE b.source_type='alerts' AND b.status='success' AND b.rows_accepted > 0
                AND b.rows_accepted = (SELECT count(*) FROM core.fact_alert));
COMMIT;
