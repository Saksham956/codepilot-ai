"""Crash only a trusted sleeper controller, then verify owned-resource recovery."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from isolation_probe import DockerProbe, create_args
from real_worker_ownership import RealWorkerOwnership
from synthetic_import_probe import IMAGE


def crash_fixture(root,docker_exe):
    docker=DockerProbe(docker_exe)
    ownership=RealWorkerOwnership(docker,root).__enter__()
    args=create_args(IMAGE,ownership.name,'import time; time.sleep(120)')
    if docker.call(*ownership.label_args(args)).returncode:raise RuntimeError('Fixture create failed')
    if docker.call('start',ownership.name).returncode:raise RuntimeError('Fixture start failed')
    # Deliberate abrupt exit: no Python finally/context cleanup runs.
    os._exit(86)


def check(root,docker_exe):
    module_dir=str(Path(__file__).resolve().parent)
    program=('import sys;sys.path.insert(0,'+repr(module_dir)+');'
             'from check_real_worker_recovery import crash_fixture;'
             'crash_fixture('+repr(str(root))+','+repr(docker_exe)+')')
    child=subprocess.run([sys.executable,'-I','-B','-c',program],capture_output=True,timeout=30)
    if child.returncode!=86:raise RuntimeError('Crash fixture did not reach its planned exit')
    docker=DockerProbe(docker_exe)
    with RealWorkerOwnership(docker,root) as ownership:
        recovered=ownership.recovery
        if len(recovered)!=1 or not all(r['cleanup_verified'] for r in recovered):
            raise RuntimeError('Interrupted resource was not recovered')
    return dict(status='real_worker_crash_recovery_verified',fixture='fixed trusted sleeper; no repository code',
                child_exit_code=86,recovered=recovered,cleanup_verified=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--docker',required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();result=check(a.root,a.docker);a.report.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
