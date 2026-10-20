"""Observe a review image using fixed trusted code; never run repository code."""
import json
from pathlib import Path
import subprocess
import uuid

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
from isolation_probe import DockerProbe, create_args
from verify_requests_dependency_lock import verify_lock

SCRIPT = '''import importlib.metadata as m, importlib.util, json, os, platform, sys
v = sys.implementation.version
env = dict(python_version=platform.python_version_tuple()[0]+'.'+platform.python_version_tuple()[1], python_full_version=platform.python_version(), implementation_name=sys.implementation.name, implementation_version=platform.python_version(), platform_python_implementation=platform.python_implementation(), os_name=os.name, sys_platform=sys.platform, platform_system=platform.system(), platform_machine=platform.machine(), platform_release=platform.release(), platform_version=platform.version(), extra='')
print(json.dumps(dict(prefix=sys.prefix, environment=env, distributions=[dict(name=d.metadata['Name'], version=d.version) for d in m.distributions()], requests_discoverable=importlib.util.find_spec('requests') is not None)))
'''


def check_runtime(lock, receipt):
    if receipt['prefix'] != '/opt/worker-venv' or receipt['requests_discoverable']:
        raise ValueError('Unexpected interpreter or preinstalled Requests')
    env = receipt['environment']
    for key in ('python_version', 'implementation_name', 'os_name', 'sys_platform', 'platform_system', 'platform_machine'):
        if env[key] != lock['target'][key]:
            raise ValueError('Runtime target mismatch: ' + key)
    installed = {}
    for row in receipt['distributions']:
        name = canonicalize_name(row['name'])
        if name in installed:
            raise ValueError('Duplicate installed distribution')
        installed[name] = row['version']
    expected = {r['name']: r['version'] for r in lock['dependencies']}
    if set(installed) - set(expected) - {'pip'} or 'requests' in installed:
        raise ValueError('Unexpected installed distribution')
    for row in lock['dependencies']:
        if installed.get(row['name']) != row['version']:
            raise ValueError('Installed version differs from lock')
        if row['requires_python'] and Version(env['python_full_version']) not in SpecifierSet(row['requires_python']):
            raise ValueError('Actual Python is unsupported')
        for raw in row['requires_dist']:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate(env):
                continue
            if req.url or req.extras or canonicalize_name(req.name) not in expected or Version(expected[canonicalize_name(req.name)]) not in req.specifier:
                raise ValueError('Actual runtime dependency closure failed')
    return True


def validate(inputs, image_ref, docker):
    if not verify_lock(inputs)['metadata_closure_verified']:
        raise ValueError('Input lock failed offline verification')
    lock = json.loads((inputs / 'requests_harness_dependency_lock.json').read_text())
    image = docker.inspect(image_ref)
    image_id = image['Id']
    if image.get('Os') != 'linux' or image['Config'].get('Volumes') or image['Config'].get('Labels', {}).get('codepilot.dependency_lock') != lock['dependency_lock_sha256']:
        raise ValueError('Image identity/configuration mismatch')
    name = 'codepilot-probe-' + uuid.uuid4().hex
    try:
        created = docker.call(*create_args(image_id, name, SCRIPT))
        if created.returncode:
            raise RuntimeError('Could not create runtime observation container')
        run = docker.call('start', '--attach', name, timeout=30)
        if run.returncode or len(run.stdout) > 131072:
            raise RuntimeError('Runtime observation failed')
        receipt = json.loads(run.stdout)
        check_runtime(lock, receipt)
    finally:
        removed = docker.call('rm', '--force', name)
        listing = docker.call('ps', '--all', '--filter', 'name=^/' + name + '$', '--format', '{{.Names}}')
        if removed.returncode or listing.returncode or listing.stdout.strip():
            raise RuntimeError('Runtime observation cleanup not verified')
    probes = docker.run(image_id)
    if probes['status'] != 'synthetic_probes_passed':
        raise RuntimeError('New image isolation checks failed: ' + json.dumps(probes))
    return dict(status='offline_worker_runtime_verified', image_id=image_id,
                dependency_lock_sha256=lock['dependency_lock_sha256'], runtime=receipt,
                actual_runtime_dependency_closure_verified=True, isolation=probes,
                cleanup_verified=True, requests_source_executed=False,
                repository_execution_enabled=False, execution_approved=False,
                source_import_provenance_observed=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--image-ref', required=True)
    parser.add_argument('--docker', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.inputs, args.image_ref, DockerProbe(args.docker))
    args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
