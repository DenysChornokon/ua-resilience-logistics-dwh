import csv
import json
import pytest

from scripts.generate_outage_scenarios import generate
from scripts.load_outage_scenarios import inspect, build_sql


def test_reproducible_nested_scenarios_and_import(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b'
    one,two=generate(a),generate(b)
    assert one['datasets']==two['datasets']
    seed,datasets=inspect(a)
    assert seed==42 and [len(datasets[n][3]) for n in (50,250,1000)]==[50,250,1000]
    shorter=datasets[50][3]; longer=datasets[250][3]
    assert [(r['started_at'],r['finished_at']) for r in shorter]==[(r['started_at'],r['finished_at']) for r in longer[:50]]
    sql=build_sql(seed,datasets)
    assert sql.count('COPY scenario.outage_events')==3 and 'BEGIN;' in sql and 'COMMIT;' in sql
    assert sql.count('\\.\n')==3
    assert all(datasets[n][2] in sql for n in datasets)


def test_refuses_overwrite_and_detects_tampering(tmp_path):
    generate(tmp_path)
    with pytest.raises(FileExistsError): generate(tmp_path)
    p=tmp_path/'outages_50.csv'; p.write_bytes(p.read_bytes()+b'altered')
    with pytest.raises(ValueError,match='Checksum mismatch'): inspect(tmp_path)


def test_rejects_wrong_source_type(tmp_path):
    generate(tmp_path)
    path=tmp_path/'manifest.json'; meta=json.loads(path.read_text(encoding='utf-8'))
    meta['source_type']='observed'; path.write_text(json.dumps(meta),encoding='utf-8')
    with pytest.raises(ValueError,match='Invalid scenario manifest'): inspect(tmp_path)
