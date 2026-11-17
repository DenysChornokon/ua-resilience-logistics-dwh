"""Compare cached and dynamic joins on boundary and missing-source fixtures; rollback."""
import subprocess

from scripts.generate_density_scenarios import ROOT
from scripts.run_delivery_cache_benchmark import statements


def equality_check():
    baseline, cached = statements('density_sparse_s41_v1')
    baseline = baseline.replace('density_sparse_s41_v1', 'point-test-a')
    cached = cached.replace('density_sparse_s41_v1', 'point-test-a')
    return f"""DO $$ BEGIN
      IF EXISTS (({baseline} EXCEPT ALL {cached}) UNION ALL ({cached} EXCEPT ALL {baseline}))
      THEN RAISE EXCEPTION 'Cached results differ at a boundary or for unknown states'; END IF;
    END $$;\n"""


def main():
    sql = (ROOT / 'tests/test_delivery_start_states.sql').read_text(encoding='utf-8')
    invalid = "UPDATE control.load_batch SET status='failed' WHERE source_type='alerts' AND status='success';"
    if sql.count(invalid) != 1 or sql.count('ROLLBACK;') != 1:
        raise ValueError('Boundary fixture format has changed')
    setup = """CREATE TEMP TABLE delivery_state_cache AS SELECT * FROM scenario.region_state WHERE scenario_id='point-test-a';
CREATE INDEX delivery_state_cache_range_idx ON delivery_state_cache USING gist (tstzrange(started_at,finished_at,'[)'));
ANALYZE delivery_state_cache;
""" + equality_check()
    unknown = """DELETE FROM pg_temp.delivery_state_cache;
INSERT INTO pg_temp.delivery_state_cache SELECT * FROM scenario.region_state WHERE scenario_id='point-test-a';
""" + equality_check()
    sql = sql.replace(invalid, setup + invalid).replace('ROLLBACK;', unknown + 'ROLLBACK;')
    cmd = ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1','-U','dwh_user','-d','logistics_dwh']
    subprocess.run(cmd, cwd=ROOT, input=sql.encode('utf-8'), check=True)
    print('Cached and dynamic results match for boundary and unknown-state fixtures; rolled back.')


if __name__ == '__main__':
    main()
