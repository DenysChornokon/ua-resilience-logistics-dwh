"""Capture public Kyiv portal reference codes and a pinned facilities dataset."""
import hashlib
import json
import re
from datetime import datetime,timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scripts.prepare_spatial_research import ROOT

OUTPUT=ROOT/'data/raw/geography/boundary_reference_20261110'
FACILITIES_URL='https://data.kyivcity.gov.ua/uploads/versions/version_4700bd1f_v1.5.json'
RESOURCE_URL='https://data.kyivcity.gov.ua/dataset/dani-pro-dytiachi-sportyvni-ta-inshi-maidanchyky-dlia-dozvillia-ta-vidpochynku-shcho-perebuva-aparat/resource/866aa611-ceaa-4937-a168-2a0e9240b2d1'
SLUGS=('holosiivska-rda','darnytska-rda','desnianska-rda','dniprovska-rda','obolonska-rda',
       'pecherska-rda','podilska-rda','sviatoshynska-rda','solomianska-rda','shevchenka-rda')


def parse_official_page(html):
    soup=BeautifulSoup(html,'html.parser'); text=soup.get_text(' ',strip=True)
    match=re.search(r'Код\s+КАТОТТГ\s*:\s*(UA\d{17})',text)
    names=[h.get_text(' ',strip=True) for h in soup.find_all('h4') if 'районна в місті Києві державна адміністрація' in h.get_text(' ',strip=True)]
    if not match or len(names)!=1:raise ValueError('Missing or ambiguous official district identity')
    return {'code':match.group(1),'organization':names[0]}


def capture(directory=OUTPUT):
    directory=Path(directory); targets=[directory/'district_index.json',directory/'sportplaygrounds.json',directory/'manifest.json']
    if any(p.exists() for p in targets):raise FileExistsError('Reference output already exists')
    session=requests.Session();session.headers.update({'User-Agent':'UAResilienceDWH/1.0 (academic data verification)'})
    records=[]
    for slug in SLUGS:
        url='https://data.kyivcity.gov.ua/organization/'+slug
        r=session.get(url,timeout=30);r.raise_for_status();data=parse_official_page(r.text)
        records.append({'url':url,'code':data['code'],'organization':data['organization'],
                        'html_sha256':hashlib.sha256(r.content).hexdigest()})
    if len({x['code'] for x in records})!=10:raise ValueError('Duplicate official district code')
    r=session.get(FACILITIES_URL,timeout=40);r.raise_for_status()
    if len(r.content)>10_000_000:raise ValueError('Unexpectedly large facility dataset')
    facilities=r.json()
    if not isinstance(facilities,list) or not facilities or not all(isinstance(x,dict) for x in facilities):
        raise ValueError('Unexpected facility dataset')
    index=json.dumps({'records':records},ensure_ascii=False,indent=2).encode('utf-8')+b'\n'
    manifest={'generator':'kyiv_official_boundary_reference_v1','retrieved_at_utc':datetime.now(timezone.utc).isoformat(),
              'index_sha256':hashlib.sha256(index).hexdigest(),'facilities_sha256':hashlib.sha256(r.content).hexdigest(),
              'facilities_url':FACILITIES_URL,'resource_page':RESOURCE_URL,'facilities_records':len(facilities),
              'source_license_statement':'Open Data Licence, per Kyiv data portal resource page'}
    directory.mkdir(parents=True,exist_ok=True);created=[]
    try:
        for path,raw in ((targets[0],index),(targets[1],r.content),(targets[2],json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')+b'\n')):
            with path.open('xb') as f:created.append(path);f.write(raw)
    except BaseException:
        for path in created:path.unlink(missing_ok=True)
        raise
    return manifest


if __name__=='__main__':print(json.dumps(capture(),ensure_ascii=False,indent=2))
