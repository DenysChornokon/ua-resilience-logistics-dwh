BEGIN;

-- A scenario is explicitly synthetic; it does not claim observed outages.
CREATE SCHEMA IF NOT EXISTS scenario;
CREATE TABLE scenario.definitions (
    scenario_id text PRIMARY KEY,
    region_id text NOT NULL REFERENCES core.dim_region(region_id),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    description text NOT NULL,
    CHECK (window_start < window_end)
);
CREATE TABLE scenario.outage_events (
    scenario_id text NOT NULL REFERENCES scenario.definitions(scenario_id),
    event_id text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    source_type text NOT NULL DEFAULT 'synthetic' CHECK (source_type='synthetic'),
    PRIMARY KEY (scenario_id,event_id),
    CHECK (started_at < finished_at)
);

INSERT INTO scenario.definitions VALUES
 ('kyiv_demo_v1','UA80000000000093317','2026-07-01 00:00:00+00','2026-10-01 00:00:00+00',
  'Демонстраційний сценарій: модельовані відключення; реальні інтервали тривог');
INSERT INTO scenario.outage_events(scenario_id,event_id,started_at,finished_at) VALUES
 ('kyiv_demo_v1','synthetic-outage-01','2026-07-01 20:00:00+00','2026-07-01 23:00:00+00'),
 ('kyiv_demo_v1','synthetic-outage-02','2026-07-02 02:00:00+00','2026-07-02 06:00:00+00'),
 ('kyiv_demo_v1','synthetic-outage-03','2026-08-15 09:00:00+00','2026-08-15 12:00:00+00'),
 ('kyiv_demo_v1','synthetic-outage-04','2026-09-15 17:00:00+00','2026-09-15 20:00:00+00');

-- Sweep endpoints, aggregate simultaneous changes, then merge equal neighbouring states.
CREATE VIEW scenario.region_state AS
WITH valid_scenarios AS (
 SELECT s.* FROM scenario.definitions s
 WHERE EXISTS (SELECT 1 FROM control.load_batch b
   WHERE b.source_type='alerts' AND b.status='success' AND b.rows_accepted>0
     AND b.rows_accepted=(SELECT count(*) FROM core.fact_alert)
     AND NOT EXISTS (SELECT 1 FROM core.fact_alert a WHERE a.load_id<>b.load_id))
), events AS (
 SELECT s.scenario_id, greatest(a.started_at,s.window_start) AS started_at,
        least(a.finished_at,s.window_end) AS finished_at, 'alert'::text AS factor
 FROM valid_scenarios s JOIN core.fact_alert a ON a.region_id=s.region_id
  AND a.event_type='Повітряна тривога' AND a.started_at<s.window_end AND a.finished_at>s.window_start
 UNION ALL
 SELECT s.scenario_id,greatest(o.started_at,s.window_start),least(o.finished_at,s.window_end),'outage'
 FROM valid_scenarios s JOIN scenario.outage_events o USING(scenario_id)
 WHERE o.started_at<s.window_end AND o.finished_at>s.window_start
), changes AS (
 SELECT scenario_id,started_at AS t,(factor='alert')::int AS da,(factor='outage')::int AS do_
 FROM events
 UNION ALL
 SELECT scenario_id,finished_at,-(factor='alert')::int,-(factor='outage')::int FROM events
 UNION ALL SELECT scenario_id,window_start,0,0 FROM valid_scenarios
 UNION ALL SELECT scenario_id,window_end,0,0 FROM valid_scenarios
), points AS (
 SELECT scenario_id,t,sum(da) AS da,sum(do_) AS do_ FROM changes GROUP BY scenario_id,t
), active AS (
 SELECT scenario_id,t,lead(t) OVER w AS next_t,
        sum(da) OVER w AS alert_count,sum(do_) OVER w AS outage_count
 FROM points WINDOW w AS (PARTITION BY scenario_id ORDER BY t ROWS UNBOUNDED PRECEDING)
), segments AS (
 SELECT scenario_id,t,next_t,(alert_count>0) AS alert_active,(outage_count>0) AS outage_active
 FROM active WHERE next_t IS NOT NULL AND t<next_t
), changes_of_state AS (
 SELECT *,CASE WHEN lag(alert_active) OVER w IS DISTINCT FROM alert_active
                     OR lag(outage_active) OVER w IS DISTINCT FROM outage_active
               THEN 1 ELSE 0 END AS is_new
 FROM segments WINDOW w AS (PARTITION BY scenario_id ORDER BY t)
), islands AS (
 SELECT *,sum(is_new) OVER (PARTITION BY scenario_id ORDER BY t) AS island
 FROM changes_of_state
)
SELECT s.scenario_id,s.region_id,min(i.t) AS started_at,max(i.next_t) AS finished_at,
       i.alert_active,i.outage_active,
       extract(epoch FROM max(i.next_t)-min(i.t)) AS duration_seconds
FROM islands i JOIN valid_scenarios s USING(scenario_id)
GROUP BY s.scenario_id,s.region_id,i.island,i.alert_active,i.outage_active;
COMMIT;
