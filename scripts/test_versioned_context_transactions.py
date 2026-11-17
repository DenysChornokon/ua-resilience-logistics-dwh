"""Exercise real refresh transactions with fixtures; all fixture writes roll back."""
import copy
import subprocess
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4

from scripts.load_spatial_research import COMMAND,literal
from scripts.prepare_spatial_research import ROOT
from scripts.verify_district_context import snapshot_hash
from scripts.versioned_context import (ACTIVE,VERSION,VIEW,active,assert_spatial_sources,
                                       id_literal,legacy,refresh_sql,spatial_query,validate_snapshot)


def run(sql):
    return subprocess.run(COMMAND,cwd=ROOT,input=sql.encode('utf-8'),capture_output=True)


def main():
    _,points,point_meta,spatial=assert_spatial_sources()
    before=active();validate_snapshot(before,points,point_meta)
    old_legacy=legacy();count=spatial_query(f'SELECT count(*) FROM {VERSION};')
    wrong=run(refresh_sql(str(uuid4()),str(uuid4()),before['rows'],before['metadata'],spatial))
    if wrong.returncode==0 or b'Active version changed' not in wrong.stderr:
        raise AssertionError('Concurrent active-version conflict was not rejected')
    incomplete_meta=copy.deepcopy(before['metadata']);incomplete_meta['context_sha256']=snapshot_hash(before['rows'][:-1])
    missing=run(refresh_sql(before['version_id'],str(uuid4()),before['rows'][:-1],incomplete_meta,spatial))
    if missing.returncode==0 or b'do not exactly match' not in missing.stderr:
        raise AssertionError('Incomplete version was not rejected')
    bad_spatial={**spatial,'points':'0'*32}
    mismatch=run(refresh_sql(before['version_id'],str(uuid4()),before['rows'],before['metadata'],bad_spatial))
    if mismatch.returncode==0 or b'Spatial inputs changed' not in mismatch.stderr:
        raise AssertionError('Changed spatial inputs were not rejected')
    simulated=copy.deepcopy(before['rows'])
    item=next(r for r in simulated if r['temperature_c'] is not None)
    item['temperature_c']=str(Decimal(item['temperature_c'])+Decimal('1.0'))
    meta=copy.deepcopy(before['metadata']);meta['context_sha256']=snapshot_hash(simulated);meta['captured_at_utc']=datetime.now(timezone.utc).isoformat()
    ident=str(uuid4());new=id_literal(ident);old=id_literal(before['version_id']);delivery=literal(item['delivery_id']);temp=literal(item['temperature_c'])
    sql=refresh_sql(before['version_id'],ident,simulated,meta,spatial,finish='ROLLBACK;')
    checks=f"""DO $$ BEGIN
      IF (SELECT version_id FROM {ACTIVE} WHERE singleton=true) IS DISTINCT FROM {new} THEN RAISE EXCEPTION 'New version not active'; END IF;
      IF NOT EXISTS (SELECT 1 FROM {VIEW} WHERE delivery_id={delivery} AND temperature_c={temp}::numeric AND version_id={new})
       THEN RAISE EXCEPTION 'Updated value missing from active view'; END IF;
      IF NOT EXISTS (SELECT 1 FROM {VERSION} WHERE version_id={old}) THEN RAISE EXCEPTION 'Previous version lost'; END IF;
    END $$;
    ROLLBACK;"""
    sql=sql.rsplit('ROLLBACK;',1)[0]+checks
    result=run(sql)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    after=active();validate_snapshot(after,points,point_meta)
    if after!=before or spatial_query(f'SELECT count(*) FROM {VERSION};')!=count or legacy()!=old_legacy:
        raise AssertionError('Rollback or previous version preservation failed')
    print('Conflict, incomplete version and changed spatial inputs rejected; simulated change switched atomically and rolled back; previous version preserved.')


if __name__=='__main__':main()
