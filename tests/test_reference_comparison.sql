BEGIN;
INSERT INTO control.load_batch(source_type, source_file, status) VALUES ('ref-test','ref-test','success');
INSERT INTO core.dim_region(region_id,region_name) VALUES ('ref-test-a','test'),('ref-test-b','test');
INSERT INTO core.fact_delivery(delivery_id,region_id,started_at,finished_at,distance_km,status,load_id)
SELECT id, region, '2026-01-01 00:00:00+00'::timestamptz,
  '2026-01-01 00:00:20+00'::timestamptz, 1, 'completed', max(load_id)
FROM (VALUES ('ref-test-d','ref-test-a'),('ref-test-zero','ref-test-b')) v(id,region)
CROSS JOIN control.load_batch WHERE source_type='ref-test' GROUP BY id,region;
INSERT INTO core.fact_alert(event_id,region_id,event_type,started_at,finished_at,load_id)
SELECT id,'ref-test-a','Повітряна тривога',
  '2026-01-01 00:00:00+00'::timestamptz + s*interval '1 second',
  '2026-01-01 00:00:00+00'::timestamptz + e*interval '1 second',max(load_id)
FROM (VALUES ('ref-a',1,10),('ref-b',2,3),('ref-c',4,5),('ref-d',9,12),
             ('ref-e',12,14),('ref-f',20,25)) v(id,s,e)
CROSS JOIN control.load_batch WHERE source_type='ref-test' GROUP BY id,s,e;
UPDATE control.load_batch SET rows_accepted=(SELECT count(*) FROM core.fact_alert)
 WHERE source_type='alerts' AND status='success';
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM mart.delivery_alert_exposure_reference WHERE delivery_id='ref-test-d'
     AND alert_overlap_seconds=13 AND alert_exposure_ratio=0.65 AND overlap_alert_count=5) THEN
   RAISE EXCEPTION 'nested or overlapping intervals: incorrect reference';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM mart.delivery_alert_exposure_reference WHERE delivery_id='ref-test-zero'
     AND alert_overlap_seconds=0 AND overlap_alert_count=0) THEN
   RAISE EXCEPTION 'region isolation or no-overlap case failed';
 END IF;
 IF EXISTS ((SELECT * FROM mart.delivery_alert_exposure EXCEPT SELECT * FROM mart.delivery_alert_exposure_reference)
       UNION ALL (SELECT * FROM mart.delivery_alert_exposure_reference EXCEPT SELECT * FROM mart.delivery_alert_exposure)) THEN
   RAISE EXCEPTION 'Reference and mart differ';
 END IF;
END $$;
ROLLBACK;
