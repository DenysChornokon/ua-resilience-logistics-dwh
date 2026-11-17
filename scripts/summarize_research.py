"""Recalculate and plot separate benchmark series from their recorded measurements."""
import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'
REGULAR = {
    'interval': ('postgres_benchmark_20261105', (10000, 100000, 1000000), ('baseline', 'range'), 5),
    'temporal_gist': ('gist_ablation_20261105', (10000, 100000, 1000000), ('range_no_gist', 'range'), 5),
    'spatiotemporal': ('spatiotemporal_benchmark_20261111', (10000, 100000), ('unindexed', 'indexed'), 5),
}
CACHE = 'delivery_scale_benchmark_20261107'
PROFILES = ('sparse', 'uniform', 'clustered')
CACHE_SIZES = (10000, 100000)


def number(value):
    try:
        value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid execution time') from exc
    if not value.is_finite() or value <= 0:
        raise ValueError('Execution time must be finite and positive')
    return value


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def regular_groups(path, sizes, methods, repetitions):
    groups, seen = defaultdict(list), set()
    for row in read_csv(path):
        size, method, rep, phase = int(row['size']), row['method'], int(row['repetition']), row['phase']
        if size not in sizes or method not in methods or phase not in ('warmup', 'measured'):
            raise ValueError('Unexpected benchmark case')
        if int(row['output_rows']) != size:
            raise ValueError('Measured query returned the wrong number of rows')
        if (phase == 'warmup' and rep != 0) or (phase == 'measured' and rep not in range(1, repetitions + 1)):
            raise ValueError('Unexpected repetition')
        key = (size, method, rep)
        if key in seen:
            raise ValueError('Duplicate benchmark repetition')
        seen.add(key)
        value = number(row['execution_ms'])
        if phase == 'measured':
            groups[(size, method)].append(value)
    expected = {(s, m, r) for s in sizes for m in methods for r in range(repetitions + 1)}
    if seen != expected:
        raise ValueError('Incomplete benchmark series')
    return groups


def cache_groups(path, repetitions=3):
    groups, seen = defaultdict(list), set()
    for row in read_csv(path):
        profile, size, rep, phase = row['profile'], int(row['size']), int(row['repetition']), row['phase']
        if (profile not in PROFILES or size not in CACHE_SIZES or row['seed'] != '42' or
            row['scenario_id'] != f'density_{profile}_s42_v1' or phase not in ('warmup', 'measured')):
            raise ValueError('Unexpected scenario case')
        if (phase == 'warmup' and rep != 0) or (phase == 'measured' and rep not in range(1, repetitions + 1)):
            raise ValueError('Unexpected scenario repetition')
        key = (profile, size, rep)
        if key in seen:
            raise ValueError('Duplicate scenario repetition')
        seen.add(key)
        baseline, setup, cached = number(row['baseline_ms']), number(row['setup_ms']), number(row['cached_ms'])
        if phase == 'measured':
            groups[(profile, size, 'baseline')].append(baseline)
            # Sum within each paired trial before taking a median.
            groups[(profile, size, 'setup_plus_cached')].append(setup + cached)
    expected = {(p, s, r) for p in PROFILES for s in CACHE_SIZES for r in range(repetitions + 1)}
    if seen != expected:
        raise ValueError('Incomplete scenario series')
    return groups


def stats(values):
    return {'runs': len(values), 'median_ms': median(values), 'min_ms': min(values), 'max_ms': max(values)}


def same(actual, expected):
    if actual != Decimal(expected):
        raise ValueError(f'Recalculated summary disagrees with saved summary: {actual} vs {expected}')


def verify_regular_summary(path, groups, spatial=False):
    expected = {(int(row['size']), row['method']): row for row in read_csv(path)}
    if set(expected) != set(groups):
        raise ValueError('Unexpected saved summary cases')
    for key, values in groups.items():
        result, row = stats(values), expected[key]
        if int(row['runs']) != result['runs']:
            raise ValueError('Saved summary repetition count mismatch')
        for name in ('median', 'min', 'max'):
            same(result[f'{name}_ms'], row[f'{name}_ms' if spatial else f'{name}_execution_ms'])


def verify_cache_summary(path, groups):
    expected = {(row['profile'], int(row['size'])): row for row in read_csv(path)}
    if set(expected) != {(p, s) for p in PROFILES for s in CACHE_SIZES}:
        raise ValueError('Unexpected saved scenario summary cases')
    for (profile, size), row in expected.items():
        if row['seed'] != '42' or int(row['runs']) != 3:
            raise ValueError('Unexpected saved scenario metadata')
        same(median(groups[(profile, size, 'baseline')]), row['baseline_ms'])
        same(median(groups[(profile, size, 'setup_plus_cached')]), row['setup_plus_cached_ms'])


def comparison(series, size, profile, before, after):
    left, right = stats(before), stats(after)
    return {'series': series, 'profile': profile, 'size': size, 'runs': left['runs'],
            'baseline_median_ms': left['median_ms'], 'baseline_min_ms': left['min_ms'], 'baseline_max_ms': left['max_ms'],
            'candidate_median_ms': right['median_ms'], 'candidate_min_ms': right['min_ms'], 'candidate_max_ms': right['max_ms'],
            'ratio_of_medians': round(left['median_ms'] / right['median_ms'], 6),
            'median_reduction_percent': round((Decimal(1) - right['median_ms'] / left['median_ms']) * 100, 3)}


