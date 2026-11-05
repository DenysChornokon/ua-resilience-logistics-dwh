BEGIN;
-- One row per delivery and scenario when the region and start time are in scope.
-- Unknown state remains NULL when source validation prevents state generation.
CREATE VIEW scenario.delivery_start_state AS
SELECT s.scenario_id,d.delivery_id,d.region_id,d.started_at AS delivery_started_at,
       r.started_at AS state_started_at,r.finished_at AS state_finished_at,
       (r.started_at IS NOT NULL) AS state_known,r.alert_active,r.outage_active
FROM scenario.definitions s
JOIN core.fact_delivery d ON d.region_id=s.region_id
 AND d.started_at>=s.window_start AND d.started_at<s.window_end
LEFT JOIN scenario.region_state r ON r.scenario_id=s.scenario_id
 AND r.region_id=d.region_id
 AND d.started_at>=r.started_at AND d.started_at<r.finished_at;
COMMIT;
