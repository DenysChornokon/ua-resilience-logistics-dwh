"""Safely append and activate a verified temporal snapshot, if it has changed."""
import json
import subprocess
from datetime import datetime,timezone
from uuid import uuid4

from scripts.district_candidate import COMMIT,SHA256
from scripts.load_district_context import SCENARIO,capture,verify_sources
from scripts.load_spatial_research import COMMAND
from scripts.prepare_spatial_research import ROOT
from scripts.verify_district_context import assert_fresh,snapshot_hash
from scripts.versioned_context import SPATIAL_SQL,active,assert_spatial_sources,refresh_sql,spatial_query,validate_snapshot


def main():
    _,points,point_meta,spatial=assert_spatial_sources()
    previous=active();validate_snapshot(previous,points,point_meta)
    if previous['spatial_fingerprint']!=spatial:raise RuntimeError('Spatial inputs changed since active version')
    observed,weather_counts=verify_sources(points);rows=observed.pop('rows');digest=snapshot_hash(rows)
    if observed==previous['metadata']['source_fingerprint'] and digest==previous['metadata']['context_sha256']:
        after=active();validate_snapshot(after,points,point_meta)
        if after['version_id']!=previous['version_id'] or after['spatial_fingerprint']!=spatial or spatial_query(SPATIAL_SQL)!=spatial:
            raise RuntimeError('Active version changed during validation; retry')
        assert_fresh(capture(),after['metadata'])
        print(json.dumps({'status':'unchanged','version_id':after['version_id'],'rows':len(rows)}));return
    metadata={'candidate_commit':COMMIT,'candidate_sha256':SHA256,
              'candidate_licence':'OSM-derived; transformation and redistribution terms not confirmed',
              'scenario_id':SCENARIO,'captured_at_utc':datetime.now(timezone.utc).isoformat(),
              'context_sha256':digest,'source_fingerprint':observed,'point_source_manifest':point_meta,'weather_verification':weather_counts}
    assert_fresh(capture(),metadata)
    ident=str(uuid4())
    subprocess.run(COMMAND,cwd=ROOT,input=refresh_sql(previous['version_id'],ident,rows,metadata,spatial).encode('utf-8'),check=True)
    after=active();validate_snapshot(after,points,point_meta)
    if after['version_id']!=ident or after['spatial_fingerprint']!=spatial or spatial_query(SPATIAL_SQL)!=spatial:
        raise RuntimeError('Version saved, but active version or spatial inputs changed; check before use')
    try:assert_fresh(capture(),after['metadata'])
    except RuntimeError as exc:raise RuntimeError('Version saved, but source changed around activation; run refresh again before use') from exc
    print(json.dumps({'status':'refreshed','version_id':ident,'previous_version_id':previous['version_id'],'rows':len(rows)}))


if __name__=='__main__':main()
