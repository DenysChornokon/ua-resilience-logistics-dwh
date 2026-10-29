from src.poc_interval_join import overlap_seconds
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


DELIVERIES = ROOT / "data" / "synthetic" / "deliveries.csv"
ALERTS = ROOT / "data" / "processed" / "alerts.csv"
OUTPUT = ROOT / "reports" / "first_experiment.csv"


def main():
    deliveries = pd.read_csv(DELIVERIES, dtype={"region_id": str})
    alerts = pd.read_csv(ALERTS, dtype={"region_id": str})

    for data in (deliveries, alerts):
        for column in ("started_at", "finished_at"):
            data[column] = pd.to_datetime(
                data[column], utc=True, errors="raise")

        if (data["finished_at"] <= data["started_at"]).any():
            raise ValueError("Знайдено інтервал із некоректною тривалістю")

    if deliveries["delivery_id"].duplicated().any():
        raise ValueError("Знайдено повторні delivery_id")

    if set(deliveries["region_id"]) != set(alerts["region_id"]):
        raise ValueError(
            "region_id у доставках і тривогах не збігаються. "
            "Перевірте та узгодьте ідентифікатори."
        )

    results = []

    for delivery in deliveries.itertuples(index=False):
        matching_alerts = alerts[
            (alerts["region_id"] == delivery.region_id)
            & (alerts["started_at"] < delivery.finished_at)
            & (alerts["finished_at"] > delivery.started_at)
        ]

        overlaps = [
            overlap_seconds(
                delivery.started_at,
                delivery.finished_at,
                alert.started_at,
                alert.finished_at,
            )
            for alert in matching_alerts.itertuples(index=False)
        ]

        results.append({
            "delivery_id": delivery.delivery_id,
            "region_id": delivery.region_id,
            "started_at": delivery.started_at,
            "finished_at": delivery.finished_at,
            "alert_count": len(overlaps),
            "has_alert_overlap": bool(overlaps),
        })

    result = pd.DataFrame(results)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT, index=False)

    affected = int(result["has_alert_overlap"].sum())
    print(f"Доставок: {len(result)}")
    print(f"Доставок із перетином тривоги: {affected}")
    print(f"Доставок без перетину: {len(result) - affected}")
    print(f"Результат збережено: {OUTPUT}")


if __name__ == "__main__":
    main()
