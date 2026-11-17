-- Fixture changes are rolled back even when psql exits after an error.
BEGIN;
INSERT INTO core.dim_region(region_id,region_name) VALUES ('state-test-a','test'),('state-test-b','test');
INSERT INTO scenario.definitions VALUES
 ('state-test-a','state-test-a','2026-01-01 00:00:00+00','2026-01-01 00:00:30+00','test'),
 ('state-test-b','state-test-b','2026-01-01 00:00:00+00','2026-01-01 00:00:30+00','empty');
INSERT INTO core.fact_alert(event_id,region_id,event_type,started_at,finished_at,load_id)
SELECT id,'state-test-a','Повітряна тривога',
 '2026-01-01 00:00:00+00'::timestamptz+s*interval '1 second',
 '2026-01-01 00:00:00+00'::timestamptz+e*interval '1 second', b.load_id
FROM (VALUES ('state-test-1',2,14),('state-test-2',4,6),('state-test-3',14,18)) x(id,s,e)
CROSS JOIN control.load_batch b WHERE b.source_type='alerts' AND b.status='success';
INSERT INTO scenario.outage_events(scenario_id,event_id,started_at,finished_at) VALUES
 ('state-test-a','out-1','2026-01-01 00:00:10+00','2026-01-01 00:00:22+00'),
 ('state-test-a','out-2','2026-01-01 00:00:12+00','2026-01-01 00:00:14+00'),
 ('state-test-a','touch-end','2026-01-01 00:00:30+00','2026-01-01 00:00:32+00');
UPDATE control.load_batch SET rows_accepted=(SELECT count(*) FROM core.fact_alert)
 WHERE source_type='alerts' AND status='success';
CREATE TEMP TABLE expected_states(started_at timestamptz,finished_at timestamptz,alert_active boolean,outage_active boolean);
INSERT INTO expected_states
SELECT '2026-01-01 00:00:00+00'::timestamptz+s*interval '1 second',
       '2026-01-01 00:00:00+00'::timestamptz+e*interval '1 second',a,o
FROM (VALUES (0,2,false,false),(2,10,true,false),(10,18,true,true),
             (18,22,false,true),(22,30,false,false)) v(s,e,a,o);
DO $$ BEGIN
 IF EXISTS ((SELECT started_at,finished_at,alert_active,outage_active FROM scenario.region_state WHERE scenario_id='state-test-a'
             EXCEPT SELECT * FROM expected_states)
     UNION ALL (SELECT * FROM expected_states EXCEPT SELECT started_at,finished_at,alert_active,outage_active FROM scenario.region_state WHERE scenario_id='state-test-a')) THEN
   RAISE EXCEPTION 'Incorrect overlapping, nested, adjacent or simultaneous state';
 END IF;
 IF (SELECT count(*) FROM scenario.region_state WHERE scenario_id='state-test-b' AND NOT alert_active AND NOT outage_active AND duration_seconds=30) <> 1 THEN
   RAISE EXCEPTION 'Empty scenario or region isolation failed';
 END IF;
 IF (SELECT sum(duration_seconds) FROM scenario.region_state WHERE scenario_id='state-test-a') <> 30 THEN
   RAISE EXCEPTION 'Coverage duration failed';
 END IF;
END $$;
-- No successful alert load must not turn unknown alerts into false states.
UPDATE control.load_batch SET status='failed' WHERE source_type='alerts' AND status='success';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM scenario.region_state) THEN
   RAISE EXCEPTION 'Missing successful source must not produce states';
 END IF;
END $$;
ROLLBACK;
