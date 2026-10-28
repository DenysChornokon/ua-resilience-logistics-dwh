from datetime import datetime, timezone


def overlap_seconds(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> float:
    latest_start = max(first_start, second_start)
    earliest_end = min(first_end, second_end)

    return max(
        0.0,
        (earliest_end - latest_start).total_seconds()
    )
