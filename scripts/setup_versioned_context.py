"""Preserve the original district context as the first version; create a new view."""
import subprocess
from uuid import uuid4

from scripts.load_spatial_research import COMMAND
from scripts.prepare_spatial_research import ROOT
from scripts.versioned_context import assert_spatial_sources, legacy, setup_sql, validate_snapshot


def main():
    _,points,point_meta,spatial=assert_spatial_sources()
    old=legacy();validate_snapshot(old,points,point_meta)
    version=str(uuid4())
    subprocess.run(COMMAND,cwd=ROOT,input=setup_sql(version,old['rows'],old['metadata'],spatial).encode('utf-8'),check=True)
    print(f'Original temporal context preserved as version {version}.')


if __name__=='__main__':main()
