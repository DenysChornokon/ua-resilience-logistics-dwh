"""Validate and atomically load the current PoC CSV files into PostgreSQL.

Run from the project root: python -m scripts.load_dwh
Uses Docker Compose's psql; no database password is read or printed by Python.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = 'UA80000000000093317'
PERIOD_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
SOURCES = {
    'alerts': ('data/processed/alerts.csv', ['event_id', 'region_id', 'region_name', 'event_type', 'started_at', 'finished_at']),
    'deliveries': ('data/synthetic/deliveries.csv', ['delivery_id', 'region_id', 'started_at', 'finished_at', 'distance_km', 'status']),
    'weather': ('data/processed/kyiv_weather.csv', ['time', 'temperature_2m', 'precipitation', 'wind_speed_10m']),
}


@dataclass
class Dataset:
    name: str
    path: str
    fields: list[str]
    digest: str
    rows: list[dict[str, str]]


class ValidationError(Exception):
    pass


def instant(value: str) -> datetime:
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.utcoffset() is None:
        raise ValueError('часовий пояс або UTC-зміщення не вказано')
    return value.astimezone(timezone.utc)


def decimal_value(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('нечислове значення') from exc
    if not result.is_finite():
        raise ValueError('значення має бути скінченним')
    return result


def read_sources(root: Path) -> dict[str, Dataset]:
    datasets = {}
    errors = []
    for name, (relpath, fields) in SOURCES.items():
        path = root / relpath
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
            if reader.fieldnames != fields:
                raise ValueError(f'очікувані колонки {fields}, отримано {reader.fieldnames}')
            rows = list(reader)
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            errors.append(f'{relpath}: {exc}')
            continue
        if not rows:
            errors.append(f'{relpath}: файл не містить рядків')
        seen = set()
        times = []
        for line, row in enumerate(rows, 2):
            try:
                if None in row or any(row.get(field) is None or not row[field].strip() for field in fields):
                    raise ValueError('пропущене значення або невідповідна кількість полів')
                if any('\n' in row[field] or '\r' in row[field] for field in fields):
                    raise ValueError('перенесення рядка всередині поля не підтримується')
                key = row['event_id'] if name == 'alerts' else row['delivery_id'] if name == 'deliveries' else row['time']
                if key in seen:
                    raise ValueError(f'повторний ключ {key}')
                seen.add(key)
                if name in ('alerts', 'deliveries'):
                    if row['region_id'] != REGION:
                        raise ValueError('неочікуваний region_id')
                    start, end = instant(row['started_at']), instant(row['finished_at'])
                    if not (PERIOD_START <= start < end <= PERIOD_END):
                        raise ValueError('некоректний інтервал або вихід за межі UTC-вікна')
                if name == 'alerts':
                    if row['region_name'] != 'місто Київ' or row['event_type'] != 'Повітряна тривога':
                        raise ValueError('неочікувана назва регіону або тип тривоги')
                elif name == 'deliveries':
                    if not Decimal('0.5') <= decimal_value(row['distance_km']) <= Decimal('20'):
                        raise ValueError('відстань за межами контракту')
                    if row['status'] not in ('completed', 'delayed', 'cancelled'):
                        raise ValueError('неочікуваний статус доставки')
                else:
                    time = instant(row['time'])
                    if not PERIOD_START <= time < PERIOD_END or time.minute or time.second or time.microsecond:
                        raise ValueError('час погоди не є цілою годиною в UTC-вікні')
                    times.append(time)
                    decimal_value(row['temperature_2m'])
                    if decimal_value(row['precipitation']) < 0 or decimal_value(row['wind_speed_10m']) < 0:
                        raise ValueError('опади або вітер від’ємні')
            except (ValueError, OverflowError) as exc:
                errors.append(f'{relpath}:{line}: {exc}')
        if name == 'weather':
            expected = int((PERIOD_END - PERIOD_START) / timedelta(hours=1))
            if len(times) != expected or len(set(times)) != expected:
                errors.append(f'{relpath}: неповне або повторне погодинне покриття: {len(set(times))}/{expected}')
        datasets[name] = Dataset(name, relpath, fields, hashlib.sha256(raw).hexdigest(), rows)
    if errors:
        counts = Counter(message.split(':', 1)[0] for message in errors)
        raise ValidationError(f'Помилок перевірки: {len(errors)}; за файлами: {dict(counts)}\n' + '\n'.join(errors))
    return datasets


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def copy_rows(dataset: Dataset) -> str:
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerows([[row[field] for field in dataset.fields] for row in dataset.rows])
    return output.getvalue() + '\\.\n'


def make_sql(data: dict[str, Dataset]) -> str:
    a, d, w = data['alerts'], data['deliveries'], data['weather']
    checks = []
    for dataset, table in ((a, 'core.fact_alert'), (d, 'core.fact_delivery'), (w, 'core.fact_weather_hour')):
        checks.append(f"(SELECT count(*) FROM {table}) <> {len(dataset.rows)} OR EXISTS (SELECT 1 FROM {table} f LEFT JOIN control.load_batch b ON b.load_id=f.load_id WHERE b.load_id IS NULL OR b.status <> 'success' OR b.source_type <> {sql_literal(dataset.name)} OR b.source_sha256 IS DISTINCT FROM {sql_literal(dataset.digest)})")
    parts = ["""\\set ON_ERROR_STOP on
