BEGIN;

-- 1. Шари сховища

CREATE SCHEMA IF NOT EXISTS staging;

CREATE SCHEMA IF NOT EXISTS core;

CREATE SCHEMA IF NOT EXISTS mart;

CREATE SCHEMA IF NOT EXISTS control;

-- 2. Облік завантажень

CREATE TABLE IF NOT EXISTS control.load_batch (
    load_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type text NOT NULL,
    source_file text NOT NULL,
    source_sha256 text,
    transformation_version text,
    source_timezone_assumption text,
    timezone_verified boolean,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    rows_read bigint CHECK (rows_read >= 0),
    rows_accepted bigint CHECK (rows_accepted >= 0),
    rows_rejected bigint CHECK (rows_rejected >= 0),
    status text NOT NULL CHECK (
        status IN (
            'running',
            'success',
            'failed'
        )
    )
);

-- 3. Довідник регіонів

CREATE TABLE IF NOT EXISTS core.dim_region (
    region_id text PRIMARY KEY CHECK (length(trim(region_id)) > 0),
    region_name text NOT NULL CHECK (length(trim(region_name)) > 0)
);

-- 4. Факти доставок

CREATE TABLE IF NOT EXISTS core.fact_delivery (
    delivery_id text PRIMARY KEY CHECK (length(trim(delivery_id)) > 0),
    region_id text NOT NULL REFERENCES core.dim_region (region_id),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    distance_km numeric NOT NULL CHECK (distance_km >= 0),
    status text NOT NULL CHECK (
        status IN (
            'completed',
            'delayed',
            'cancelled'
        )
    ),
    load_id bigint NOT NULL REFERENCES control.load_batch (load_id),
    CHECK (finished_at > started_at)
);

-- 5. Факти повітряних тривог

CREATE TABLE IF NOT EXISTS core.fact_alert (
    event_id text PRIMARY KEY CHECK (length(trim(event_id)) > 0),
    region_id text NOT NULL REFERENCES core.dim_region (region_id),
    event_type text NOT NULL CHECK (length(trim(event_type)) > 0),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    load_id bigint NOT NULL REFERENCES control.load_batch (load_id),
    CHECK (finished_at > started_at)
);

-- 6. Довідник погодних точок

CREATE TABLE IF NOT EXISTS core.dim_weather_location (
    weather_location_id text PRIMARY KEY CHECK (
        length(trim(weather_location_id)) > 0
    ),
    latitude numeric NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude numeric NOT NULL CHECK (
        longitude BETWEEN -180 AND 180
    ),
    description text
);

-- 7. Погодинні погодні дані

CREATE TABLE IF NOT EXISTS core.fact_weather_hour (
    weather_location_id text NOT NULL REFERENCES core.dim_weather_location (weather_location_id),
    observed_at timestamptz NOT NULL,
    temperature_2m numeric NOT NULL,
    precipitation numeric NOT NULL CHECK (precipitation >= 0),
    wind_speed_10m numeric NOT NULL CHECK (wind_speed_10m >= 0),
    load_id bigint NOT NULL REFERENCES control.load_batch (load_id),
    PRIMARY KEY (
        weather_location_id,
        observed_at
    )
);

COMMIT;