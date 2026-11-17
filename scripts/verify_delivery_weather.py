"""Compare every delivery's weather and context against the source CSV files."""
import argparse
import csv
import hashlib
import json
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from scripts.generate_density_scenarios import ROOT
from scripts.run_postgres_benchmark import query
from scripts.verify_multifactor_states import parse

KYIV = 'UA80000000000093317'
LOCATION = 'kyiv_open_meteo'
SQL = """SELECT json_build_object(
 'batches',(SELECT json_agg(row_to_json(b) ORDER BY b.load_id) FROM control.load_batch b WHERE b.source_type IN ('weather','deliveries')),
 'deliveries',(SELECT json_agg(json_build_array(delivery_id,region_id,started_at,finished_at,load_id) ORDER BY delivery_id) FROM core.fact_delivery),
 'weather',(SELECT json_agg(json_build_array(weather_location_id,observed_at,temperature_2m,precipitation,wind_speed_10m,load_id) ORDER BY weather_location_id,observed_at) FROM core.fact_weather_hour),
 'weather_rows',(SELECT json_agg(row_to_json(w) ORDER BY delivery_id) FROM mart.delivery_start_weather w),
 'context_rows',(SELECT json_agg(row_to_json(c) ORDER BY delivery_id) FROM mart.delivery_context c),
 'alert_rows',(SELECT json_agg(row_to_json(a) ORDER BY delivery_id) FROM mart.delivery_alert_exposure a),
 'weather_view',pg_get_viewdef('mart.delivery_start_weather'::regclass,true),
 'context_view',pg_get_viewdef('mart.delivery_context'::regclass,true)
);"""


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def weather_for(delivery, weather):
    if delivery['region_id'] != KYIV:
        return None
    start = parse(delivery['started_at']).astimezone(timezone.utc)
    hour = start.replace(minute=0, second=0, microsecond=0)
    return weather.get(hour)


def check(data, deliveries, observations, hashes):
    batches = data['batches'] or []
    chosen = {}
    for kind in ('weather', 'deliveries'):
        found = [b for b in batches if b['source_type']==kind and b['status']=='success']
        if len(found)!=1 or found[0]['source_sha256']!=hashes[kind]:
            raise AssertionError(f'Wrong {kind} source or missing successful load')
        chosen[kind] = found[0]
    weather = {}
    for row in observations:
        hour = parse(row['time']).astimezone(timezone.utc)
        if hour in weather or hour.minute or hour.second or hour.microsecond:
            raise AssertionError('Duplicate or non-hourly timestamp')
        weather[hour] = (Decimal(row['temperature_2m']),Decimal(row['precipitation']),Decimal(row['wind_speed_10m']))
    source_deliveries = {d['delivery_id']:d for d in deliveries}
    if len(source_deliveries)!=len(deliveries) or not deliveries or not weather:
        raise AssertionError('Missing or duplicate source data')
    if chosen['weather']['rows_accepted']!=len(weather) or chosen['deliveries']['rows_accepted']!=len(deliveries):
        raise AssertionError('Batch counts differ from input')
    actual_weather = data['weather'] or []
    if len(actual_weather)!=len(weather): raise AssertionError('Weather fact row count differs')
    for location,hour,temp,rain,wind,load_id in actual_weather:
        moment=parse(hour).astimezone(timezone.utc)
        if location!=LOCATION or load_id!=chosen['weather']['load_id'] or weather.get(moment)!=(Decimal(temp),Decimal(rain),Decimal(wind)):
            raise AssertionError(f'Weather fact differs: {moment}')
    actual_deliveries = data['deliveries'] or []
    if len(actual_deliveries)!=len(deliveries): raise AssertionError('Delivery fact row count differs')
    for ident,region,start,end,load_id in actual_deliveries:
        source=source_deliveries.get(ident)
        if (not source or region!=source['region_id'] or parse(start)!=parse(source['started_at'])
            or parse(end)!=parse(source['finished_at']) or load_id!=chosen['deliveries']['load_id']):
            raise AssertionError(f'Delivery fact differs: {ident}')
    by_id = {r['delivery_id']:r for r in data['weather_rows'] or []}
    context = {r['delivery_id']:r for r in data['context_rows'] or []}
    alert = {r['delivery_id']:r for r in data['alert_rows'] or []}
    if any(len(collection)!=len(deliveries) for collection in (data['weather_rows'] or [],data['context_rows'] or [],data['alert_rows'] or [],by_id,context,alert)):
        raise AssertionError('Missing or duplicated weather, context, or alert rows')
    counts={'deliveries':len(deliveries),'weather_known':0,'weather_unknown':0,'previous_hour_precipitation_gt_zero':0}
    fields=('region_id','delivery_started_at','weather_location_id','weather_observed_at','weather_known',
            'temperature_c','wind_speed_kmh','precipitation_previous_hour_mm','weather_load_id')
    for ident,source in source_deliveries.items():
        row,ctx,exposure=by_id[ident],context[ident],alert[ident]
        expected=weather_for(source,weather)
        if any(row[k]!=ctx[k] for k in fields) or parse(row['delivery_started_at'])!=parse(source['started_at']) or row['region_id']!=source['region_id']:
            raise AssertionError(f'Context differs from weather or source: {ident}')
        if parse(ctx['delivery_finished_at'])!=parse(source['finished_at']) or not ctx['alert_data_available']:
            raise AssertionError(f'Context misses delivery or alert: {ident}')
        for k in ('alert_overlap_seconds','alert_exposure_ratio','overlap_alert_count'):
            if ctx[k]!=exposure[k]: raise AssertionError(f'Alert context differs: {ident}')
        if expected is None:
            if row['weather_known'] or any(row[k] is not None for k in ('weather_observed_at','temperature_c','wind_speed_kmh','precipitation_previous_hour_mm','weather_location_id','weather_load_id')):
                raise AssertionError(f'Unknown weather represented as known: {ident}')
            counts['weather_unknown']+=1
        else:
            start=parse(source['started_at']).astimezone(timezone.utc)
            hour=start.replace(minute=0,second=0,microsecond=0)
            if (not row['weather_known'] or parse(row['weather_observed_at'])!=hour or row['weather_location_id']!=LOCATION
                or row['weather_load_id']!=chosen['weather']['load_id'] or
                (Decimal(row['temperature_c']),Decimal(row['precipitation_previous_hour_mm']),Decimal(row['wind_speed_kmh']))!=expected):
                raise AssertionError(f'Weather mismatch: {ident}')
            counts['weather_known']+=1
            counts['previous_hour_precipitation_gt_zero']+=expected[1]>0
    return counts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    paths={'weather':ROOT/'data/processed/kyiv_weather.csv','deliveries':ROOT/'data/synthetic/deliveries.csv'}
    hashes={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    data=json.loads(query(SQL),parse_float=Decimal)
    counts=check(data,read_csv(paths['deliveries']),read_csv(paths['weather']),hashes)
    report={'source_sha256':hashes,'verified_counts':counts,
            'weather_view_sha256':hashlib.sha256(data['weather_view'].encode('utf-8')).hexdigest(),
            'context_view_sha256':hashlib.sha256(data['context_view'].encode('utf-8')).hexdigest(),
            'semantics':'latest exact UTC hour at or before start; precipitation sum in preceding hour at that timestamp',
            'scope':'current loaded data; weather one Kyiv point; deliveries synthetic'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2); f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
