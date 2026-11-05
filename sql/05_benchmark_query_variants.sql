BEGIN;
-- Capture the input once; stop if its load provenance is incomplete.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM control.load_batch b
    WHERE b.source_type='alerts' AND b.status='success' AND b.source_sha256 IS NOT NULL
      AND b.rows_accepted > 0 AND b.rows_accepted=(SELECT count(*) FROM core.fact_alert)
      AND NOT EXISTS (SELECT 1 FROM core.fact_alert a WHERE a.load_id <> b.load_id)
  ) THEN RAISE EXCEPTION 'Alert source is missing or has inconsistent provenance'; END IF;
END $$;

CREATE TABLE benchmark.alert_events AS
SELECT event_id, region_id, event_type, started_at, finished_at
FROM core.fact_alert WHERE event_type='Повітряна тривога';
ALTER TABLE benchmark.alert_events ADD PRIMARY KEY(event_id);
CREATE INDEX benchmark_alert_range_gist ON benchmark.alert_events
USING gist (tstzrange(started_at, finished_at, '[)'));
ANALYZE benchmark.alert_events;
CREATE TABLE benchmark.alert_snapshot AS
SELECT b.load_id, b.source_sha256, count(a.event_id) AS alert_count, now() AS captured_at
FROM control.load_batch b JOIN core.fact_alert a ON a.load_id=b.load_id
WHERE b.source_type='alerts' AND b.status='success' AND a.event_type='Повітряна тривога'
GROUP BY b.load_id, b.source_sha256;

-- Both variants share the same input, aggregation, output and zero-row logic.
-- The only difference is the candidate intersection predicate.
DO $create$
DECLARE n integer; method text; predicate text;
BEGIN
  FOREACH n IN ARRAY ARRAY[10000,100000,1000000] LOOP
    FOREACH method IN ARRAY ARRAY['baseline','range'] LOOP
      predicate := CASE method
        WHEN 'baseline' THEN 'd.started_at < a.finished_at AND a.started_at < d.finished_at'
        ELSE 'tstzrange(a.started_at,a.finished_at,''[)'') && tstzrange(d.started_at,d.finished_at,''[)'')'
      END;
      EXECUTE format($view$
        CREATE VIEW benchmark.%1$I AS
        WITH matches AS (
          SELECT d.delivery_id, a.event_id,
                 greatest(d.started_at,a.started_at) AS segment_start,
                 least(d.finished_at,a.finished_at) AS segment_end
          FROM benchmark.%2$I d JOIN benchmark.alert_events a
            ON a.region_id=d.region_id AND %3$s
        ), marked AS (
          SELECT *, max(segment_end) OVER (PARTITION BY delivery_id
            ORDER BY segment_start,segment_end,event_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
          FROM matches
        ), islands AS (
          SELECT *, sum(CASE WHEN prev_end IS NULL OR segment_start > prev_end THEN 1 ELSE 0 END)
            OVER (PARTITION BY delivery_id ORDER BY segment_start,segment_end,event_id) AS island_id
          FROM marked
        ), merged AS (
          SELECT delivery_id,island_id,min(segment_start) AS segment_start,max(segment_end) AS segment_end
          FROM islands GROUP BY delivery_id,island_id
        ), totals AS (
          SELECT delivery_id,sum(extract(epoch FROM segment_end-segment_start)) AS seconds
          FROM merged GROUP BY delivery_id
        ), counts AS (
          SELECT delivery_id,count(*)::integer AS events FROM matches GROUP BY delivery_id
        )
        SELECT d.delivery_id,coalesce(t.seconds,0) AS alert_overlap_seconds,
          coalesce(t.seconds,0)/extract(epoch FROM d.finished_at-d.started_at) AS alert_exposure_ratio,
          coalesce(c.events,0) AS overlap_alert_count
        FROM benchmark.%2$I d LEFT JOIN totals t USING(delivery_id) LEFT JOIN counts c USING(delivery_id)
      $view$, method || '_' || n, 'deliveries_' || n, predicate);
    END LOOP;
  END LOOP;
END $create$;
COMMIT;
