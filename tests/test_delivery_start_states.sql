-- All fixture changes are rolled back, including on an error in psql.
BEGIN;
INSERT INTO core.dim_region(region_id,region_name) VALUES ('point-test-a','test'),('point-test-b','test');
INSERT INTO scenario.definitions VALUES
 ('point-test-a','point-test-a','2026-01-01 00:00:00+00','2026-01-01 00:00:30+00','test');
INSERT INTO core.fact_alert(event_id,region_id,event_type,started_at,finished_at,load_id)
SELECT 'point-test-alert','point-test-a','Повітряна тривога',
 '2026-01-01 00:00:02+00','2026-01-01 00:00:18+00',load_id
FROM control.load_batch WHERE source_type='alerts' AND status='success';
INSERT INTO scenario.outage_events(scenario_id,event_id,started_at,finished_at) VALUES
 ('point-test-a','point-test-outage','2026-01-01 00:00:10+00','2026-01-01 00:00:22+00');
INSERT INTO core.fact_delivery(delivery_id,region_id,started_at,finished_at,distance_km,status,load_id)
SELECT id,reg,'2026-01-01 00:00:00+00'::timestamptz + n*interval '1 second',
 '2026-01-01 00:00:05+00'::timestamptz + n*interval '1 second',1,'completed',b.load_id
FROM (VALUES ('point-0','point-test-a',0),('point-2','point-test-a',2),
 ('point-10','point-test-a',10),('point-18','point-test-a',18),('point-22','point-test-a',22),
 ('point-end','point-test-a',30),('point-other','point-test-b',10)) AS x(id,reg,n)
CROSS JOIN control.load_batch b WHERE b.source_type='deliveries' AND b.status='success';
UPDATE control.load_batch SET rows_accepted=(SELECT count(*) FROM core.fact_alert)
 WHERE source_type='alerts' AND status='success';
CREATE TEMP TABLE point_expected(delivery_id text,alert_active boolean,outage_active boolean);
INSERT INTO point_expected VALUES ('point-0',false,false),('point-2',true,false),
 ('point-10',true,true),('point-18',false,true),('point-22',false,false);
DO $$ BEGIN
 IF (SELECT count(*) FROM scenario.delivery_start_state WHERE scenario_id='point-test-a') <> 5 THEN
   RAISE EXCEPTION 'Incorrect number of in-scope rows';
 END IF;
 IF EXISTS ((SELECT delivery_id,alert_active,outage_active FROM scenario.delivery_start_state WHERE scenario_id='point-test-a' EXCEPT SELECT * FROM point_expected)
 UNION ALL (SELECT * FROM point_expected EXCEPT SELECT delivery_id,alert_active,outage_active FROM scenario.delivery_start_state WHERE scenario_id='point-test-a')) THEN
   RAISE EXCEPTION 'Incorrect state at a boundary';
 END IF;
 IF EXISTS (SELECT 1 FROM scenario.delivery_start_state WHERE scenario_id='point-test-a' AND NOT state_known) THEN
   RAISE EXCEPTION 'Known states flagged as unknown';
 END IF;
END $$;
UPDATE control.load_batch SET status='failed' WHERE source_type='alerts' AND status='success';
DO $$ BEGIN
 IF (SELECT count(*) FROM scenario.delivery_start_state WHERE scenario_id='point-test-a'
     AND NOT state_known AND alert_active IS NULL AND outage_active IS NULL) <> 5 THEN
   RAISE EXCEPTION 'Unknown input must yield NULL factor flags';
 END IF;
END $$;
ROLLBACK;
