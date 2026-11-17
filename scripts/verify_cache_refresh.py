"""Exercise stale state detection and explicit refresh in a rolled-back transaction."""
import subprocess

from scripts.generate_density_scenarios import ROOT
from scripts.run_delivery_cache_benchmark import statements


def main():
    fixture = (ROOT / 'tests/test_delivery_start_states.sql').read_text(encoding='utf-8')
    separator = "UPDATE control.load_batch SET status='failed' WHERE source_type='alerts' AND status='success';"
    if fixture.count(separator) != 1 or fixture.count('BEGIN;') != 1:
        raise ValueError('Boundary fixture changed')
    fixture = fixture.split(separator, 1)[0].replace('BEGIN;', 'BEGIN ISOLATION LEVEL REPEATABLE READ;', 1)
    base, cached = statements('density_sparse_s41_v1')
    base = base.replace('density_sparse_s41_v1', 'point-test-a')
    cached = cached.replace('density_sparse_s41_v1', 'point-test-a')
    sql = fixture + f"""
CREATE TEMP TABLE delivery_state_cache ON COMMIT DROP AS
 SELECT * FROM scenario.region_state WHERE scenario_id='point-test-a';
CREATE INDEX delivery_state_cache_range_idx ON delivery_state_cache USING gist (tstzrange(started_at,finished_at,'[)'));
ANALYZE delivery_state_cache;
DO $$ BEGIN
 IF EXISTS (({base} EXCEPT ALL {cached}) UNION ALL ({cached} EXCEPT ALL {base}))
 THEN RAISE EXCEPTION 'Initial cache is not equivalent'; END IF;
END $$;
INSERT INTO scenario.outage_events(scenario_id,event_id,started_at,finished_at)
 VALUES ('point-test-a','refresh-test-outage','2026-01-01 00:00:02+00','2026-01-01 00:00:03+00');
DO $$ BEGIN
 IF NOT EXISTS (({base} EXCEPT ALL {cached}) UNION ALL ({cached} EXCEPT ALL {base}))
 THEN RAISE EXCEPTION 'Expected stale cache after change'; END IF;
 IF NOT EXISTS (SELECT 1 FROM scenario.delivery_start_state WHERE scenario_id='point-test-a' AND delivery_id='point-2' AND alert_active AND outage_active)
 THEN RAISE EXCEPTION 'Changed outage did not affect expected delivery'; END IF;
END $$;
DELETE FROM pg_temp.delivery_state_cache;
INSERT INTO pg_temp.delivery_state_cache SELECT * FROM scenario.region_state WHERE scenario_id='point-test-a';
ANALYZE delivery_state_cache;
DO $$ BEGIN
 IF EXISTS (({base} EXCEPT ALL {cached}) UNION ALL ({cached} EXCEPT ALL {base}))
 THEN RAISE EXCEPTION 'Refreshed cache does not match source'; END IF;
END $$;
ROLLBACK;
BEGIN;
CREATE TEMP TABLE refresh_lifecycle_probe (value integer) ON COMMIT DROP;
INSERT INTO refresh_lifecycle_probe VALUES (1);
COMMIT;
DO $$ BEGIN
 IF to_regclass('pg_temp.refresh_lifecycle_probe') IS NOT NULL
 THEN RAISE EXCEPTION 'Temporary cache survived commit'; END IF;
END $$;
"""
    cmd = ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
           '-U','dwh_user','-d','logistics_dwh']
    subprocess.run(cmd, cwd=ROOT, input=sql.encode('utf-8'), check=True)
    print('Mutation made cache stale, refresh restored equality, changes rolled back; ON COMMIT DROP verified.')


if __name__ == '__main__':
    main()
