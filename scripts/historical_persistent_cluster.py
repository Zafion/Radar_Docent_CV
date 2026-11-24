"""Owned, resumable local PostgreSQL cluster for historical replay only."""
import fcntl, json, os, stat, sys, uuid
from pathlib import Path
import psycopg
from tests.postgres_isolated_validation import FreshCluster, ROOT, BIN, PORT

class HistoricalCluster(FreshCluster):
    def __init__(self, root, plan_sha256):
        requested=Path(root)
        if requested.is_symlink():raise ValueError('untrusted_cluster_directory')
        self.root=requested.resolve(); self.plan_sha256=plan_sha256
        if not self.root.is_relative_to((ROOT/'auditoria_local').resolve()): raise ValueError('cluster_outside_audit_directory')
        self.data=self.root/'d';self.sock=self.root/'s';self.env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_'))};self.started=False;self.lock=None
    def __enter__(self):
        if self.root.exists() and (self.root.is_symlink() or self.root.stat().st_uid!=os.getuid()):raise ValueError('untrusted_cluster_directory')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(self.root,0o700)
        self.lock=open(self.root/'owner.lock','a+')
        marker=self.root/'owner.json'
        try:
            fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            if marker.exists():
                if marker.is_symlink() or marker.stat().st_uid!=os.getuid():raise ValueError('untrusted_cluster_marker')
                state=json.loads(marker.read_text());self.token=state['token']
                if state['plan_sha256']!=self.plan_sha256 or state['root']!=str(self.root):raise ValueError('cluster_identity_mismatch')
                if self.data.is_symlink() or self.sock.is_symlink() or not (self.data/'PG_VERSION').is_file() or not self.sock.is_dir():raise ValueError('cluster_files_mismatch')
            else:
                if self.data.exists() or self.sock.exists():raise ValueError('unowned_cluster_contents')
                self.token=uuid.uuid4().hex;self.sock.mkdir(mode=0o700)
                self.command('initdb','-D',str(self.data),'--no-locale','--encoding=UTF8','--username=funk_validation_owner','--auth-local=trust','--auth-host=reject','--no-instructions')
                with (self.data/'postgresql.conf').open('a') as handle:handle.write(f"\nlisten_addresses = ''\nport = {PORT}\nunix_socket_directories = '{self.sock}'\nunix_socket_permissions = 0700\nmax_connections = 20\nshared_buffers = '32MB'\nlog_statement = 'none'\nlog_min_error_statement = 'panic'\nlog_error_verbosity = 'terse'\nfunkcionario.validation_token = '{self.token}'\n")
                fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as f:json.dump({'token':self.token,'root':str(self.root),'plan_sha256':self.plan_sha256},f);f.flush();os.fsync(f.fileno())
            if not (self.data/'postmaster.pid').exists():self.command('pg_ctl','-D',str(self.data),'-l',str(self.root/'server.log'),'-w','-t','20','start')
            with psycopg.connect(self.dsn('postgres')) as c:self.verify(c)
            self.started=True;return self
        except BaseException:
            if self.lock:self.lock.close();self.lock=None
            raise
    def __exit__(self,*args):
        try:
            if self.started:
                with psycopg.connect(self.dsn('postgres')) as c:self.verify(c)
                self.command('pg_ctl','-D',str(self.data),'-w','-t','20','-m','fast','stop')
        finally:
            if self.lock:self.lock.close();self.lock=None
            self.started=False
