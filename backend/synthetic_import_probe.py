"""Observe imports of two exact backend-authored fixtures, never real Requests."""
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

import artifact_materializer as materializer
import source_artifact as policy
from import_provenance_policy import check_import_receipt
from isolation_probe import DockerProbe, create_args

IMAGE = 'sha256:a708b1ec2b38a51fc947c5907a61e92a5781d9e9304ab230f37eb1a74520d654'
FIXTURES = {
    'src/requests/__init__.py': b'IS_SYNTHETIC_FIXTURE = True\n',
    'src/requests/auth.py': b'def add(left, right):\n    return left + right\n',
}
HASHES = {name: hashlib.sha256(raw).hexdigest() for name, raw in FIXTURES.items()}


def worker_script(digest):
    if not policy.re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid artifact digest')
    code = ['import base64, hashlib, json, re, sys, stat, tempfile, importlib, importlib.util',
            'from pathlib import Path', 'from contextlib import contextmanager']
    for name in ('MAX_FILES', 'MAX_FILE_BYTES', 'MAX_TOTAL_BYTES', 'MAX_ARTIFACT_BYTES', 'BLOCKED_PARTS', 'BLOCKED_FILES'):
        code.append(name + ' = ' + repr(getattr(policy, name)))
    code.append('SECRET_MARKERS = re.compile(' + repr(policy.SECRET_MARKERS.pattern) + ', re.IGNORECASE)')
    for fn in (policy.approved_path, policy._source_bytes, policy._json_bytes,
               policy._reject_prefix_collisions, policy.verify_source_artifact,
               materializer._ordinary_directory, materializer.materialize_source_artifact):
        code.append(inspect.getsource(fn))
    code.append('EXPECTED = ' + repr(HASHES))
    code.append('DIGEST = ' + repr(digest))
    code.append('''
if importlib.util.find_spec('requests') is not None:
    raise ValueError('Requests already discoverable')
payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
with materialize_source_artifact(payload, DIGEST, '/tmp') as (root, manifest):
    observed = {row['path']: row['sha256'] for row in manifest['files']}
    if observed != EXPECTED:
        raise ValueError('Only the two exact backend fixtures may execute')
    for relative, expected in EXPECTED.items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Fixture changed before import')
    sys.path.insert(0, str(root / 'src'))
    package = importlib.import_module('requests')
    auth = importlib.import_module('requests.auth')
    if package.IS_SYNTHETIC_FIXTURE is not True or auth.add(2, 3) != 5:
        raise ValueError('Synthetic fixture behavior mismatch')
    modules = []
    for module in (package, auth):
        origin = str(Path(module.__spec__.origin).resolve())
        modules.append(dict(name=module.__name__, origin=origin, sha256=hashlib.sha256(Path(origin).read_bytes()).hexdigest()))
    receipt = dict(artifact_sha256=DIGEST, package_paths=list(package.__path__), modules=modules)
    worker_root = str(root)
    sys.path.pop(0)
    del sys.modules['requests.auth']
    del sys.modules['requests']
if root.exists():
    raise RuntimeError('Input cleanup failed')
print(json.dumps(dict(receipt=receipt, worker_root=worker_root, materialization_cleanup_verified=True)))
''')
    return '\n\n'.join(code)


def run_probe(docker_exe):
    docker = DockerProbe(docker_exe)
    image = docker.inspect(IMAGE)
    if image['Id'] != IMAGE or image.get('Os') != 'linux' or image['Config'].get('Volumes'):
        raise ValueError('Unexpected immutable image configuration')
    name = 'codepilot-probe-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='codepilot-import-fixture-') as directory:
        root = Path(directory)
        for relative, raw in FIXTURES.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        artifact = policy.build_source_artifact(root, sorted(FIXTURES))
        try:
            args = create_args(IMAGE, name, worker_script(artifact.sha256))
            args.insert(1, '--interactive')
            if docker.call(*args).returncode:
                raise RuntimeError('Container creation failed')
            config = docker.inspect(name)
            host = config['HostConfig']
            if (config['Image'] != IMAGE or config['Config']['User'] != '65534:65534'
                    or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs']
                    or host.get('Binds') or host.get('Privileged')
                    or any(m['Type'] != 'tmpfs' for m in config.get('Mounts', []))):
                raise ValueError('Unexpected container isolation configuration')
            # Bounded output is guaranteed by the fixed bootstrap and exact literal fixtures.
            # This capture must not be reused for arbitrary repository programs.
            result = subprocess.run(docker.command + ['start', '--attach', '--interactive', name],
                                    input=artifact.payload, capture_output=True, timeout=30)
            if result.returncode or len(result.stdout) > 131072:
                raise RuntimeError('Synthetic import observation failed')
            observation = json.loads(result.stdout)
            state = docker.inspect(name)['State']
            if state['ExitCode'] or state.get('OOMKilled') or observation['materialization_cleanup_verified'] is not True:
                raise RuntimeError('Worker did not finish cleanly')
            checked = check_import_receipt(observation['receipt'], artifact.payload, artifact.sha256, observation['worker_root'])
            if not checked['target_modules_consistent']:
                raise ValueError('Import receipt inconsistent with artifact')
        finally:
            removed = docker.call('rm', '--force', name)
            listing = docker.call('ps', '--all', '--filter', 'name=^/' + name + '$', '--format', '{{.Names}}')
            if removed.returncode or listing.returncode or listing.stdout.strip():
                raise RuntimeError('Container cleanup not verified')
    if root.exists():
        raise RuntimeError('Host fixture cleanup failed')
    return dict(status='synthetic_import_observed', image_id=IMAGE, artifact_sha256=artifact.sha256,
                fixture_hashes=HASHES, observation=observation, receipt_check=checked,
                synthetic_import_observed=True, host_cleanup_verified=True, container_cleanup_verified=True,
                requests_source_executed=False, repository_execution_enabled=False, execution_approved=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = run_probe(args.docker)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
