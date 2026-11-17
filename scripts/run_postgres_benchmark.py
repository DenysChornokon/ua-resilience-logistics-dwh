"""Run reproducible PostgreSQL EXPLAIN ANALYZE trials for prepared benchmark views."""
import argparse
import csv
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
SIZES = (10000, 100000, 1000000)
METHODS = ('baseline', 'range')
PSQL = ['docker','compose','exec','-T','db','psql','-X','-v','ON_ERROR_STOP=1',
        '-U','dwh_user','-d','logistics_dwh','-A','-t']


def query(sql):
    result = subprocess.run(PSQL + ['-c', sql], cwd=ROOT, capture_output=True, check=True)
    return result.stdout.decode('utf-8').strip()


def walk(node):
    yield node
    for child in node.get('Plans', []):
        yield from walk(child)


def plan_metrics(plan, expected_rows):
    root = plan['Plan']
    if root['Actual Rows'] != expected_rows or root.get('Actual Loops', 1) != 1:
        raise ValueError(f"Expected {expected_rows} output rows; got {root['Actual Rows']}")
    return {'planning_ms': plan['Planning Time'], 'execution_ms': plan['Execution Time'],
            'output_rows': root['Actual Rows'],
            'shared_hit_blocks': root.get('Shared Hit Blocks', 0),
            'shared_read_blocks': root.get('Shared Read Blocks', 0),
            'temp_read_blocks': root.get('Temp Read Blocks', 0),
            'temp_written_blocks': root.get('Temp Written Blocks', 0),
            'indexes': ';'.join(sorted({n['Index Name'] for n in walk(root) if 'Index Name' in n}))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--sizes', type=int, nargs='+', default=list(SIZES), choices=SIZES)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    args = parser.parse_args()
    if args.repetitions < 1 or len(set(args.sizes)) != len(args.sizes):
        parser.error('repetitions must be positive and sizes unique')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'plans').mkdir()
    meta = {'status': 'running', 'utc_started': datetime.now(timezone.utc).isoformat(),
            'sizes': args.sizes, 'repetitions': args.repetitions, 'warmups_per_case': 1,
            'order_seed': 20261105, 'python': sys.version, 'platform': platform.platform(),
            'processor': platform.processor(),
            'timing_scope': 'EXPLAIN ANALYZE server execution, all output rows; excludes client result transfer and index build',
            'cache': 'warmups; shared cache is not cleared; actual buffer statistics retained',
            'memory': 'overall process memory not measured',
            'postgres_version': query('SELECT version();'),
            'postgres_settings': json.loads(query("SELECT json_object_agg(name, setting || ' ' || coalesce(unit,'')) FROM pg_settings WHERE name IN ('work_mem','shared_buffers','max_parallel_workers_per_gather','effective_cache_size','random_page_cost','jit');")),
            'dataset_manifest': json.loads(query('SELECT json_agg(t ORDER BY rows_expected) FROM benchmark.dataset_manifest t;')),
            'alert_snapshot': json.loads(query('SELECT row_to_json(t) FROM benchmark.alert_snapshot t;')),
            'query_definition_sha256': hashlib.sha256((ROOT/'sql/05_benchmark_query_variants.sql').read_bytes()).hexdigest()}
    metadata_path = args.output / 'metadata.json'
    def save_meta(): metadata_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save_meta()
    csv_path = args.output / 'measurements.csv'
    fieldnames = ['order','phase','repetition','size','method','planning_ms','execution_ms','output_rows',
                  'shared_hit_blocks','shared_read_blocks','temp_read_blocks','temp_written_blocks','indexes']
    rng = random.Random(meta['order_seed'])
    order = 0
    try:
        with csv_path.open('x',encoding='utf-8',newline='') as f:
            writer = csv.DictWriter(f,fieldnames=fieldnames)
            writer.writeheader()
            for rep in range(args.repetitions + 1):
                cases = [(n, m) for n in args.sizes for m in METHODS]
                rng.shuffle(cases)
                for n, method in cases:
                    phase = 'warmup' if rep == 0 else 'measured'
                    view = f'benchmark.{method}_{n}'
                    data = json.loads(query(f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM {view};'))
                    metrics = plan_metrics(data[0], n)
                    order += 1
                    filename = f'{order:03d}_{phase}_{method}_{n}.json'
                    (args.output/'plans'/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    writer.writerow({'order':order,'phase':phase,'repetition':rep,'size':n,'method':method,**metrics})
                    f.flush()
                    print(f"{order}: {phase} {method} {n} {metrics['execution_ms']:.3f} ms",flush=True)
        rows = list(csv.DictReader(csv_path.open(encoding='utf-8', newline='')))
        with (args.output/'summary.csv').open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['size','method','runs','median_execution_ms','min_execution_ms','max_execution_ms'])
            writer.writeheader()
            for n in args.sizes:
                for method in METHODS:
                    values=[float(r['execution_ms']) for r in rows if r['phase']=='measured' and int(r['size'])==n and r['method']==method]
                    writer.writerow({'size':n,'method':method,'runs':len(values),'median_execution_ms':round(median(values),3),
                                     'min_execution_ms':min(values),'max_execution_ms':max(values)})
        meta['status']='complete'
        meta['utc_finished']=datetime.now(timezone.utc).isoformat()
        save_meta()
    except BaseException as exc:
        meta['status']='failed'; meta['error']=str(exc); save_meta(); raise


if __name__ == '__main__':
    main()
