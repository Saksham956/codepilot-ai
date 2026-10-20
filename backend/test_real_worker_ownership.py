from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from real_worker_ownership import RealWorkerOwnership


class OwnershipTests(unittest.TestCase):
    def test_reserves_before_creation_and_labels_exact_identity(self):
        with tempfile.TemporaryDirectory() as directory,patch('real_worker_ownership.recover',return_value=[]),patch('real_worker_ownership._cleanup',return_value=True):
            with RealWorkerOwnership(Mock(),Path(directory)) as owner:
                row=owner.journal.connection.execute('SELECT name FROM resources').fetchone()
                self.assertEqual(row['name'],owner.name)
                args=owner.label_args(['create','--name',owner.name])
                self.assertIn('codepilot.job='+owner.job,args)
                self.assertIn('codepilot.journal='+owner.journal.journal_id,args)
                with self.assertRaises(ValueError):owner.label_args(['create','--name','other'])

    def test_unresolved_recovery_blocks_new_reservation(self):
        with tempfile.TemporaryDirectory() as directory,patch('real_worker_ownership.recover',return_value=[{'cleanup_verified':False}]):
            with self.assertRaises(RuntimeError):
                with RealWorkerOwnership(Mock(),Path(directory)):pass

    def test_unverified_cleanup_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory,patch('real_worker_ownership.recover',return_value=[]),patch('real_worker_ownership._cleanup',return_value=False):
            with self.assertRaises(RuntimeError):
                with RealWorkerOwnership(Mock(),Path(directory)):pass
