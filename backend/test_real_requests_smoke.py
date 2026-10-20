import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from real_requests_smoke import run, worker_script, ARTIFACT


class PinnedSourceGate(unittest.TestCase):
    def test_changed_artifact_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source_artifact.json').write_bytes(b'{}')
            with patch('real_requests_smoke.DockerProbe') as docker:
                with self.assertRaises(ValueError):
                    run(root, 'not-invoked')
                docker.assert_not_called()

    def test_valid_json_tampering_rejected_before_docker(self):
        source = Path(__file__).parent / 'real_requests_smoke_inputs/source_artifact.json'
        document = json.loads(source.read_bytes())
        document['files'][0]['content_b64'] = 'cHJpbnQoMSkK'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source_artifact.json').write_text(json.dumps(document))
            with patch('real_requests_smoke.DockerProbe') as docker:
                with self.assertRaises(ValueError):
                    run(root, 'not-invoked')
                docker.assert_not_called()

    def test_bootstrap_is_valid_python_with_pinned_artifact(self):
        script = worker_script()
        compile(script, '<trusted-worker>', 'exec')
        self.assertIn(repr(ARTIFACT), script)
