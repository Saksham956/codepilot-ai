"""Fixed upstream baseline and explicitly seeded temporary-bug exercise."""
import hashlib
import inspect
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

import artifact_materializer as materializer
import source_artifact as policy
from patch_validator import prepare_patch_copy
from synthetic_import_probe import IMAGE
from isolation_probe import DockerProbe, create_args
from real_worker_ownership import RealWorkerOwnership

BASE='fa57efaa367a3e4c9cb34c5069e49eda83cbbe307e028cef39536159f619483e'
REVISION='096c8d42545d3b68ea21a4f890fb2b2d8979c0bd'
TARGET='src/itsdangerous/encoding.py'
ORIGINAL='return base64.urlsafe_b64encode(string).rstrip(b"=")'
BROKEN='return b"deliberate-learning-bug"'
TEST='tests/test_itsdangerous/test_encoding.py'
NODES=[TEST+'::'+name for name in ('test_want_bytes','test_base64','test_base64_bad','test_int_bytes')]


def script(digest):
    if not policy.re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Bad digest')
    code=['import base64, hashlib, json, re, sys, stat, tempfile, importlib, importlib.util, io, contextlib',
          'from pathlib import Path','from contextlib import contextmanager']
    for name in ('MAX_FILES','MAX_FILE_BYTES','MAX_TOTAL_BYTES','MAX_ARTIFACT_BYTES','BLOCKED_PARTS','BLOCKED_FILES'):
        code.append(name+' = '+repr(getattr(policy,name)))
    code.append('SECRET_MARKERS = re.compile('+repr(policy.SECRET_MARKERS.pattern)+', re.IGNORECASE)')
    for fn in (policy.approved_path,policy._source_bytes,policy._json_bytes,policy._reject_prefix_collisions,
               policy.verify_source_artifact,materializer._ordinary_directory,materializer.materialize_source_artifact):
        code.append(inspect.getsource(fn))
    code.extend(['DIGEST = '+repr(digest),'NODES = '+repr(NODES)])
    code.append('''
if importlib.util.find_spec('itsdangerous') is not None:
    raise ValueError('Unexpected preinstalled package')
payload=sys.stdin.buffer.read(MAX_ARTIFACT_BYTES+1)
with materialize_source_artifact(payload,DIGEST,'/tmp') as (root,manifest):
    sys.path.insert(0,str(root/'src'))
    import pytest
    import itsdangerous
    class Results:
        def __init__(self):self.rows=[];self.collected=[];self.collection_errors=0
        def pytest_collection_finish(self,session):self.collected=[item.nodeid for item in session.items]
        def pytest_collectreport(self,report):
            if report.failed:self.collection_errors+=1
        def pytest_runtest_logreport(self,report):
            if report.when=='call' or report.failed:self.rows.append(dict(node=report.nodeid,phase=report.when,outcome=report.outcome))
    tracker=Results()
    output=io.StringIO()
    args=['-q','-c','/dev/null','--noconftest','-p','no:cacheprovider','--basetemp=/tmp/codepilot-pytest']+[str(root/node) for node in NODES]
    with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
        exit_code=int(pytest.main(args,plugins=[tracker]))
    source_files={str(root/entry['path']):entry['sha256'] for entry in manifest['files']}
    origins=[]
    for name,module in list(sys.modules.items()):
        if name=='itsdangerous' or name.startswith('itsdangerous.'):
            origin=getattr(getattr(module,'__spec__',None),'origin',None)
            if not origin:raise ValueError('Missing package origin')
            origin=str(Path(origin).resolve())
            if origin not in source_files or hashlib.sha256(Path(origin).read_bytes()).hexdigest()!=source_files[origin]:
                raise ValueError('Imported source mismatch')
            origins.append(dict(module=name,origin=origin,sha256=source_files[origin]))
    result=dict(artifact_sha256=DIGEST,exit_code=exit_code,collected=len(tracker.collected),collection_errors=tracker.collection_errors,
                passed=sum(r['outcome']=='passed' for r in tracker.rows),failed=sum(r['outcome']=='failed' for r in tracker.rows),
                tests=tracker.rows,origins=origins,diagnostic_tail=output.getvalue()[-12000:])
    sys.path.pop(0)
if root.exists():raise RuntimeError('Source cleanup failed')
result['materialization_cleanup_verified']=True
print(json.dumps(result))
''')
    return '\n\n'.join(code)


def execute(docker, artifact):
    policy.verify_source_artifact(artifact.payload,artifact.sha256)
    with RealWorkerOwnership(docker) as ownership:
        return _execute_owned(docker,artifact,ownership)