SET client_encoding = 'UTF8';
BEGIN;
SELECT pg_advisory_xact_lock(82991351);
""", f"""DO $guard$
BEGIN
  IF EXISTS (SELECT 1 FROM core.fact_alert) OR EXISTS (SELECT 1 FROM core.fact_delivery)
     OR EXISTS (SELECT 1 FROM core.fact_weather_hour) THEN
    IF {' OR '.join(checks)} THEN
      RAISE EXCEPTION 'Core tables contain different or incomplete data; no changes applied';
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM core.dim_region WHERE region_id = '{REGION}' AND region_name <> 'місто Київ') THEN
    RAISE EXCEPTION 'Region definition conflicts with input';
  END IF;
  IF EXISTS (SELECT 1 FROM core.dim_weather_location WHERE weather_location_id='kyiv_open_meteo' AND (latitude <> 50.4501 OR longitude <> 30.5234)) THEN
    RAISE EXCEPTION 'Weather location conflicts with input';
  END IF;
END $guard$;
CREATE TEMP TABLE load_decision AS SELECT NOT (EXISTS (SELECT 1 FROM core.fact_alert) OR EXISTS (SELECT 1 FROM core.fact_delivery) OR EXISTS (SELECT 1 FROM core.fact_weather_hour)) AS do_load;
CREATE TEMP TABLE tmp_alerts (event_id text, region_id text, region_name text, event_type text, started_at text, finished_at text) ON COMMIT DROP;
CREATE TEMP TABLE tmp_deliveries (delivery_id text, region_id text, started_at text, finished_at text, distance_km text, status text) ON COMMIT DROP;
CREATE TEMP TABLE tmp_weather (time text, temperature_2m text, precipitation text, wind_speed_10m text) ON COMMIT DROP;
"""]
    for table, dataset in [('tmp_alerts', a), ('tmp_deliveries', d), ('tmp_weather', w)]:
        parts.append(f"COPY {table} FROM STDIN WITH (FORMAT csv);\n")
        parts.append(copy_rows(dataset))
    parts.append('CREATE TEMP TABLE load_context (source_type text PRIMARY KEY, load_id bigint NOT NULL) ON COMMIT DROP;\n')
    for dataset in (a, d, w):
        tz_assumption = sql_literal('Europe/Kyiv') if dataset.name == 'alerts' else 'NULL'
        verified = 'false' if dataset.name == 'alerts' else 'NULL'
        parts.append(f"""WITH inserted AS (
  INSERT INTO control.load_batch (source_type, source_file, source_sha256, transformation_version, source_timezone_assumption, timezone_verified, finished_at, rows_read, rows_accepted, rows_rejected, status)
  SELECT {sql_literal(dataset.name)}, {sql_literal(dataset.path)}, {sql_literal(dataset.digest)}, 'csv_to_core_v1', {tz_assumption}, {verified}, now(), {len(dataset.rows)}, {len(dataset.rows)}, 0, 'success'
  WHERE (SELECT do_load FROM load_decision)
  RETURNING source_type, load_id
) INSERT INTO load_context SELECT source_type, load_id FROM inserted;
""")
    parts.append(f"""INSERT INTO core.dim_region(region_id, region_name)
