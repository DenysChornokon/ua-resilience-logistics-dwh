
from pathlib import Path

import pandas as pd
import requests

OUTPUT_DIR = Path("data/raw/weather")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://archive-api.open-meteo.com/v1/archive"

PARAMS = {
    "latitude": 50.4501,
    "longitude": 30.5234,
    "start_date": "2026-07-01",
    "end_date": "2026-09-30",
    "hourly": [
        "temperature_2m",
        "precipitation",
        "wind_speed_10m",
    ],
    "timezone": "GMT",
}

response = requests.get(
    URL,
    params=PARAMS,
    timeout=60,
)
response.raise_for_status()

output_json = OUTPUT_DIR / "kyiv_weather.json"
output_json.write_bytes(response.content)

payload = response.json()

weather = pd.DataFrame(payload["hourly"])
weather["time"] = pd.to_datetime(
    weather["time"],
    utc=True,
)

output_csv = OUTPUT_DIR / "kyiv_weather.csv"
weather.to_csv(output_csv, index=False)

print(f"Rows: {len(weather)}")
print(weather.head())
print(weather.isna().sum())
