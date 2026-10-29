from datetime import datetime, timedelta, timezone

from src.poc_interval_join import overlap_seconds


def ts(hour: int, minute: int = 0):
    return datetime(
        2026, 7, 1, hour, minute,
        tzinfo=timezone.utc
    )


def test_partial_overlap():
    result = overlap_seconds(
        ts(10, 15), ts(11, 30),
        ts(10, 45), ts(11, 45),
    )
    assert result == 45 * 60


def test_no_overlap():
    result = overlap_seconds(
        ts(10), ts(11),
        ts(12), ts(13),
    )
    assert result == 0


def test_touching_boundaries():
    result = overlap_seconds(
        ts(10), ts(11),
        ts(11), ts(12),
    )
    assert result == 0


def test_full_containment():
    result = overlap_seconds(
        ts(10), ts(12),
        ts(10, 30), ts(11),
    )
    assert result == 30 * 60
