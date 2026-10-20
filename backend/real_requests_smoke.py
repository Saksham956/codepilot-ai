"""Operator-only, pinned real-source Basic Auth smoke run. No arbitrary inputs."""
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

import artifact_materializer as materializer
import source_artifact as policy
from import_provenance_policy import check_import_receipt
from isolation_probe import DockerProbe, create_args
from patch_validator import safe_file
from synthetic_import_probe import IMAGE
from real_worker_ownership import RealWorkerOwnership

ARTIFACT = 'f31134cffb53a184b11ae553107bd4a620a65db9d7a18aebb1e4405137c9ac95'
REVISION = 'b25c87d7cb8d6a18a37fa12442b5f883f9e41741'
SPEC_SHA256 = 'ffe990da1104cde54240d9feacb285841a114ec7335733f32c509a6bfd067b0c'


def compatibility_spec():
    raw = Path(__file__).with_name('auth_compatibility_spec.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SPEC_SHA256:
        raise ValueError('Compatibility specification changed')
    return json.loads(raw)


def worker_script():
    code = ['import base64, hashlib, json, re, sys, stat, tempfile, importlib, importlib.util',
            'from pathlib import Path', 'from contextlib import contextmanager']
    for name in ('MAX_FILES', 'MAX_FILE_BYTES', 'MAX_TOTAL_BYTES', 'MAX_ARTIFACT_BYTES', 'BLOCKED_PARTS', 'BLOCKED_FILES'):
        code.append(name + ' = ' + repr(getattr(policy, name)))
    code.append('SECRET_MARKERS = re.compile(' + repr(policy.SECRET_MARKERS.pattern) + ', re.IGNORECASE)')
    for fn in (policy.approved_path, policy._source_bytes, policy._json_bytes,
               policy._reject_prefix_collisions, policy.verify_source_artifact,
               materializer._ordinary_directory, materializer.materialize_source_artifact):
        code.append(inspect.getsource(fn))
    code.append('DIGEST = ' + repr(ARTIFACT))
    code.append('COMPATIBILITY_CASES = ' + repr(compatibility_spec()['cases']))
    code.append('''
if importlib.util.find_spec('requests') is not None:
    raise ValueError('Requests already installed')
payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
with materialize_source_artifact(payload, DIGEST, '/tmp') as (root, manifest):
    sys.path.insert(0, str(root / 'src'))
    requests = importlib.import_module('requests')
    auth = importlib.import_module('requests.auth')
    cases = [('ascii', 'user', 'pass', b'user:pass'),
             ('latin1', 'usér', 'päss', b'us\\xe9r:p\\xe4ss'),
             ('bytes', b'\\xff', b'\\xfe', b'\\xff:\\xfe'),
             ('empty', '', '', b':')]
    results = []
    for name, username, password, expected in cases:
        actual = auth._basic_auth_str(username, password)
        wanted = 'Basic ' + base64.b64encode(expected).decode('ascii')
        results.append(dict(name=name, passed=type(actual) is str and actual == wanted))
    for name, username, password in [('unicode_username', '用户', 'pass'), ('unicode_password', 'user', '密码')]:
        try:
            auth._basic_auth_str(username, password)
        except UnicodeEncodeError:
            reproduced = True
        else:
            reproduced = False
        results.append(dict(name=name, passed=reproduced, expected_exception='UnicodeEncodeError'))
    prepared = requests.Request('GET', 'https://example.invalid/', auth=('user','pass')).prepare()
    results.append(dict(name='prepared_request_header_without_sending', passed=prepared.headers.get('Authorization') == 'Basic dXNlcjpwYXNz'))
    matrix = []
    for case in COMPATIBILITY_CASES:
        def decode(part):
            return bytes.fromhex(part['hex']) if part['kind'] == 'bytes' else part['value']
        try:
            header = auth._basic_auth_str(decode(case['username']), decode(case['password']))
        except UnicodeEncodeError:
            observed = dict(outcome='UnicodeEncodeError')
        else:
            if type(header) is not str or not header.startswith('Basic '):
                raise ValueError('Unexpected header format')
            observed = dict(outcome='header', authorization=header, userpass_hex=base64.b64decode(header[6:], validate=True).hex())
        matrix.append(dict(id=case['id'], observed=observed, passed=observed == case['legacy_expected_by_static_inference']))
    rows = []
    for module in (requests, auth):
        origin = str(Path(module.__spec__.origin).resolve())
        rows.append(dict(name=module.__name__, origin=origin, sha256=hashlib.sha256(Path(origin).read_bytes()).hexdigest()))
    receipt = dict(artifact_sha256=DIGEST, package_paths=list(requests.__path__), modules=rows)
    # Check every loaded module whose source belongs to this Requests snapshot.
    files = {str(root / row['path']): row['sha256'] for row in manifest['files']}
    loaded = []
    for name, module in list(sys.modules.items()):
        if name == 'requests' or name.startswith('requests.'):
            origin = getattr(getattr(module, '__spec__', None), 'origin', None)
            if origin and str(origin).startswith(str(root) + '/'):
                path = str(Path(origin).resolve())
                if path not in files or hashlib.sha256(Path(path).read_bytes()).hexdigest() != files[path]:
                    raise ValueError('Unexpected loaded source')
                loaded.append(name)
    result = dict(cases=results, compatibility_matrix=matrix, receipt=receipt, worker_root=str(root), loaded_source_modules=sorted(loaded), requests_version=requests.__version__)
    sys.path.pop(0)
if root.exists():
    raise RuntimeError('Materialized source cleanup failed')
result['materialization_cleanup_verified'] = True
print(json.dumps(result, sort_keys=True))
''')
    return '\n\n'.join(code)


def run(inputs, docker_exe):
    path = safe_file(inputs, 'source_artifact.json')
    with path.open('rb') as stream:
        payload = stream.read(policy.MAX_ARTIFACT_BYTES + 1)
    policy.verify_source_artifact(payload, ARTIFACT)
    docker = DockerProbe(docker_exe)
    with RealWorkerOwnership(docker) as ownership:
        return _run_owned(payload,docker,ownership)


def _run_owned(payload,docker,ownership):
    image = docker.inspect(IMAGE)
    if image['Id'] != IMAGE or image.get('Os') != 'linux' or image['Config'].get('Volumes'):
        raise ValueError('Pinned image unavailable')
    name = ownership.name
    process = reader = None
    captured = bytearray()
    exceeded = threading.Event()
    read_error = threading.Event()
    count = [0]
    try:
        args = create_args(IMAGE, name, worker_script())
        args.insert(1, '--interactive')
        if docker.call(*ownership.label_args(args)).returncode:
            raise RuntimeError('Create failed')
        config = docker.inspect(name)
        host = config['HostConfig']
        if (config['Image'] != IMAGE or config['Config']['User'] != '65534:65534'
            or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs'] or host.get('Binds')
            or host.get('Privileged') or host['Memory'] != 536870912 or host['MemorySwap'] != 536870912
            or host['PidsLimit'] != 64 or host['NanoCpus'] != 1000000000
            or set(host.get('CapDrop') or []) != {'ALL'}
            or 'no-new-privileges:true' not in host['SecurityOpt']
            or any(m['Type'] != 'tmpfs' for m in config.get('Mounts', []))):
            raise ValueError('Container profile mismatch')
        with tempfile.TemporaryFile() as source:
            source.write(payload)
            source.seek(0)
            process = subprocess.Popen(docker.command + ['start','--attach','--interactive',name],
                                       stdin=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            def drain():
                try:
                    while True:
                        chunk = process.stdout.read(8192)
                        if not chunk:
                            break
                        count[0] += len(chunk)
                        captured.extend(chunk[:max(0,65536-len(captured))])
                        if count[0] > 1048576:
                            exceeded.set()
                            break
                except Exception:
                    read_error.set()
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            deadline = time.monotonic() + 30
            while process.poll() is None:
                if exceeded.is_set() or time.monotonic() > deadline:
                    raise RuntimeError('Output limit or execution deadline exceeded')
                time.sleep(0.05)
            reader.join(timeout=5)
            if reader.is_alive() or read_error.is_set() or exceeded.is_set() or process.returncode:
                raise RuntimeError('Worker stream failed')
        state = docker.inspect(name)['State']
        if state['ExitCode'] or state.get('OOMKilled') or count[0] > 65536:
            raise RuntimeError('Worker result incomplete')
        result = json.loads(captured)
        checked = check_import_receipt(result['receipt'], payload, ARTIFACT, result['worker_root'])
        if not checked['target_modules_consistent'] or result['materialization_cleanup_verified'] is not True:
            raise ValueError('Source provenance or cleanup check failed')
        if len(result['cases']) != 7 or not all(row['passed'] is True for row in result['cases']):
            raise ValueError('Smoke checks failed')
        if len(result['compatibility_matrix']) != 12 or not all(row['passed'] is True for row in result['compatibility_matrix']):
            raise ValueError('Compatibility matrix failed')
    finally:
        clean = ownership.cleanup()
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if reader:
            reader.join(timeout=5)
        if not clean or (reader and reader.is_alive()):
            raise RuntimeError('Container/stream cleanup not verified')
    return dict(status='real_requests_smoke_passed', revision=REVISION, artifact_sha256=ARTIFACT,
                image_id=IMAGE, bootstrap_sha256=hashlib.sha256(worker_script().encode()).hexdigest(),
                compatibility_spec_sha256=SPEC_SHA256,
                source_executed=True, upstream_tests_executed=False, patch_applied=False,
                production_repository_executor_enabled=False, cleanup_verified=True,
                output_bytes=count[0], result=result, receipt_check=checked)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--docker', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.inputs, args.docker)
    args.report.write_text(json.dumps(report,indent=2), encoding='utf-8')
    print(json.dumps(report,indent=2))