def _execute_owned(docker,artifact,ownership):
    name=ownership.name
    process=reader=None;captured=bytearray();count=[0];exceeded=threading.Event()
    try:
        args=create_args(IMAGE,name,script(artifact.sha256));args.insert(1,'--interactive')
        if docker.call(*ownership.label_args(args)).returncode:raise RuntimeError('Container create failed')
        config=docker.inspect(name);host=config['HostConfig']
        if (config['Image']!=IMAGE or host['NetworkMode']!='none' or not host['ReadonlyRootfs']
            or config['Config']['User']!='65534:65534' or host.get('Binds') or host.get('Privileged')
            or host['Memory']!=536870912 or host['PidsLimit']!=64 or host['NanoCpus']!=1000000000
            or any(m['Type']!='tmpfs' for m in config.get('Mounts',[]))):raise ValueError('Container profile mismatch')
        with tempfile.TemporaryFile() as source:
            source.write(artifact.payload);source.seek(0)
            process=subprocess.Popen(docker.command+['start','--attach','--interactive',name],stdin=source,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            def drain():
                try:
                    while True:
                        chunk=process.stdout.read(8192)
                        if not chunk:break
                        count[0]+=len(chunk);captured.extend(chunk[:max(0,65536-len(captured))])
                        if count[0]>1048576:exceeded.set();break
                finally:process.stdout.close()
            reader=threading.Thread(target=drain,daemon=True);reader.start();deadline=time.monotonic()+30
            while process.poll() is None:
                if exceeded.is_set() or time.monotonic()>deadline:raise RuntimeError('Worker output/time limit')
                time.sleep(.05)
            reader.join(5)
            if reader.is_alive() or exceeded.is_set() or process.returncode or count[0]>65536:raise RuntimeError('Worker result incomplete: '+captured.decode('utf-8',errors='replace')[-3000:])
        result=json.loads(captured)
        state=docker.inspect(name)['State']
        if state['ExitCode'] or state.get('OOMKilled') or result['artifact_sha256']!=artifact.sha256 or result['materialization_cleanup_verified'] is not True:
            raise ValueError('Worker result/provenance failed')
        result['bootstrap_sha256']=hashlib.sha256(script(artifact.sha256).encode()).hexdigest()
    finally:
        clean=ownership.cleanup()
        if process and process.poll() is None:process.kill();process.wait(5)
        if reader:reader.join(5)
        if not clean or (reader and reader.is_alive()):raise RuntimeError('Cleanup unverified')
    result['container_cleanup_verified']=True
    return result


def run(inputs,docker_exe):
    source=inputs/'source'
    names=sorted(r['path'] for r in json.loads((inputs/'provenance.json').read_text())['files'])
    original=policy.build_source_artifact(source,names)
    if original.sha256!=BASE:raise ValueError('Pinned upstream source differs')
    docker=DockerProbe(docker_exe)
    baseline=execute(docker,original)
    repeat=execute(docker,original)
    mutation=[dict(file=TARGET,original_code=ORIGINAL,replacement_code=BROKEN)]
    repair=[dict(file=TARGET,original_code=BROKEN,replacement_code=ORIGINAL)]
    bug_owner=fix_owner=None
    try:
        bug_owner,bug_validation=prepare_patch_copy(source,mutation,[TARGET])
        if bug_owner is None:raise ValueError('Learning mutation rejected')
        buggy=Path(bug_owner.name)/'repository'
        bug_artifact=policy.build_source_artifact(buggy,names)
        broken=execute(docker,bug_artifact)
        fix_owner,fix_validation=prepare_patch_copy(buggy,repair,[TARGET])
        if fix_owner is None:raise ValueError('Learning repair rejected')
        restored=policy.build_source_artifact(Path(fix_owner.name)/'repository',names)
        if restored.sha256!=BASE:raise ValueError('Restoration changed upstream bytes')
        repaired=execute(docker,restored)
    finally:
        if fix_owner:fix_owner.cleanup()
        if bug_owner:bug_owner.cleanup()
    unchanged=policy.build_source_artifact(source,names).sha256==BASE
    success=(all(r['exit_code']==0 and r['collected']==8 and r['passed']==8 and r['failed']==0 for r in (baseline,repeat,repaired))
             and broken['exit_code']==1 and broken['collected']==8 and broken['failed']==2 and broken['passed']==6 and unchanged)
    return dict(status='learning_comparison_passed' if success else 'comparison_failed',repository='https://github.com/pallets/itsdangerous',
                revision=REVISION,worker_image=IMAGE,upstream_artifact_sha256=BASE,test_nodes=NODES,
                baseline=baseline,baseline_repeat=repeat,deliberately_broken=broken,repaired=repaired,
                mutation=mutation,repair=repair,patch_validation=fix_validation,original_unchanged=unchanged,
                scope='Upstream encoding tests on verified source; deliberately seeded temporary bug and exact restoration. Not an upstream bug fix.',
                generic_repository_executor_enabled=False)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);p.add_argument('--docker',required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();result=run(a.inputs,a.docker);a.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('baseline','baseline_repeat','deliberately_broken','repaired')},indent=2))
    raise SystemExit(result['status']!='learning_comparison_passed')
