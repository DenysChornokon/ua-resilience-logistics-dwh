from datetime import datetime, timezone
from decimal import Decimal

from scripts.verify_delivery_weather import KYIV, weather_for


def test_hour_selection_is_utc_and_does_not_use_future():
    hour = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    weather = {hour: (Decimal('20'), Decimal('0'), Decimal('4'))}
    assert weather_for({'region_id':KYIV, 'started_at':'2026-07-01T15:59:59+03:00'}, weather) == weather[hour]
    assert weather_for({'region_id':KYIV, 'started_at':'2026-07-01T16:00:00+03:00'}, weather) is None


def test_other_region_is_unknown_even_if_same_hour_exists():
    hour = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    assert weather_for({'region_id':'other', 'started_at':hour.isoformat()}, {hour:(1,2,3)}) is None
