import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from managed_probe_jobs import (
    CAPTURE_LIMIT, OUTPUT_LIMIT, JobJournal, _cleanup, _program,
    recover, run_managed_probe,
)


class ManagedProbeTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name) / "journal"

    def test_durable_intent_and_identity(self):
        with JobJournal(self.root) as journal:
            identity = journal.journal_id
            job, name = journal.reserve("sleep")
        with JobJournal(self.root) as journal:
            self.assertEqual(journal.journal_id, identity)
            row = journal.connection.execute("SELECT * FROM resources WHERE name=?", (name,)).fetchone()
            self.assertEqual(row["job"], job)
            self.assertEqual(row["state"], "planned")

    def test_live_controller_lock_blocks_second_controller(self):
        with JobJournal(self.root), self.assertRaises(RuntimeError):
            with JobJournal(self.root):
                self.fail("Second controller acquired the journal")
        with JobJournal(self.root):
            pass

    def test_unknown_probe_rejected(self):
        with JobJournal(self.root) as journal:
            with self.assertRaises(ValueError):
                journal.reserve("user-script")
        with self.assertRaises(ValueError):
            _program("rm -rf /")

    def test_recover_absent_resource(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        with JobJournal(self.root) as journal:
            job, _ = journal.reserve("orphan")
        with JobJournal(self.root) as journal:
            result = recover(journal, docker)
            self.assertTrue(result[0]["cleanup_verified"])
            self.assertEqual(result[0]["job_id"], job)
            self.assertEqual(recover(journal, docker), [])

    def test_recovery_checks_labels_and_removes_by_id(self):
        docker = Mock()
        cid = "a" * 64
        with JobJournal(self.root) as journal:
            job, name = journal.reserve("orphan")
            docker.call.side_effect = [Mock(returncode=0, stdout=cid.encode()), Mock(returncode=0), Mock(returncode=0, stdout=b"")]
            docker.inspect.return_value = {"Id": cid, "Config": {"Labels": {"codepilot.job": job, "codepilot.journal": journal.journal_id}}}
            self.assertTrue(_cleanup(journal, docker, job, name))
        self.assertEqual(docker.call.call_args_list[1].args, ("rm", "--force", cid))

    def test_unrelated_container_never_removed(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=("a" * 64).encode())
        docker.inspect.return_value = {"Id": "a" * 64, "Config": {"Labels": {"codepilot.journal": "other"}}}
        with JobJournal(self.root) as journal:
            job, name = journal.reserve("orphan")
            self.assertFalse(_cleanup(journal, docker, job, name))
            self.assertEqual(journal.connection.execute("SELECT state FROM resources").fetchone()[0], "planned")
        self.assertFalse(any(call.args[0] == "rm" for call in docker.call.call_args_list))

    def test_daemon_failure_not_treated_as_absence(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=1, stdout=b"")
        with JobJournal(self.root) as journal:
            journal.reserve("orphan")
            report = recover(journal, docker)
            self.assertEqual(report[0]["status"], "cleanup_error")
            with self.assertRaises(RuntimeError):
                run_managed_probe(journal, docker, "sleep")

    def test_bad_resource_names_refused(self):
        with JobJournal(self.root) as journal:
            docker = Mock()
            self.assertFalse(_cleanup(journal, docker, "bad", "existing-container"))
            docker.call.assert_not_called()

    def test_recovery_finishes_job_interrupted_after_resource_cleanup(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        with JobJournal(self.root) as journal:
            job, name = journal.reserve("orphan")
            _cleanup(journal, docker, job, name)
            result = recover(journal, docker)
            self.assertEqual(result[0]["status"], "recovered")

    def test_late_worker_is_reconciled_after_prior_absence(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        cid = "b" * 64
        with JobJournal(self.root) as journal:
            job, name = journal.reserve("orphan")
            recover(journal, docker)
            docker.call.side_effect = [Mock(returncode=0, stdout=cid.encode()), Mock(returncode=0, stdout=cid.encode()), Mock(returncode=0), Mock(returncode=0, stdout=b"")]
            docker.inspect.return_value = {"Id": cid, "Config": {"Labels": {"codepilot.job": job, "codepilot.journal": journal.journal_id}}}
            result = recover(journal, docker)
            self.assertTrue(result[0]["cleanup_verified"])

    def test_uncertain_creation_keeps_pending_record(self):
        import subprocess
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        with JobJournal(self.root) as journal, patch("managed_probe_jobs._create", side_effect=subprocess.TimeoutExpired("create", 15)):
            report = run_managed_probe(journal, docker, "sleep")
            self.assertEqual(report["status"], "cleanup_error")
            self.assertEqual(journal.connection.execute("SELECT state FROM resources").fetchone()[0], "uncertain")

    def test_pre_start_cancellation_is_persisted(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        with JobJournal(self.root) as journal, patch("managed_probe_jobs._create") as create:
            report = run_managed_probe(journal, docker, "sleep", cancelled=lambda: True)
            create.assert_not_called()
            state = journal.connection.execute("SELECT state FROM jobs").fetchone()[0]
            self.assertEqual(state, "cancelled")
        self.assertTrue(report["cleanup_verified"])

    def process(self, output=b"ready\n", running=True):
        process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(output))
        process.poll.return_value = None if running else 0
        return process

    def launch(self, journal, process, *, cancel=lambda: False, timeout=30):
        docker = Mock(command=["docker"])
        docker.call.return_value = Mock(returncode=0, stdout=b"")
        docker.inspect.return_value = {"State": {"ExitCode": 0}}
        with patch("managed_probe_jobs._create", return_value=(b"", None)), patch("managed_probe_jobs.subprocess.Popen", return_value=process):
            return run_managed_probe(journal, docker, "sleep", cancelled=cancel, timeout_seconds=timeout)

    def test_active_cancellation_while_worker_running(self):
        process = self.process()
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) >= 3
        with JobJournal(self.root) as journal:
            report = self.launch(journal, process, cancel=cancel)
        self.assertEqual(report["status"], "cancelled")
        self.assertTrue(report["cleanup_verified"])

    def test_streaming_capture_stays_bounded(self):
        process = self.process(b"x" * (OUTPUT_LIMIT + 8192))
        with JobJournal(self.root) as journal:
            report = self.launch(journal, process)
            persisted = journal.connection.execute("SELECT result FROM jobs").fetchone()[0]
        self.assertEqual(report["status"], "output_limit_exceeded")
        self.assertEqual(report["capture_bytes"], CAPTURE_LIMIT)
        self.assertGreater(report["output_bytes"], OUTPUT_LIMIT)
        self.assertLessEqual(report["output_bytes"], OUTPUT_LIMIT + 8192)
        self.assertLess(len(persisted), 2000)

    def test_active_timeout(self):
        with JobJournal(self.root) as journal:
            report = self.launch(journal, self.process(), timeout=0.01)
        self.assertEqual(report["status"], "timeout")
        self.assertTrue(report["cleanup_verified"])

    def test_incomplete_capture_never_reports_completed(self):
        with JobJournal(self.root) as journal:
            report = self.launch(journal, self.process(b"x" * (CAPTURE_LIMIT + 1), running=False))
        self.assertEqual(report["status"], "capture_limit_exceeded")

    def test_create_exception_leaves_recoverable_record(self):
        docker = Mock()
        docker.call.return_value = Mock(returncode=1, stdout=b"")
        with JobJournal(self.root) as journal, patch("managed_probe_jobs._create", side_effect=RuntimeError("private-path")):
            report = run_managed_probe(journal, docker, "sleep")
            self.assertEqual(report["status"], "cleanup_error")
            self.assertNotIn("private-path", json.dumps(report))
            self.assertEqual(journal.connection.execute("SELECT state FROM resources").fetchone()[0], "planned")


if __name__ == "__main__":
    unittest.main()
