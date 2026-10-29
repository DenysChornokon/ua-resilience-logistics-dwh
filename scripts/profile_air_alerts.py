
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from dateutil import tz

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data/raw/alerts/airAlert_2026-10-26.json"
OUTPUT_PATH = ROOT / "reports/air_alerts_profile.json"
SOURCE_TIMEZONE = "Europe/Kyiv"
KYIV = tz.gettz(SOURCE_TIMEZONE)
if KYIV is None:
    raise RuntimeError("IANA timezone Europe/Kyiv is unavailable")

NULL_VALUES = {"", "null", "none", "nan"}


def is_missing(value):
    return (
        value is None
        or isinstance(value, str)
        and value.strip().lower() in NULL_VALUES
    )


def parse_datetime(value):
    if is_missing(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def local_to_utc(value):
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is not None:
        return None, "invalid"
    if not tz.datetime_exists(parsed, KYIV):
        return None, "nonexistent"
    if tz.datetime_ambiguous(parsed, KYIV):
        return None, "ambiguous"
    return parsed.replace(tzinfo=KYIV).astimezone(timezone.utc), None

def profile():
    with INPUT_PATH.open("r", encoding="utf-8-sig") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("Expected a JSON array")

    if not all(isinstance(row, dict) for row in records):
        raise ValueError("Expected objects inside the array")

    print(f"Total records: {len(records)}")

    # 1. Field names, types and missing values
    all_fields = sorted({
        key
        for record in records
        for key in record
    })

    fields = {}

    for field in all_fields:
        values = [row.get(field) for row in records]

        fields[field] = {
            "types": dict(Counter(
                type(value).__name__
                for value in values
            )),
            "missing": sum(
                is_missing(value) for value in values
            ),
            "absent_key": sum(
                field not in row for row in records
            ),
        }

    # 2. Categories
    categorical_fields = [
        "CATUTTC",
        "addressAdminUnitL1",
        "addressAdminUnitL2",
        "addressPostName",
        "type",
        "level",
        "cause",
    ]

    categories = {}

    for field in categorical_fields:
        categories[field] = dict(Counter(
            str(row.get(field))
            for row in records
            if not is_missing(row.get(field))
        ))

    # 3. Duplicate IDs
    ids = [
        str(row["uid"])
        for row in records
        if not is_missing(row.get("uid"))
    ]

    id_counts = Counter(ids)
    duplicate_ids = {
        key: count
        for key, count in id_counts.items()
        if count > 1
    }

    # 4. Time interval validation
    valid_intervals = []
    invalid_intervals = 0
    missing_timestamps = 0
    invalid_timestamps = 0
    unresolved_timestamps = []

    for row in records:
        raw_start = row.get("dateTimeStart")
        raw_end = row.get("dateTimeEnd")

        if is_missing(raw_start) or is_missing(raw_end):
            missing_timestamps += 1
            continue

        start, start_issue = local_to_utc(raw_start)
        end, end_issue = local_to_utc(raw_end)

        if start_issue in {"ambiguous", "nonexistent"} or end_issue in {"ambiguous", "nonexistent"}:
            unresolved_timestamps.append({
                "uid": str(row.get("uid")),
                "start_issue": start_issue,
                "end_issue": end_issue,
            })
            continue

        if start is None or end is None:
            invalid_timestamps += 1
            continue

        try:
            duration = (end - start).total_seconds() / 60
        except TypeError:
            invalid_timestamps += 1
            continue

        if duration <= 0:
            invalid_intervals += 1
            continue

        valid_intervals.append({
            "region": str(row.get("CATUTTC") or "UNKNOWN"),
            "start": start,
            "end": end,
            "duration_min": duration,
        })

    durations = [
        item["duration_min"]
        for item in valid_intervals
    ]

    # 5. Перетини лише в межах одного регіону
    # Порівняння всіх областей між собою давало хибні перетини.
    from collections import defaultdict
    by_region = defaultdict(list)
    for interval in valid_intervals:
        by_region[interval["region"]].append(interval)

    overlapping_intervals = 0
    for intervals in by_region.values():
        latest_end = None
        for item in sorted(intervals, key=lambda x: x["start"]):
            if latest_end is not None:
                try:
                    if item["start"] < latest_end:
                        overlapping_intervals += 1
                except TypeError as exc:
                    raise ValueError(
                        "Змішані часові зони у датах джерела") from exc
            if latest_end is None or item["end"] > latest_end:
                latest_end = item["end"]

    # 6. Build final report
    report = {
        "source_file": INPUT_PATH.name,
        "source_timezone_assumption": SOURCE_TIMEZONE,
        "source_timezone_verified": False,
        "note": "Europe/Kyiv is assumed by user instruction; ambiguous or nonexistent local timestamps are excluded from interval statistics and listed separately.",
        "total_records": len(records),
        "fields": fields,
        "categories": categories,
        "duplicate_uid_values": duplicate_ids,
        "missing_timestamps": missing_timestamps,
        "invalid_timestamps": invalid_timestamps,
        "unresolved_timezone_intervals": unresolved_timestamps,
        "invalid_intervals": invalid_intervals,
        "valid_intervals": len(valid_intervals),
        "overlapping_intervals": overlapping_intervals,
        "min_start": (
            min(item["start"] for item in valid_intervals).isoformat()
            if valid_intervals else None
        ),
        "max_end": (
            max(item["end"] for item in valid_intervals).isoformat()
            if valid_intervals else None
        ),
        "duration_minutes": {
            "min": min(durations) if durations else None,
            "median": median(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("\nFields:")
    for name, stats in fields.items():
        print(f"  {name}: {stats}")

    print("\nCategories:")
    for name, values in categories.items():
        print(f"  {name}: {values}")

    print("\nQuality checks:")
    print(f"  Duplicate UID values: {len(duplicate_ids)}")
    print(f"  Missing timestamps: {missing_timestamps}")
    print(f"  Invalid timestamps: {invalid_timestamps}")
    print(f"  Invalid intervals: {invalid_intervals}")
    print(f"  Unresolved local-time intervals: {len(unresolved_timestamps)}")
    print(f"  Overlapping intervals: {overlapping_intervals}")

    print("\nTime coverage:")
    print(f"  First: {report['min_start']}")
    print(f"  Last:  {report['max_end']}")
    print(f"  Duration (minutes): {report['duration_minutes']}")

    print(f"\nReport saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    profile()
