-- Test records are isolated to this transaction and always rolled back.
BEGIN;
INSERT INTO benchmark.deliveries_10000 VALUES
 ('B-99000000','UA80000000000093317','2026-07-01 00:00:00+00','2026-07-01 00:15:00+00',1,'completed'),
 ('B-99000001','UA80000000000093317','2026-07-01 12:00:00+00','2026-07-01 12:15:00+00',1,'completed');
INSERT INTO benchmark.alert_events VALUES
 ('bench-fixture-1','UA80000000000093317','Повітряна тривога','2026-07-01 00:00:01+00','2026-07-01 00:00:10+00'),
 ('bench-fixture-2','UA80000000000093317','Повітряна тривога','2026-07-01 00:00:02+00','2026-07-01 00:00:03+00'),
 ('bench-fixture-3','UA80000000000093317','Повітряна тривога','2026-07-01 00:00:09+00','2026-07-01 00:00:12+00'),
 ('bench-fixture-4','UA80000000000093317','Повітряна тривога','2026-07-01 00:00:12+00','2026-07-01 00:00:14+00'),
 ('bench-fixture-touch','UA80000000000093317','Повітряна тривога','2026-07-01 00:15:00+00','2026-07-01 00:16:00+00'),
 ('bench-fixture-other','other-region','Повітряна тривога','2026-07-01 00:00:00+00','2026-07-01 00:01:00+00');
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM benchmark.baseline_10000 WHERE delivery_id='B-99000000'
      AND alert_overlap_seconds=13 AND overlap_alert_count=4) THEN
   RAISE EXCEPTION 'Incorrect overlapping, nested, adjacent, touching or region result';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM benchmark.range_10000 WHERE delivery_id='B-99000001'
      AND alert_overlap_seconds=0 AND alert_exposure_ratio=0 AND overlap_alert_count=0) THEN
   RAISE EXCEPTION 'No-overlap delivery is missing';
 END IF;
 IF EXISTS ((SELECT * FROM benchmark.baseline_10000 WHERE delivery_id LIKE 'B-9900000%' EXCEPT SELECT * FROM benchmark.range_10000 WHERE delivery_id LIKE 'B-9900000%')
   UNION ALL (SELECT * FROM benchmark.range_10000 WHERE delivery_id LIKE 'B-9900000%' EXCEPT SELECT * FROM benchmark.baseline_10000 WHERE delivery_id LIKE 'B-9900000%')) THEN
   RAISE EXCEPTION 'Variants differ on fixtures';
 END IF;
END $$;
ROLLBACK;