def make_comparisons(report_root):
    report_root = Path(report_root)
    comparisons, source_paths = [], []
    for series, (directory, sizes, methods, repetitions) in REGULAR.items():
        folder = report_root / directory
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        if meta['status'] != 'complete' or meta['repetitions'] != repetitions or tuple(meta['sizes']) != sizes:
            raise ValueError('Unexpected or incomplete experiment metadata')
        groups = regular_groups(folder / 'measurements.csv', sizes, methods, repetitions)
        verify_regular_summary(folder / 'summary.csv', groups, spatial=series == 'spatiotemporal')
        for size in sizes:
            comparisons.append(comparison(series, size, '', groups[(size, methods[0])], groups[(size, methods[1])]))
        source_paths.extend(folder / filename for filename in ('metadata.json', 'measurements.csv', 'summary.csv'))
    folder = report_root / CACHE
    meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    if (meta['status'] != 'complete' or meta['repetitions'] != 3 or tuple(meta['sizes']) != CACHE_SIZES or
        tuple(meta['profiles']) != PROFILES or meta['seed'] != 42):
        raise ValueError('Unexpected or incomplete scenario metadata')
    groups = cache_groups(folder / 'measurements.csv')
    verify_cache_summary(folder / 'summary.csv', groups)
    for size in CACHE_SIZES:
        for profile in PROFILES:
            comparisons.append(comparison('states', size, profile, groups[(profile, size, 'baseline')], groups[(profile, size, 'setup_plus_cached')]))
    source_paths.extend(folder / filename for filename in ('metadata.json', 'measurements.csv', 'summary.csv'))
    return comparisons, source_paths


def plot(comparisons, output):
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10), layout='constrained')
    panels = [
        ('interval', '1. Часовий перетин доставки', 'Порівняння меж', 'Діапазони + GiST'),
        ('temporal_gist', '2. Часові діапазони: вплив конфігурації', 'Без GiST', 'З GiST'),
        ('spatiotemporal', '3. Район і стан тривоги на початок', 'Без просторового GiST', 'З просторовим GiST'),
        ('states', '4. Стани тривоги та відключення', 'Початковий запит', 'Підготовка + запит'),
    ]
    for ax, (series, title, left_label, right_label) in zip(axes.flat, panels):
        entries = [r for r in comparisons if r['series'] == series]
        x = np.arange(len(entries), dtype=float)
        labels = ([f"{r['profile']}\n{int(r['size'])//1000} тис." for r in entries] if series == 'states' else
                  ['1 млн' if r['size'] == 1000000 else f"{int(r['size'])//1000} тис." for r in entries])
        for key, label, color, shift, marker in [('baseline', left_label, '#63758b', -.16, 'o'), ('candidate', right_label, '#007ea7', .16, 's')]:
            medians = np.array([float(r[f'{key}_median_ms']) for r in entries])
            low = np.array([float(r[f'{key}_min_ms']) for r in entries])
            high = np.array([float(r[f'{key}_max_ms']) for r in entries])
            ax.errorbar(x + shift, medians, yerr=[medians - low, high - medians], fmt=marker,
                        color=color, ecolor=color, capsize=4, markersize=6, linewidth=1.3, label=label, zorder=3)
        ax.set_yscale('log'); ax.set_xticks(x, labels, fontsize=9)
        ax.set_ylabel('Час PostgreSQL, мс (лог. шкала)')
        ax.set_title(title, fontsize=12, loc='left', weight='bold')
        ax.grid(axis='y', which='major', linestyle=':', alpha=.45, zorder=0)
        ax.legend(fontsize=9, frameon=False, loc='upper left')
        ax.margins(y=.25)
    fig.suptitle('Окремі експерименти: порівняння варіантів усередині кожного досліду', fontsize=14, weight='bold')
    fig.supxlabel('Синтетичні доставки; позначки — мінімум і максимум повторів', fontsize=10)
    try:
        for ext in ('png', 'svg'):
            path = output / f'research_comparison.{ext}'
            fig.savefig(path, dpi=220, bbox_inches='tight')
            if ext == 'svg':
                data = path.read_bytes().replace(b'\r\n', b'\n')
                path.write_bytes(b'\n'.join(line.rstrip(b' \t') for line in data.split(b'\n')))
    finally:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new directory for derived tables and figures')
    args = parser.parse_args()
    comparisons, sources = make_comparisons(REPORTS)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'comparisons.csv').open('x', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparisons[0])); writer.writeheader(); writer.writerows(comparisons)
    plot(comparisons, output)
    source_hashes = {}
    for source in sources:
        with source.open('rb') as handle:
            source_hashes[source.relative_to(ROOT).as_posix()] = hashlib.file_digest(handle, 'sha256').hexdigest()
    with Path(__file__).open('rb') as handle:
        runner_hash = hashlib.file_digest(handle, 'sha256').hexdigest()
    manifest = {'status': 'complete', 'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'runner_sha256': runner_hash,
                'source_sha256': source_hashes, 'case_count': len(comparisons),
                'method': 'medians and min/max from measured trials only; state preparation plus query summed per paired trial before median',
                'note': 'The four panels are separate experiments and do not directly compare different query workloads.'}
    with (output / 'metadata.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2); handle.write('\n')
    print(f'Summarized {len(comparisons)} cases from four experiments: {output}')


if __name__ == '__main__':
    main()
