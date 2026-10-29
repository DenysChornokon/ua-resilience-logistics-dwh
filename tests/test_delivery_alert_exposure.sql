-- All fixture changes are rolled back, including on error when psql exits.
BEGIN;
INSERT INTO control.load_batch (source_type, source_file, status)
VALUES ('test', 'test-only', 'success');
INSERT INTO core.dim_region(region_id, region_name) VALUES ('test-a', 'test-a'), ('test-b', 'test-b');
INSERT INTO core.fact_delivery (delivery_id, region_id, started_at, finished_at, distance_km, status, load_id)
SELECT id, region, '2026-01-01 00:00:00+00'::timestamptz,
       '2026-01-01 00:00:10+00'::timestamptz, 1, 'completed', max(load_id)
FROM (VALUES ('test-overlap','test-a'), ('test-other','test-b')) v(id, region)
CROSS JOIN control.load_batch WHERE source_type='test' GROUP BY id, region;
INSERT INTO core.fact_alert(event_id, region_id, event_type, started_at, finished_at, load_id)
SELECT id, 'test-a', 'Повітряна тривога',
       '2026-01-01 00:00:00+00'::timestamptz + start_s * interval '1 second',
       '2026-01-01 00:00:00+00'::timestamptz + end_s * interval '1 second', max(load_id)
FROM (VALUES ('test-1',1,6),('test-2',4,8),('test-nested',2,3),('test-adjacent',8,9),('test-touch',10,12)) v(id,start_s,end_s)
CROSS JOIN control.load_batch WHERE source_type='test' GROUP BY id,start_s,end_s;
-- Extend the audit record temporarily to satisfy the guard; rollback restores it.
UPDATE control.load_batch SET rows_accepted = (SELECT count(*) FROM core.fact_alert)
WHERE source_type='alerts' AND status='success';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM mart.delivery_alert_exposure WHERE delivery_id='test-overlap'
      AND alert_overlap_seconds=8 AND alert_exposure_ratio=0.8 AND overlap_alert_count=4) THEN
    RAISE EXCEPTION 'Overlap, nested interval, or touching boundary failed';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM mart.delivery_alert_exposure WHERE delivery_id='test-other'
      AND alert_overlap_seconds=0 AND alert_exposure_ratio=0 AND overlap_alert_count=0) THEN
    RAISE EXCEPTION 'Region isolation or zero exposure failed';
  END IF;
END $$;
UPDATE control.load_batch SET status='failed' WHERE source_type='alerts' AND status='success';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM mart.delivery_alert_exposure) THEN
    RAISE EXCEPTION 'Missing successful source must not produce zero exposures';
  END IF;
END $$;
ROLLBACK;
