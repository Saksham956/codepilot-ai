from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from report_job_service import ReportJobs


class JobTests(unittest.TestCase):
    def wait(self,service,identity):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            job=service.get(identity)
            if job['state'] not in ('queued','running','cancelling'):return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_completed_report_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ReportJobs(Path(directory),lambda r:{'status':'done'})
            try:
                job=service.submit(SimpleNamespace(mode='reviewed_example'))
                self.assertEqual(self.wait(service,job['id'])['result'],{'status':'done'})
            finally:service.close()
            service=ReportJobs(Path(directory),lambda r:None)
            try:self.assertEqual(service.get(job['id'])['state'],'completed')
            finally:service.close()

    def test_active_and_queued_cancellation(self):
        started=threading.Event(); release=threading.Event(); calls=[]
        def run(request):
            calls.append(1);started.set();release.wait(2);return {'status':'done'}
        with tempfile.TemporaryDirectory() as directory:
            service=ReportJobs(Path(directory),run)
            try:
                first=service.submit(SimpleNamespace(mode='reviewed_example'));self.assertTrue(started.wait(1))
                second=service.submit(SimpleNamespace(mode='reviewed_example'))
                self.assertEqual(service.cancel(first['id'])['state'],'cancelling')
                service.cancel(second['id']);release.set()
                self.assertEqual(self.wait(service,first['id'])['state'],'cancelled')
                self.assertIsNone(service.get(first['id'])['result'])
                self.assertEqual(self.wait(service,second['id'])['state'],'cancelled')
                self.assertEqual(len(calls),1)
            finally:release.set();service.close()

    def test_restart_marks_unfinished_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ReportJobs(Path(directory),lambda r:None)
            with service.connect() as db:
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',('a'*32,'live','running',0,1,1,None,None))
            service.close();service=ReportJobs(Path(directory),lambda r:None)
            try:self.assertEqual(service.get('a'*32)['state'],'interrupted')
            finally:service.close()

    def test_errors_sanitized(self):
        def fail(request):raise RuntimeError('secret provider detail')
        with tempfile.TemporaryDirectory() as directory:
            service=ReportJobs(Path(directory),fail)
            try:
                job=self.wait(service,service.submit(SimpleNamespace(mode='live'))['id'])
                self.assertEqual(job['state'],'failed');self.assertNotIn('secret',job['error'])
            finally:service.close()
