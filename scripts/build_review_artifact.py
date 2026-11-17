"""Build a deterministic review archive from an explicit source allowlist.

No deployment, network, git mutation, environment/credential file or audit input
is copied. An existing validated Python environment supplies version metadata.
"""
import argparse
import gzip
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_ENTRYPOINTS={'run_send_push_test.py','run_send_telegram_test.py','run_generate_vapid_keys.py'}
SENSITIVE=[re.compile(rb'postgres(?:ql)?://[^\s:/]+:[^\s@]+@',re.I),
           re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
           re.compile(rb'(?<!\w)\d{7,12}:[A-Za-z0-9_-]{30,50}')]


def payload(root):
    names={n for n in ('LICENSE','NOTICE.txt','README.md','requirements.txt','.gitignore') if (root/n).is_file()}
    names.update(p.name for p in root.glob('run_*.py') if p.name not in EXCLUDED_ENTRYPOINTS)
    for prefix in ('app','scripts','tests','deploy'):
        for path in (root/prefix).rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to(root)
            if any(part.startswith('.') or part=='__pycache__' for part in relative.parts) or '.bak' in path.name:continue
            if prefix in {'scripts','tests'} and path.suffix not in {'.py','.sh'}:continue
            if prefix=='deploy' and path.suffix not in {'.service','.timer'}:continue
            if prefix=='app' and path.suffix not in {'.py','.sql','.html','.css','.js','.png','.ico'}:continue
            if path.is_symlink():raise ValueError('source_symlink_forbidden')
            names.add(relative.as_posix())
    files={}
    for name in sorted(names):
        path=root/name
        if path.is_symlink():raise ValueError('source_symlink_forbidden')
        content=path.read_bytes()
        if any(rule.search(content) for rule in SENSITIVE):raise ValueError('sensitive_pattern_in_allowlisted_file: '+name)
        files[name]=(content,0o755 if name.endswith('.sh') else 0o644)
    return files


def build(output,root=ROOT):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'auditoria_local/resultados') or output.exists():raise ValueError('new ignored output required')
    output.mkdir(parents=True)
    files=payload(root)
    base_path=root/'release/schema_pre_audit.sql'
    schema=base_path.read_bytes() if base_path.exists() else subprocess.check_output(['git','show','main:app/storage/schema.sql'],cwd=root)
    # No pip freeze/direct_url: those can contain authenticated URLs.
    versions=sorted({(d.metadata['Name'].lower().replace('_','-'),d.version) for d in metadata.distributions()})
    lock=''.join(name+'=='+version+'\n' for name,version in versions)
    files['release/requirements.lock']=(lock.encode(),0o644)
    files['release/schema_pre_audit.sql']=(schema,0o644)
    provenance=root/'release/provenance.json'
    if provenance.exists():source_provenance=provenance.read_bytes()
    else:
        source_provenance=(json.dumps({'base_main':subprocess.check_output(['git','rev-parse','main'],cwd=root,text=True).strip(),'python':os.sys.version.split()[0],'platform_scope':'Linux x86_64; PostgreSQL 16; validate other targets separately','source':'reviewed working tree, not committed HEAD'},sort_keys=True,indent=2)+'\n').encode()
    files['release/provenance.json']=(source_provenance,0o644)
    validation='''from pathlib import Path
import os,socket,sys,unittest
root=Path(__file__).resolve().parents[1];os.chdir(root);sys.path.insert(0,str(root))
for key in list(os.environ):
 if key.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_')):os.environ.pop(key,None)
os.environ['ENABLE_NOTIFICATIONS']='0';os.environ['INDEXNOW_KEY']=''
def forbidden(*args,**kwargs):raise AssertionError('external_network_forbidden_in_artifact_validation')
socket.socket.connect=forbidden;socket.create_connection=forbidden
for name in ('docs/auditoria_evidencias','auditoria_local/resultados'):(root/name).mkdir(parents=True,exist_ok=True)
from app.web.routes import TEMPLATES
names=TEMPLATES.env.list_templates()
for name in names:TEMPLATES.env.get_template(name)
print('compiled_templates',len(names),flush=True)
suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py',top_level_dir=str(root))
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
    files['release/check_artifact.py']=(validation.encode(),0o644)
    listing=[{'path':name,'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),'mode':oct(mode)} for name,(content,mode) in sorted(files.items())]
    files['release/files.json']=((json.dumps(listing,sort_keys=True,indent=2)+'\n').encode(),0o644)
    archive=output/'funkcionario-review.tar.gz'
    with archive.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        for name,(content,mode) in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(content);info.mode=mode;info.mtime=0;info.uid=info.gid=0;info.uname=info.gname=''
            tar.addfile(info,io.BytesIO(content))
    inventory=[{'path':name,'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),'mode':oct(mode)} for name,(content,mode) in sorted(files.items())]
    (output/'artifact_inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    summary={'files':len(files),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size,'locked_distributions':len(versions),'private_audit_inputs_copied':False,'dependencies_bundled':False}
    (output/'results.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary));return 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',required=True,type=Path)
    raise SystemExit(build(p.parse_args().output_dir))
