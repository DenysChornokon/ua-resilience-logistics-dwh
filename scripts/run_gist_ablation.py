"""Compare the same range query with and without its GiST index."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts import run_postgres_benchmark as benchmark

STATE_SQL = """SELECT json_build_object(
 'alert_copies_equal', NOT EXISTS ((SELECT * FROM benchmark.alert_events EXCEPT ALL SELECT * FROM benchmark.alert_events_no_gist) UNION ALL (SELECT * FROM benchmark.alert_events_no_gist EXCEPT ALL SELECT * FROM benchmark.alert_events)),
 'alert_rows', (SELECT count(*) FROM benchmark.alert_events),
 'definitions_match', (SELECT bool_and(replace(pg_get_viewdef(format('benchmark.range_%s',n)::regclass,true),'benchmark.alert_events','benchmark.alert_events_no_gist') = pg_get_viewdef(format('benchmark.range_no_gist_%s',n)::regclass,true)) FROM (VALUES (10000),(100000),(1000000)) v(n)),
 'indexes', (SELECT json_agg(indexdef ORDER BY tablename,indexname) FROM pg_indexes WHERE schemaname='benchmark' AND tablename IN ('alert_events','alert_events_no_gist'))
);"""


def inspect_state():
    state = json.loads(benchmark.query(STATE_SQL))
    if not state['alert_copies_equal'] or not state['definitions_match'] or state['alert_rows'] <= 0:
        raise ValueError('Alert data or view definitions do not match')
    indexes = state['indexes'] or []
    if not any('benchmark_alert_range_gist' in item and 'USING gist' in item for item in indexes):
        raise ValueError('Expected GiST index missing')
    if any('alert_events_no_gist' in item and 'USING gist' in item for item in indexes):
        raise ValueError('Control table has a GiST index')
    return state


def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output', type=Path, required=True)
    args,_=parser.parse_known_args()
    before=inspect_state()
    benchmark.METHODS=('range_no_gist','range')
    benchmark.main()
    try:
        after=inspect_state()
        if before != after:
            raise ValueError('Database state changed during the measurement series')
        extra={'purpose':'Same range predicate with and without a GiST index on identical alert copies',
               'control_sql_sha256':hashlib.sha256((benchmark.ROOT/'sql/06_range_without_gist.sql').read_bytes()).hexdigest(),
               'verified_state':after,
               'limitation':'Different physical copies can have different storage layout; index build and maintenance excluded.'}
        (args.output/'ablation_metadata.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    except BaseException as exc:
        path=args.output/'metadata.json'
        meta=json.loads(path.read_text(encoding='utf-8'))
        meta['status']='failed'; meta['error']=f'post-run state validation: {exc}'
        path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
