
from pathlib import Path

import numpy as np
import pandas as pd


# ==========================================
# 1. CONFIGURATION
# ==========================================

ROOT = Path(__file__).resolve().parents[1]

OUTPUT_PATH = ROOT / "data" / "synthetic" / "deliveries.csv"

SEED = 42
COUNT = 500

REGION_ID = "UA80000000000093317"

START_DATE = pd.Timestamp("2026-07-01T00:00:00Z")
END_DATE = pd.Timestamp("2026-10-01T00:00:00Z")

MIN_DURATION_SECONDS = 15 * 60
MAX_DURATION_SECONDS = 90 * 60


# ==========================================
# 2. GENERATE SYNTHETIC DATA
# ==========================================

def generate_deliveries():
    rng = np.random.default_rng(seed=SEED)

    # Довжина досліджуваного періоду
    total_seconds = int(
        (END_DATE - START_DATE).total_seconds()
    )

    # Випадкова тривалість доставки:
    # від 15 до 90 хвилин включно
    durations = rng.integers(
        MIN_DURATION_SECONDS,
        MAX_DURATION_SECONDS + 1,
        size=COUNT,
    )

    # Початок кожної доставки генерується
    # так, щоб її завершення не вийшло
    # за межі періоду дослідження.
    start_offsets = np.array([
        rng.integers(0, total_seconds - int(duration) + 1)
        for duration in durations
    ])

    started_at = (
        START_DATE
        + pd.to_timedelta(start_offsets, unit="s")
    )

    finished_at = (
        started_at
        + pd.to_timedelta(durations, unit="s")
    )

    # Умовна модельна відстань: 0.5–20 км
    distance_km = np.round(
        rng.uniform(0.5, 20.0, size=COUNT),
        2,
    )

    # Статуси згенеровані незалежно від тривог
    statuses = rng.choice(
        ["completed", "delayed", "cancelled"],
        size=COUNT,
        p=[0.85, 0.10, 0.05],
    )

    deliveries = pd.DataFrame({
        "delivery_id": [
            f"D-{i:05d}"
            for i in range(1, COUNT + 1)
        ],
        "region_id": REGION_ID,
        "started_at": started_at,
        "finished_at": finished_at,
        "distance_km": distance_km,
        "status": statuses,
    })

    return deliveries.sort_values(
        "started_at"
    ).reset_index(drop=True)


# ==========================================
# 3. DATA QUALITY CHECKS
# ==========================================

def validate_deliveries(df):
    assert len(df) == COUNT

    assert df["delivery_id"].is_unique

    assert not df.isnull().any().any()

    assert (
        df["finished_at"] > df["started_at"]
    ).all()

    assert (
        df["started_at"] >= START_DATE
    ).all()

    assert (
        df["finished_at"] <= END_DATE
    ).all()

    assert (
        df["region_id"] == REGION_ID
    ).all()

    print("Data quality checks: PASSED")


# ==========================================
# 4. SAVE RESULT
# ==========================================

def main():
    deliveries = generate_deliveries()

    validate_deliveries(deliveries)

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    deliveries.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    print(f"Generated: {len(deliveries)} deliveries")
    print(f"Saved to: {OUTPUT_PATH}")

    print("\nFirst 5 records:")
    print(deliveries.head().to_string(index=False))

    print("\nStatus distribution:")
    print(deliveries["status"].value_counts())


if __name__ == "__main__":
    main()
