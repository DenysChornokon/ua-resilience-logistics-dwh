import pytest

from scripts.verify_source_reproduction import OUTPUTS, RAW, reproduce, weather_from_payload


@pytest.fixture
def payload():
    return {'timezone': 'GMT', 'utc_offset_seconds': 0, 'latitude': 50.439365, 'longitude': 30.476192,
            'hourly_units': {'time': 'iso8601', 'temperature_2m': '°C', 'precipitation': 'mm', 'wind_speed_10m': 'km/h'},
            'hourly': {'time': ['2026-07-01T00:00'], 'temperature_2m': [22.3],
                       'precipitation': [0.0], 'wind_speed_10m': [6.6]}}


def test_weather_uses_gmt_and_preserves_values(payload):
    row = weather_from_payload(payload).iloc[0]
    assert row['time'].isoformat() == '2026-07-01T00:00:00+00:00'
    assert (row['temperature_2m'], row['precipitation'], row['wind_speed_10m']) == (22.3, 0.0, 6.6)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update({'utc_offset_seconds': 10800}),
    lambda p: p.update({'timezone': 'Europe/Kyiv'}),
    lambda p: p.update({'latitude': 48.0}),
    lambda p: p['hourly_units'].update({'precipitation': 'inch'}),
    lambda p: p['hourly']['precipitation'].append(1),
    lambda p: p['hourly'].pop('wind_speed_10m'),
    lambda p: p['hourly'].update({'time': ['2026-07-01T00:00+03:00']}),
])
def test_weather_rejects_invalid_source_metadata_or_arrays(payload, mutation):
    mutation(payload)
    with pytest.raises((ValueError, TypeError)):
        weather_from_payload(payload)


def test_existing_output_directory_is_never_overwritten(tmp_path):
    root = tmp_path / 'original'
    for name in (*RAW, *OUTPUTS):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('original', encoding='utf-8')
    output = tmp_path / 'existing'
    output.mkdir()
    sentinel = output / 'keep.txt'
    sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(FileExistsError):
        reproduce(root, output)
    assert sentinel.read_text(encoding='utf-8') == 'keep'
