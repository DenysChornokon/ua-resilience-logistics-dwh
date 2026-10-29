"""Нормалізація історичних повітряних тривог Києва у CSV."""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/raw/alerts/airAlert_2026-10-26.json'
OUTPUT = ROOT / 'data/processed/alerts.csv'
PERIOD_START = pd.Timestamp('2026-07-01', tz='UTC')
PERIOD_END = pd.Timestamp('2026-10-01', tz='UTC')
SOURCE_TIMEZONE = 'Europe/Kyiv'


def local_to_utc(values):
    """Interpret zone-free source timestamps in Kyiv; reject DST ambiguity."""
    parsed = pd.to_datetime(values, errors='raise')
    if parsed.dt.tz is not None:
        raise ValueError('Джерело має містити час без зазначеного поясу')
    return parsed.dt.tz_localize(
        SOURCE_TIMEZONE, ambiguous='raise', nonexistent='raise'
    ).dt.tz_convert('UTC')


def prepare_alerts():
    with SOURCE.open(encoding='utf-8-sig') as handle:
        records = json.load(handle)
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError('Очікувався JSON-масив об’єктів')

    columns = {
        'uid': 'event_id',
        'CATUTTC': 'region_id',
        'addressAdminUnitL2': 'region_name',
        'type': 'event_type',
        'dateTimeStart': 'started_at',
        'dateTimeEnd': 'finished_at',
    }
    df = pd.DataFrame.from_records(records)
    absent = set(columns) - set(df.columns)
    if absent:
        raise ValueError(f'Відсутні поля: {sorted(absent)}')
    alerts = df[list(columns)].rename(columns=columns).copy()
    print(f'Усього записів джерела: {len(alerts)}')

    # За рішенням користувача часові мітки без поясу трактуються як
    # Europe/Kyiv. Пояс у джерела не підтверджено. Попередній відбір
    # із запасом не має відкидати подій на межі UTC-вікна. Він також
    # не намагається локалізувати неоднозначні позначки поза вікном.
    for col in ('started_at', 'finished_at'):
        alerts[col] = pd.to_datetime(alerts[col], errors='coerce')
        if alerts[col].dt.tz is not None:
            raise ValueError('Джерело має містити час без зазначеного поясу')

    missing_time = alerts['started_at'].isna() | alerts['finished_at'].isna()
    print(f'Записів із відсутнім або некоректним часом: {int(missing_time.sum())}')
    city = alerts['region_name'].eq('місто Київ')
    kind = alerts['event_type'].eq('Повітряна тривога')
    margin = pd.Timedelta(days=2)
    lower = PERIOD_START.tz_localize(None) - margin
    upper = PERIOD_END.tz_localize(None) + margin
    candidates = city & kind & (alerts['started_at'] < upper) & (alerts['finished_at'] > lower)
    # Невідомий початок або кінець може приховувати запис у вікні.
    if (city & kind & missing_time).any():
        raise ValueError('Записи Києва з відсутнім або некоректним часом потребують перевірки')
    alerts = alerts.loc[candidates].copy()
    for col in ('started_at', 'finished_at'):
        alerts[col] = local_to_utc(alerts[col])

    invalid = (
        alerts['started_at'].isna()
        | alerts['finished_at'].isna()
        | alerts['region_id'].isna()
        | (alerts['finished_at'] <= alerts['started_at'])
    )
    print(f'Кандидатів у вікні з запасом: {len(alerts)}')
    print(f'Некоректних інтервалів серед кандидатів: {int(invalid.sum())}')

    within = (alerts['started_at'] < PERIOD_END) & (
        alerts['finished_at'] > PERIOD_START)
    selected = within
    if (selected & invalid).any():
        raise ValueError('Серед тривог Києва за період є некоректні інтервали')

    result = alerts.loc[selected].copy()
    result['started_at'] = result['started_at'].clip(lower=PERIOD_START)
    result['finished_at'] = result['finished_at'].clip(upper=PERIOD_END)
    result = result.sort_values(
        ['started_at', 'event_id']).reset_index(drop=True)
    if result['event_id'].duplicated().any():
        raise ValueError('Дублікати event_id у вибірці')

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT, index=False)
    print(f'Тривог Києва за період: {len(result)}')
    print(f'Збережено: {OUTPUT}')
    return result


if __name__ == '__main__':
    prepare_alerts()
