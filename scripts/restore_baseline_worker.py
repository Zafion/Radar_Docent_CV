"""PostgreSQL restore worker. Executed unprivileged in a private net namespace."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

W=Path('/work'); BIN=Path('/usr/lib/postgresql/16/bin'); D=W/'data'; S=W/'socket'; ARCHIVE=W/'archive.dump'
ENV={'PATH':'/usr/bin:/bin','HOME':'/work','LANG':'C.UTF-8','PGPASSFILE':'/dev/null','PGSERVICEFILE':'/dev/null','PGSYSCONFDIR':'/work/empty','ENABLE_NOTIFICATIONS':'0'}

def run(args, data=None, capture=False):
    p=subprocess.run([str(BIN/args[0]),*args[1:]],input=data,stdout=subprocess.PIPE if capture else subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=ENV,timeout=1800)
    if p.returncode: raise RuntimeError('command_failed_'+args[0])
    return p.stdout if capture else b''

def sql(text, db='postgres', capture=False):
    return run(['psql','-X','-w','-q','-A','-t','-h',str(S),'-p','55467','-U','baseline_owner','-d',db,'-v','ON_ERROR_STOP=1'],text.encode(),capture)

def main():
    if os.getuid()==0 or Path('/proc/net/route').read_text().count('\n')>1: raise RuntimeError('isolation_failed')
    os.umask(0o077); S.mkdir(mode=0o700)
    toc=run(['pg_restore','--list',str(ARCHIVE)],capture=True).decode('utf8')
    if re.search(r'\b(TABLESPACE|FOREIGN SERVER|SUBSCRIPTION|EVENT TRIGGER)\b',toc):raise RuntimeError('unsupported_archive_object')
    tables=[]
    for line in toc.splitlines():
        m=re.match(r'^\d+; \d+ \d+ TABLE public ([a-z_][a-z_0-9]*) \S+$',line)
        if m:tables.append(m.group(1))
    if not tables or len(set(tables))!=len(tables):raise RuntimeError('archive_tables_invalid')
    run(['initdb','-D',str(D),'-U','baseline_owner','--encoding=UTF8','--locale=en_US.UTF-8','--auth-local=trust','--auth-host=reject'])
    (D/'postgresql.conf').write_text("listen_addresses=''\nport=55467\nunix_socket_directories='/work/socket'\nunix_socket_permissions=0700\nshared_buffers='32MB'\nmax_connections=10\nlog_statement='none'\nlog_min_error_statement=panic\n")
    started=False
    try:
        run(['pg_ctl','-D',str(D),'-l','/dev/null','-w','start']);started=True
        ident=json.loads(sql("SELECT json_build_object('version',current_setting('server_version_num'),'data',current_setting('data_directory'),'network',current_setting('listen_addresses'));",capture=True))
        if ident['data']!='/work/data' or ident['network'] or not 160000<=int(ident['version'])<170000:raise RuntimeError('postgres_identity')
        sql('CREATE DATABASE baseline TEMPLATE template0;')
        def restore(section):run(['pg_restore','--no-owner','--no-acl','--exit-on-error','--single-transaction','--section='+section,'-h',str(S),'-p','55467','-U','baseline_owner','-d','baseline',str(ARCHIVE)])
        restore('pre-data')
        if ' FUNCTION public normalize_text(' in toc:
            sql(Path('/code/normalize.sql').read_text(),db='baseline')
        restore('data');restore('post-data')
        actual=sql("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY 1;",'baseline',True).decode().splitlines()
        if sorted(tables)!=actual:raise RuntimeError('table_coverage_mismatch')
        invalid=sql("SELECT count(*) FROM pg_index WHERE NOT indisvalid OR NOT indisready;",'baseline',True).strip()
        if invalid!=b'0':raise RuntimeError('invalid_index')
        constraints=sql("SELECT count(*) FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated;",'baseline',True).strip()
        if constraints!=b'0':raise RuntimeError('unvalidated_constraint')
        counts={}
        for table in tables:
            counts[table]=int(sql('SELECT count(*) FROM public."'+table+'";','baseline',True))
        (W/'result.json').write_text(json.dumps({'status':'RESTORED','tables':counts,'server_version':ident['version'],'network':'disabled','source_exported':False},sort_keys=True)+'\n')
        run(['pg_ctl','-D',str(D),'-m','fast','-w','stop']);started=False
    finally:
        if started:
            try:run(['pg_ctl','-D',str(D),'-m','immediate','-w','stop'])
            except Exception:pass

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'STOP','condition':str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__}),file=sys.stderr)
        sys.exit(1)
