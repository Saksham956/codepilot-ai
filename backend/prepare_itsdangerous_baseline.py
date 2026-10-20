"""Fetch pinned upstream Python source/tests as data, verifying Git blobs."""
import ast
import hashlib
import json
from pathlib import Path
from collect_auth_review_evidence import fetch
from source_artifact import approved_path, build_source_artifact

REVISION='096c8d42545d3b68ea21a4f890fb2b2d8979c0bd'
TREE='3ff0edaf2d039896397fe8d91d558935a038f823'
TEST='tests/test_itsdangerous/test_encoding.py'


def prepare(destination):
    api='https://api.github.com/repos/pallets/itsdangerous/git/'
    commit=json.loads(fetch(api+'commits/'+REVISION))
    if commit['tree']['sha']!=TREE:raise ValueError('Commit tree changed')
    tree=json.loads(fetch(api+'trees/'+TREE+'?recursive=1'))
    if tree.get('truncated'):raise ValueError('Incomplete tree')
    records=[]; contents={}
    for row in tree['tree']:
        name=row['path']
        if not ((name.startswith('src/itsdangerous/') and name.endswith('.py')) or name==TEST):continue
        approved_path(name)
        if row['type']!='blob' or row['mode']!='100644':raise ValueError('Nonordinary source')
        raw=fetch('https://raw.githubusercontent.com/pallets/itsdangerous/'+REVISION+'/'+name)
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if blob!=row['sha']:raise ValueError('Source identity mismatch')
        parsed=ast.parse(raw.decode('utf-8'))
        records.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),git_blob=blob,
                            imports=sorted({ast.unparse(n) for n in ast.walk(parsed) if isinstance(n,(ast.Import,ast.ImportFrom))})))
        contents[name]=raw
    destination.mkdir(exist_ok=False)
    source=destination/'source';source.mkdir()
    for name,raw in contents.items():
        target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    artifact=build_source_artifact(source,sorted(contents))
    (destination/'artifact.json').write_bytes(artifact.payload)
    report=dict(repository='https://github.com/pallets/itsdangerous',revision=REVISION,tree=TREE,
                artifact_sha256=artifact.sha256,files=records,
                fixture_policy='No repository conftest or pytest configuration included. Selected encoding tests use only pytest and package helpers.',
                ancestor_conftest_paths=[r['path'] for r in tree['tree'] if r['path'] in ('conftest.py','tests/conftest.py','tests/test_itsdangerous/conftest.py')])
    (destination/'provenance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True)
    prepare(p.parse_args().destination)
