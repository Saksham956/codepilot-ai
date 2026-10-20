"""Local persistent report jobs with cooperative cancellation and one owner."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from managed_probe_jobs import JobJournal
from patch_validator import _reject_link


class ReportJobs:
    def __init__(self, root, investigate):
        self.root = Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        _reject_link(self.root)
        self.lease = JobJournal(self.root/'owner')
        self.lease.__enter__()
        self.lock = threading.RLock()
        self.closed = False
        self.investigate = investigate
        self.database = self.root/'reports.sqlite3'
        try:
            if self.database.exists():
                _reject_link(self.database)
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, mode TEXT, state TEXT, cancel INTEGER, created REAL, updated REAL, result TEXT, error TEXT)')
                db.execute("UPDATE jobs SET state='interrupted',updated=? WHERE state IN ('queued','running','cancelling')",(time.time(),))
            self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='report-job')
        except BaseException:
            self.lease.__exit__(None,None,None)
            raise

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database,timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, request):
        with self.lock, self.connect() as db:
            if self.closed:
                raise RuntimeError('Service is closing')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')").fetchone()[0] >= 4:
                raise OverflowError('Queue full')
            if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] >= 1000:
                raise OverflowError('Local history limit reached')
            identity = uuid.uuid4().hex
            now = time.time()
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(identity,request.mode,'queued',0,now,now,None,None))
        self.executor.submit(self._work,identity,request)
        return self.get(identity)

    def _work(self, identity, request):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT cancel FROM jobs WHERE id=?',(identity,)).fetchone()
            if row['cancel']:
                db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE id=?",(time.time(),identity))
                return
            db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),identity))
        try:
            report = self.investigate(request)
            result = json.dumps(report)
            if len(result.encode()) > 4*1024*1024:
                raise ValueError('Report too large')
            error = None
        except Exception:
            result = None
            error = 'Investigation failed. Check configuration, network and input; then try again.'
        with self.lock, self.connect() as db:
            cancelled = db.execute('SELECT cancel FROM jobs WHERE id=?',(identity,)).fetchone()[0]
            state = 'cancelled' if cancelled else 'failed' if error else 'completed'
            db.execute('UPDATE jobs SET state=?,result=?,error=?,updated=? WHERE id=?',
                       (state,None if cancelled else result,None if cancelled else error,time.time(),identity))

    def get(self, identity):
        if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
            raise KeyError(identity)
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        value = dict(row)
        value['result'] = json.loads(value['result']) if value['result'] else None
        value['cancel'] = bool(value['cancel'])
        return value

    def history(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,mode,state,created,updated FROM jobs ORDER BY created DESC LIMIT 50')]

    def cancel(self, identity):
        self.get(identity)
        with self.lock, self.connect() as db:
            db.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancelling' END,updated=? WHERE id=? AND state IN ('queued','running','cancelling')",(time.time(),identity))
        return self.get(identity)

    def close(self):
        with self.lock:
            self.closed = True
            with self.connect() as db:
                db.execute("UPDATE jobs SET cancel=1 WHERE state IN ('queued','running','cancelling')")
        self.executor.shutdown(wait=True)
        self.lease.__exit__(None,None,None)
