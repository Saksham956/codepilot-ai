import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import itsdangerous_exercise as exercise


class ExerciseTests(unittest.TestCase):
    def test_missing_source_does_not_launch_worker(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(exercise,'DockerProbe') as docker:
            with self.assertRaises((ValueError,FileNotFoundError)):
                exercise.run(Path(directory),'not-invoked')
            docker.assert_not_called()

    def test_changed_source_does_not_launch_worker(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(exercise,'DockerProbe') as docker:
            root=Path(directory);(root/'source').mkdir();(root/'source/a.py').write_text('print(1)\n')
            (root/'provenance.json').write_text(json.dumps({'files':[{'path':'a.py'}]}))
            with self.assertRaises(ValueError):exercise.run(root,'not-invoked')
            docker.assert_not_called()

    def test_explicit_test_scope(self):
        self.assertEqual(len(exercise.NODES),4)
        self.assertTrue(all(n.startswith(exercise.TEST+'::test_') for n in exercise.NODES))
        compile(exercise.script(exercise.BASE),'<worker>','exec')

    def test_recorded_comparison(self):
        result=json.loads(Path(__file__).with_name('itsdangerous_comparison_results.json').read_text())
        self.assertEqual(result['status'],'learning_comparison_passed')
        self.assertEqual(result['baseline']['passed'],8)
        self.assertEqual(result['baseline_repeat']['passed'],8)
        self.assertEqual(result['deliberately_broken']['failed'],2)
        self.assertEqual(result['repaired']['passed'],8)
        self.assertTrue(result['original_unchanged'])