SELECT '{REGION}', 'місто Київ' WHERE (SELECT do_load FROM load_decision) ON CONFLICT (region_id) DO NOTHING;
INSERT INTO core.dim_weather_location(weather_location_id, latitude, longitude, description)
SELECT 'kyiv_open_meteo', 50.4501, 30.5234, 'Open-Meteo: запитані координати Києва' WHERE (SELECT do_load FROM load_decision) ON CONFLICT (weather_location_id) DO NOTHING;
INSERT INTO core.fact_alert(event_id, region_id, event_type, started_at, finished_at, load_id)
SELECT event_id, region_id, event_type, started_at::timestamptz, finished_at::timestamptz, (SELECT load_id FROM load_context WHERE source_type='alerts') FROM tmp_alerts WHERE (SELECT do_load FROM load_decision);
INSERT INTO core.fact_delivery(delivery_id, region_id, started_at, finished_at, distance_km, status, load_id)
SELECT delivery_id, region_id, started_at::timestamptz, finished_at::timestamptz, distance_km::numeric, status, (SELECT load_id FROM load_context WHERE source_type='deliveries') FROM tmp_deliveries WHERE (SELECT do_load FROM load_decision);
INSERT INTO core.fact_weather_hour(weather_location_id, observed_at, temperature_2m, precipitation, wind_speed_10m, load_id)
SELECT 'kyiv_open_meteo', time::timestamptz, temperature_2m::numeric, precipitation::numeric, wind_speed_10m::numeric, (SELECT load_id FROM load_context WHERE source_type='weather') FROM tmp_weather WHERE (SELECT do_load FROM load_decision);
SELECT CASE WHEN do_load THEN 'LOADED' ELSE 'UNCHANGED' END AS result FROM load_decision;
COMMIT;
""")
    return ''.join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true', help='перевірити CSV без підключення до PostgreSQL')
    args = parser.parse_args()
    try:
        datasets = read_sources(ROOT)
    except ValidationError as exc:
        print(f'Перевірка не пройдена; база не змінена.\n{exc}', file=sys.stderr)
        return 1
    print('Перевірено:', ', '.join(f'{name}={len(data.rows)}' for name, data in datasets.items()), flush=True)
    if args.validate_only:
        print('Базу даних не змінено.')
        return 0
    cmd = ['docker', 'compose', 'exec', '-T', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'dwh_user', '-d', 'logistics_dwh']
    try:
        result = subprocess.run(cmd, input=make_sql(datasets).encode('utf-8'), cwd=ROOT, capture_output=True, check=False)
    except OSError as exc:
        print(f'Не вдалося запустити Docker: {exc}', file=sys.stderr)
        return 1
    print(result.stdout.decode('utf-8', errors='replace'), end='')
    if result.returncode:
        print('Завантаження не завершено; транзакцію скасовано.', file=sys.stderr)
        print(result.stderr.decode('utf-8', errors='replace'), file=sys.stderr)
        return result.returncode
    print('Час первинних тривог: Europe/Kyiv за рішенням користувача, не підтверджено розпорядником.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
