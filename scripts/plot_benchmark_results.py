"""Plot measured PostgreSQL execution times; excludes warmup trials."""
import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def summarize(path, repetitions=5):
    groups = defaultdict(list)
    with Path(path).open(encoding='utf-8', newline='') as file:
        for row in csv.DictReader(file):
            if row['phase'] != 'measured':
                continue
            size, method, value = int(row['size']), row['method'], float(row['execution_ms'])
            if method not in ('baseline', 'range') or size <= 0 or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid measurement')
            groups[(size, method)].append(value / 1000)
    sizes = sorted({size for size, _ in groups})
    if not sizes:
        raise ValueError('No measurements')
    for size in sizes:
        for method in ('baseline', 'range'):
            if len(groups[(size, method)]) != repetitions:
                raise ValueError(f'Unexpected number of measurements: {size} {method}')
    return sizes, groups


def plot(input_path, output_dir):
    sizes, groups = summarize(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / 'postgres_interval_benchmark.png', output_dir / 'postgres_interval_benchmark.svg']
    if any(p.exists() for p in targets):
        raise FileExistsError('Chart already exists; choose an empty output folder')
    fig, (ax, ratio_ax) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={'width_ratios': [1.5, 1]}, layout='constrained')
    for method, label, color, marker in [('baseline','Порівняння меж','#0072B2','o'), ('range','Діапазони + GiST','#D55E00','s')]:
        meds = [median(groups[(n, method)]) for n in sizes]
        lo = [m - min(groups[(n, method)]) for n, m in zip(sizes, meds)]
        hi = [max(groups[(n, method)]) - m for n, m in zip(sizes, meds)]
        ax.errorbar(sizes, meds, yerr=[lo, hi], color=color, marker=marker,
                    linewidth=2, capsize=4, label=label)
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xticks(sizes, [f'{n:,}'.replace(',', ' ') for n in sizes])
    ax.set_xlabel('Кількість синтетичних доставок')
    ax.set_ylabel('Час виконання PostgreSQL, с (логарифмічна шкала)')
    ax.set_title('Медіана та діапазон повторів')
    ax.grid(True, which='major', linestyle=':', alpha=.5)
    ax.legend(frameon=False)
    ratios = [median(groups[(n, 'baseline')]) / median(groups[(n, 'range')]) for n in sizes]
    bars = ratio_ax.bar(range(len(sizes)), ratios, color='#0072B2', width=.6)
    ratio_ax.bar_label(bars, labels=[f'{x:.2f}×' for x in ratios], padding=3)
    ratio_ax.axhline(1, color='#333333', linewidth=1, linestyle='--')
    ratio_ax.set_ylim(0, max(2.5, max(ratios) * 1.2))
    ratio_ax.set_xticks(range(len(sizes)), [f'{n:,}'.replace(',', ' ') for n in sizes])
    ratio_ax.set_xlabel('Кількість синтетичних доставок')
    ratio_ax.set_ylabel('Медіана часу: межі / діапазони')
    ratio_ax.set_title('Співвідношення медіан')
    fig.suptitle('Інтервальне зіставлення в PostgreSQL 17.11')
    fig.text(0.5, -0.02, 'П’ять вимірів після прогріву; 371 тривога; позначки діапазону — мінімум і максимум.',
             ha='center', fontsize=9)
    try:
        for target in targets:
            fig.savefig(target, dpi=220, bbox_inches='tight')
            if target.suffix == '.svg':
                data = target.read_bytes().replace(b'\r\n', b'\n')
                target.write_bytes(b'\n'.join(line.rstrip(b' \t') for line in data.split(b'\n')))
    finally:
        plt.close(fig)
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'reports/postgres_benchmark_20261105/measurements.csv')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'reports/postgres_benchmark_20261105/figures')
    args = parser.parse_args()
    for path in plot(args.input, args.output_dir):
        print('Saved:', path)


if __name__ == '__main__':
    main()
