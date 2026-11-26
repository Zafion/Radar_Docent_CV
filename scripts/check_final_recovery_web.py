"""Exercise the final local candidate with a temporary read-only role and Chromium."""
import argparse,hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
WORK=VOLUME/'funk-404-674-independent-trial'
RECEIPT=VOLUME/'reconciliation_404_674_final_v3.json'
BROWSER=Path.home()/'.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-name',default='final_readonly_web_check.json')
    parser.add_argument('--loopback', action='store_true', help='exercise the recovery 127.0.0.1:8001 entrypoint')
    args=parser.parse_args()
    output_name=args.output_name
    if not output_name or Path(output_name).name!=output_name or not output_name.endswith('.json'):raise ValueError('output_name_invalid')
    sys.path.insert(0,str(ROOT))
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=2*1024**3)!=VOLUME:raise ValueError('volume_invalid')
    if RECEIPT.is_symlink() or hashlib.sha256(RECEIPT.read_bytes()).hexdigest()!='9b9ecbea01806d2f59c79c7d3906e551e25f1821da41beeebec2841b321538da':raise ValueError('receipt_invalid')
    for path in (WORK,WORK/'data',WORK/'socket'):
        s=path.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:raise ValueError('cluster_permissions')
    if (WORK/'data/postmaster.pid').exists() or not (BROWSER/'chrome-headless-shell').is_file():raise ValueError('cluster_running_or_browser_missing')
    target=VOLUME/output_name
    if os.path.lexists(target):raise ValueError('receipt_exists')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(WORK/'passwd'),'/etc/passwd','--ro-bind',str(WORK/'group'),'/etc/group','--ro-bind',str(ROOT),'/project',
         '--ro-bind',str(BROWSER),'/browser','--overlay-src',str(WORK),'--tmp-overlay','/work','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
         '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--chdir','/project','--','/project/.venv/bin/python','-B','-c',
         "from scripts.compare_17nov_isolated import api_worker; api_worker('baseline_404_674_trial',browser_check=True,hardened_role=True,recovery_loopback="+str(args.loopback)+")"]
    p=subprocess.run(cmd,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=900)
    if p.returncode:
        matches=re.findall(r'(?:ValueError|RuntimeError): ([A-Za-z0-9_]+)',p.stderr)
        classes=re.findall(r'(?:psycopg\.errors\.[A-Za-z]+|[A-Za-z_.]*(?:Error|Exception))(?=:)',p.stderr)
        relations=re.findall(r'relation "([A-Za-z0-9_.]+)" does not exist',p.stderr)
        reason=matches[-1] if matches else 'missing_relation_'+relations[-1].replace('.','_') if relations else classes[-1].replace('.','_') if classes else 'isolated_web_check_failed'
        if not reason or len(reason)>80 or not all(c.isascii() and (c.isalnum() or c=='_') for c in reason):reason='isolated_web_check_failed'
        print(json.dumps({'status':'STOP','reason':reason}));return 1
    report=json.loads(p.stdout.strip().splitlines()[-1]);report.update(candidate_receipt_sha256=hashlib.sha256(RECEIPT.read_bytes()).hexdigest(),cluster=str(WORK))
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(report));return 0

if __name__=='__main__':sys.exit(main())
