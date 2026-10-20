"""Durable ownership for the two operator-approved real-source workers."""
from pathlib import Path
from managed_probe_jobs import JobJournal, _cleanup, recover


class RealWorkerOwnership:
    def __init__(self,docker,root=None):
        self.docker=docker
        self.root=Path(root) if root is not None else Path(__file__).parent/'real_worker_journal'
        self.journal=JobJournal(self.root)

    def __enter__(self):
        self.journal.__enter__()
        try:
            self.recovery=recover(self.journal,self.docker)
            if any(not row['cleanup_verified'] for row in self.recovery):
                raise RuntimeError('Previous worker ownership could not be reconciled')
            # The journal records lifecycle only. The caller fixes source/image/code.
            self.job,self.name=self.journal.reserve('baseline')
            self.cleaned=False
            return self
        except BaseException:
            self.journal.__exit__(None,None,None)
            raise

    def label_args(self,args):
        if args[0]!='create' or '--name' not in args or args[args.index('--name')+1]!=self.name:
            raise ValueError('Container name differs from durable reservation')
        args=list(args)
        args[1:1]=['--label','codepilot.job='+self.job,'--label','codepilot.journal='+self.journal.journal_id]
        return args

    def cleanup(self):
        self.cleaned=_cleanup(self.journal,self.docker,self.job,self.name)
        return self.cleaned

    def __exit__(self,error_type,error,traceback):
        try:
            if not self.cleanup():
                self.journal.finish(self.job,{'status':'cleanup_error','cleanup_verified':False})
                raise RuntimeError('Owned worker cleanup unverified')
            self.journal.finish(self.job,{'status':'completed' if error_type is None else 'interrupted','cleanup_verified':True})
        finally:
            self.journal.__exit__(error_type,error,traceback)
