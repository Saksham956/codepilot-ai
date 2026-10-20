"""Fetch only pinned Requests package Python files as verified data."""
import ast
import hashlib
import json
from pathlib import Path
from collect_auth_review_evidence import fetch
from source_artifact import build_source_artifact, approved_path

REVISION = 'b25c87d7cb8d6a18a37fa12442b5f883f9e41741'
TREE = '2663dda7dbb8ce123af703b4573d85d09383ba0f'


def prepare(destination):
    api = 'https://api.github.com/repos/psf/requests/git/'
    commit = json.loads(fetch(api + 'commits/' + REVISION))
    if commit['sha'] != REVISION or commit['tree']['sha'] != TREE:
        raise ValueError('Pinned commit/tree mismatch')
    tree = json.loads(fetch(api + 'trees/' + TREE + '?recursive=1'))
    if tree.get('truncated'):
        raise ValueError('Truncated source tree')
    selected = sorted((r for r in tree['tree'] if r['path'].startswith('src/requests/') and r['path'].endswith('.py')), key=lambda r:r['path'])
    if not 1 <= len(selected) <= 40:
        raise ValueError('Unexpected package size')
    files = {}
    records = []
    for row in selected:
        name = approved_path(row['path'])
        if row['type'] != 'blob' or row['mode'] != '100644':
            raise ValueError('Only ordinary package files are allowed')
        raw = fetch('https://raw.githubusercontent.com/psf/requests/' + REVISION + '/' + name)
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if blob != row['sha']:
            raise ValueError('Pinned source blob mismatch')
        parsed = ast.parse(raw.decode('utf-8'), filename=name)
        records.append(dict(path=name, git_blob=blob, sha256=hashlib.sha256(raw).hexdigest(),
                            imports=sorted({ast.unparse(n) for n in ast.walk(parsed) if isinstance(n,(ast.Import,ast.ImportFrom))})))
        files[name] = raw
    destination.mkdir(exist_ok=False)
    root = destination / 'source'
    root.mkdir()
    for name, raw in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    artifact = build_source_artifact(root, sorted(files))
    (destination / 'source_artifact.json').write_bytes(artifact.payload)
    evidence = dict(repository='https://github.com/psf/requests', revision=REVISION, tree=TREE,
                    artifact_sha256=artifact.sha256, files=records, source_executed=False)
    (destination / 'source_evidence.json').write_text(json.dumps(evidence,indent=2), encoding='utf-8')
    print(json.dumps(evidence,indent=2))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    prepare(parser.parse_args().destination)
