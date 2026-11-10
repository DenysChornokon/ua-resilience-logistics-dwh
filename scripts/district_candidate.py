"""Load and structurally validate the pinned experimental OSM-derived snapshot."""
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.prepare_spatial_research import ROOT

COMMIT='5b82d4c46c828f65b4f945098f8e6801b57ce1b0'
SHA256='4c79a55cbc0ad5f521e49388cd7f606ddeb8222d3b48b25ead4c40845802b8a3'
URL=f'https://raw.githubusercontent.com/zheniaslius/kyiv-air-map/{COMMIT}/public/data/raions.geojson'
PATH=ROOT/'data/raw/geography/kyiv_districts_candidate_20260913.geojson'


def validate(raw):
    if hashlib.sha256(raw).hexdigest()!=SHA256: raise ValueError('District source checksum mismatch')
    obj=json.loads(raw)
    if obj.get('type')!='FeatureCollection' or obj.get('crs') is not None: raise ValueError('Unexpected GeoJSON/CRS')
    features=[f for f in obj.get('features',[]) if f.get('properties',{}).get('oblast')=='UA80']
    if len(features)!=10: raise ValueError('Expected ten candidate districts')
    ids=set(); names=set()
    for f in features:
        p=f.get('properties',{}); ident=p.get('id'); name=p.get('name'); geom=f.get('geometry') or {}
        if not isinstance(ident,str) or not ident.startswith('UA80') or len(ident)!=19 or not ident[2:].isdigit() or not isinstance(name,str) or not name.strip():
            raise ValueError('Invalid district identity')
        if ident in ids or name in names: raise ValueError('Duplicate district')
        ids.add(ident);names.add(name)
        if geom.get('type')!='Polygon' or len(geom.get('coordinates',[]))!=1: raise ValueError('Unsupported geometry or polygon holes')
        for ring in geom['coordinates']:
            if len(ring)<4 or ring[0]!=ring[-1] or any(len(x)!=2 or not (29<x[0]<32 and 49<x[1]<52) for x in ring):
                raise ValueError('Invalid district coordinate ring')
    return sorted(features,key=lambda f:f['properties']['id'])


def inspect(path=PATH):
    return validate(Path(path).read_bytes())


def fetch(path=PATH):
    path=Path(path)
    if path.exists(): raise FileExistsError('Candidate file already exists')
    req=Request(URL,headers={'User-Agent':'UA-Resilience-DWH-research/1.0'})
    with urlopen(req,timeout=40) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000: raise ValueError('Unexpectedly large source')
    validate(raw)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f: f.write(raw)
    return len(raw)


if __name__=='__main__': print(f'Saved {fetch()} bytes with verified checksum')
