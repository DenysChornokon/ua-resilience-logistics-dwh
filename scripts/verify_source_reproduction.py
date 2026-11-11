"""Rebuild and compare the three core inputs from local source snapshots.

All regenerated files are written to a new output directory; inputs are not overwritten.
"""
import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import generate_synthetic_deliveries, load_dwh, prepare_alerts
from scripts.load_dwh import read_sources

ROOT = Path(__file__).resolve().parents[1]
RAW = ('data/raw/alerts/airAlert_2026-10-26.json', 'data/raw/weather/kyiv_weather.json')
OUTPUTS = ('data/processed/alerts.csv', 'data/processed/kyiv_weather.csv', 'data/synthetic/deliveries.csv')
FIELDS = ('time', 'temperature_2m', 'precipitation', 'wind_speed_10m')


def sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def weather_from_payload(payload):
    """Convert the saved Open-Meteo GMT hourly data without a network request."""
    if payload.get('utc_offset_seconds') != 0 or payload.get('timezone') not in ('GMT', 'UTC'):
        raise ValueError('Weather source must declare GMT/UTC with zero offset')
    lat, lon = payload.get('latitude'), payload.get('longitude')
    if (isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) or
        not isinstance(lon, (int, float)) or not math.isfinite(lat) or not math.isfinite(lon) or
        abs(lat - 50.4501) > 0.25 or abs(lon - 30.5234) > 0.4):
        raise ValueError('Weather grid point is missing or too far from Kyiv request coordinates')
    units = {'time': 'iso8601', 'temperature_2m': '°C', 'precipitation': 'mm', 'wind_speed_10m': 'km/h'}
    if any(payload.get('hourly_units', {}).get(k) != v for k, v in units.items()):
        raise ValueError('Unexpected or missing weather units')
    hourly = payload.get('hourly')
    if not isinstance(hourly, dict) or any(not isinstance(hourly.get(k), list) for k in FIELDS):
        raise ValueError('Missing weather arrays')
    lengths = {len(hourly[k]) for k in FIELDS}
    if len(lengths) != 1 or not next(iter(lengths)):
        raise ValueError('Weather arrays must have the same nonzero length')
    weather = pd.DataFrame({key: hourly[key] for key in FIELDS})
    weather['time'] = pd.to_datetime(weather['time'], format='%Y-%m-%dT%H:%M', utc=True, errors='raise')
    return weather


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.reader(handle, strict=True))


def reproduce(root, output, compare=True):
    root, output = Path(root).resolve(), Path(output).resolve()
    sources = {name: root / name for name in (*RAW, *(OUTPUTS if compare else ()))}
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError('Missing source or comparison files: ' + ', '.join(missing))
    before = {name: sha256(path) for name, path in sources.items()}
    output.mkdir(parents=True, exist_ok=False)
    for name in RAW:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources[name], destination)
        if sha256(destination) != before[name]:
            raise RuntimeError('Source changed while copying: ' + name)

    old_source, old_output = prepare_alerts.SOURCE, prepare_alerts.OUTPUT
    old_delivery = generate_synthetic_deliveries.OUTPUT_PATH
    try:
        prepare_alerts.SOURCE = output / RAW[0]
        prepare_alerts.OUTPUT = output / OUTPUTS[0]
        prepare_alerts.prepare_alerts()
        weather_payload = json.loads((output / RAW[1]).read_text(encoding='utf-8'))
        weather = weather_from_payload(weather_payload)
        weather_path = output / OUTPUTS[1]
        weather_path.parent.mkdir(parents=True, exist_ok=True)
        weather.to_csv(weather_path, index=False)
        generate_synthetic_deliveries.OUTPUT_PATH = output / OUTPUTS[2]
        generate_synthetic_deliveries.main()
    finally:
        prepare_alerts.SOURCE, prepare_alerts.OUTPUT = old_source, old_output
        generate_synthetic_deliveries.OUTPUT_PATH = old_delivery

    datasets = read_sources(output)
    comparisons = {}
    for name in OUTPUTS:
        result = output / name
        rebuilt_rows = csv_rows(result)
        item = {'rebuilt_sha256': sha256(result), 'data_rows': len(rebuilt_rows) - 1}
        if compare:
            item.update({'original_sha256': before[name], 'exact_bytes_equal': before[name] == item['rebuilt_sha256'],
                         'csv_fields_and_order_equal': csv_rows(sources[name]) == rebuilt_rows})
        comparisons[name] = item
    if any(sha256(path) != before[name] for name, path in sources.items()):
        raise RuntimeError('An original source changed during verification')
    complete = not compare or all(item['csv_fields_and_order_equal'] for item in comparisons.values())
    report = {
        'status': ('matched' if complete else 'mismatch') if compare else 'built_uncompared',
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'local raw alerts and weather JSON snapshots, deterministic synthetic delivery generator, existing Python environment',
        'raw_source_sha256': {name: before[name] for name in RAW},
        'code_sha256': {'verification': sha256(Path(__file__)), 'prepare_alerts': sha256(Path(prepare_alerts.__file__)),
                        'generate_deliveries': sha256(Path(generate_synthetic_deliveries.__file__)),
                        'load_validation': sha256(Path(load_dwh.__file__))},
        'weather_grid_point': {'latitude': weather_payload['latitude'], 'longitude': weather_payload['longitude'],
                               'requested_latitude': 50.4501, 'requested_longitude': 30.5234},
        'csv_comparison': comparisons,
        'validated_counts': {name: len(dataset.rows) for name, dataset in datasets.items()},
        'environment': {'python': platform.python_version(), 'pandas': pd.__version__, 'numpy': np.__version__},
        'limitations': 'No network source download or clean OS/Python installation; boundaries and large benchmarks excluded; synthetic deliveries are not real observations.',
    }
    with (output / 'source_reproduction_report.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    if not complete:
        raise AssertionError('Rebuilt CSV contents differ; inspect the saved report')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='new directory for source copies, regenerated files and report')
    parser.add_argument('--source-root', type=Path, default=ROOT, help='project or directory containing local data/raw source snapshots')
    parser.add_argument('--build-only', action='store_true', help='build from raw sources without comparing to existing CSV files')
    args = parser.parse_args()
    report = reproduce(args.source_root, args.output_dir, compare=not args.build_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
