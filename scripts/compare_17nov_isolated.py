"""Compare aggregate document-SHA counts using the restored isolated 17-Nov baseline."""
import json,os,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
VOLUME=ROOT/'auditoria_local/recuperacion_17nov_luks'
sys.path.insert(0,str(ROOT))

def launch():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    if validate_volatile_storage(minimum_bytes=0)!=VOLUME:raise ValueError('wrong_encrypted_volume')
    receipt_file=VOLUME/('baseline_17nov.dr.restore.json' if '--api-dr' in sys.argv or '--interruption-dr' in sys.argv else 'baseline_17nov.restore.json')
    receipt=json.loads(receipt_file.read_text());work=Path(receipt['work_directory'])
    if receipt.get('source_sha256')!='2abcde4ebb9914b41164885048d53c7aca6da3f5c141b3371a306e2048642d35' or receipt.get('status')!='BASELINE_RESTORED' or work.parent!=VOLUME:raise ValueError('receipt_mismatch')
    for p in (work,work/'data',work/'socket'):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:raise ValueError('cluster_identity')
    if (work/'data/postmaster.pid').exists():raise ValueError('cluster_running')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
         '--ro-bind',str(ROOT),'/project','--bind',str(work),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
         '--uid',str(os.getuid()),'--gid',str(os.getgid())]
    if '--browser-fresh' in sys.argv:
        browser=Path.home()/'.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64'
        if not (browser/'chrome-headless-shell').is_file():raise ValueError('local_browser_missing')
        cmd+=['--ro-bind',str(browser),'/browser']
    cmd+=['--','/project/.venv/bin/python','-B','/project/scripts/compare_17nov_isolated.py',('--interruption-worker' if '--interruption-dr' in sys.argv else '--browser-fresh-worker' if '--browser-fresh' in sys.argv else '--rollback-worker' if '--rollback' in sys.argv else '--api-fresh-worker' if '--api-fresh' in sys.argv or '--api-dr' in sys.argv else '--api-reproduction-worker' if '--api-reproduction' in sys.argv else '--api-worker' if '--api' in sys.argv else '--trial-worker' if '--trial' in sys.argv else '--worker')]
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=600)
    if p.returncode or not p.stdout:raise RuntimeError('isolated_comparison_failed')
    new=json.loads(p.stdout.strip().splitlines()[-1])
    if '--trial' in sys.argv or '--api' in sys.argv or '--api-reproduction' in sys.argv or '--api-fresh' in sys.argv or '--api-dr' in sys.argv or '--interruption-dr' in sys.argv or '--browser-fresh' in sys.argv or '--rollback' in sys.argv:
        target=OUT/('ensayo_interrupcion_desastre_20261124.json' if '--interruption-dr' in sys.argv else 'ensayo_baseline17_api_desastre_20261124.json' if '--api-dr' in sys.argv else 'ensayo_web_consulta_fresh_20261124.json' if '--browser-fresh' in sys.argv else 'ensayo_baseline17_rollback_20261124.json' if '--rollback' in sys.argv else 'ensayo_baseline17_api_fresh_20261124.json' if '--api-fresh' in sys.argv else 'ensayo_baseline17_api_reproduccion_20261124.json' if '--api-reproduction' in sys.argv else 'ensayo_baseline17_api_20261124.json' if '--api' in sys.argv else 'ensayo_baseline17_clon_20261124.json');tmp=target.with_suffix('.tmp')
        fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(new,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,target);print(json.dumps(new),flush=True);return
    old=json.loads((OUT/'comparacion_baseline_restaurado_20261123.json').read_text())
    prev=old['document_rows'];a={x['sha256'] for x in prev if x['document_presence'] in ('both','baseline_only')}
    c={x['sha256'] for x in prev if x['document_presence'] in ('both','reconstruction_only')};b=set(new['sha'])
    result={'status':'PASS','method':'SHA and per-document aggregate counts, not row-level identity comparison',
      'baseline_31_vs_17':{'baseline_31':len(a),'baseline_17':len(b),'both':len(a&b),'only_31':len(a-b),'only_17':len(b-a)},
      'baseline_17_vs_reconstruction':{'baseline_17':len(b),'reconstruction':len(c),'both':len(b&c),'only_17':len(b-c),'only_reconstruction':len(c-b)},
      'document_date_max':new['document_date_max'],'downloaded_at_max_utc':new['downloaded_at_max_utc'],
      'latest_parse_completed_utc':new['latest_parse_completed_utc'],'families':{},'notifications':new['notifications'],
      'limits':['Document date and download time are not necessarily publication dates.','Counts do not establish record identity.']}
    manifests=[]
    for name in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
        for entry in json.loads((OUT/name).read_text())['documents']:
            sha=entry['sha256'];family=entry['family']
            manifests.append({'manifest':name,'version_id':entry['version_id'],'sha256':sha,'family':family,
                'in_baseline_17':sha in b,'in_reconstruction':sha in c,
                'baseline_17_rows':new['families'].get(family,{}).get(sha,0),
                'decision':'pending_row_identity_comparison'})
    result['manifest_documents']=manifests
    for family,groups in new['families'].items():
        prior31={x['sha256']:x['counts'][family]['baseline'] for x in prev}
        priorrec={x['sha256']:x['counts'][family]['reconstruction'] for x in prev}
        def summary(oldset,other):
            shared=b&oldset
            return {'shared_sha_equal_count':sum(groups.get(s,0)==other.get(s,0) and groups.get(s,0)>0 for s in shared),
                    'shared_sha_different_count':sum(groups.get(s,0)!=other.get(s,0) for s in shared)}
        result['families'][family]={'baseline17_total':sum(groups.values()),'vs31':summary(a,prior31),'vs_reconstruction':summary(c,priorrec)}
    target=OUT/'comparacion_17nov_agregada_20261124.json';tmp=target.with_suffix('.tmp')
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,target);print(json.dumps(result),flush=True)

def worker():
    import psycopg
    from scripts.compare_restored_baseline import QUERIES
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(host='/work/socket',port=55467,user='baseline_owner',dbname='baseline',options='-c default_transaction_read_only=on') as db:
            if db.execute("SELECT current_setting('listen_addresses'),current_setting('data_directory'),current_setting('transaction_read_only')").fetchone()!=('','/work/data','on'):raise ValueError('database_identity')
            sha=[r[0] for r in db.execute('SELECT sha256 FROM document_versions WHERE sha256 IS NOT NULL')]
            families={key:{r[0]:r[1] for r in db.execute(query)} for key,query in QUERIES.items()}
            doc=db.execute('SELECT max(document_date_iso) FROM documents').fetchone()[0]
            downloaded=db.execute("SELECT max(downloaded_at) FROM document_versions").fetchone()[0]
            parsed=db.execute('SELECT max(finished_at) FROM document_parse_runs').fetchone()[0]
            notifications={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('public_alerts','notification_posts','push_notification_events')}
            print(json.dumps({'sha':sha,'families':families,'document_date_max':str(doc) if doc else None,'downloaded_at_max_utc':str(downloaded) if downloaded else None,'latest_parse_completed_utc':str(parsed) if parsed else None,'notifications':notifications}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def trial_worker():
    import psycopg
    from psycopg import sql
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner')
    name='baseline17_trial'
    tables=('documents','document_versions','award_results','award_assignments','offered_positions','docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates','public_alerts','notification_posts','push_notification_events')
    def counts(db):
        return {t:db.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in tables}
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            if admin.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses')").fetchone()!=('/work/data',''):raise ValueError('database_identity')
            if admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():raise ValueError('trial_already_exists')
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE baseline').format(sql.Identifier(name)))
        with psycopg.connect(**kw,dbname='baseline',options='-c default_transaction_read_only=on') as source:before=counts(source)
        with psycopg.connect(**kw,dbname=name) as db:
            if counts(db)!=before:raise ValueError('initial_clone_mismatch')
            for f in ('20261122_document_validation_metadata.sql','20261122_historical_procedures.sql'):
                db.execute((ROOT/'app/storage/migrations'/f).read_text())
            db.execute('CREATE SCHEMA IF NOT EXISTS recovery_17_review')
            db.execute('CREATE TABLE IF NOT EXISTS recovery_17_review.documents (source_sha text PRIMARY KEY,source_version bigint NOT NULL,target_document_id bigint,reason text NOT NULL)')
            entries=[]
            for file in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
                entries.extend(json.loads((OUT/file).read_text())['documents'])
            for entry in entries:
                target=db.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(entry['sha256'],)).fetchone()
                db.execute('INSERT INTO recovery_17_review.documents VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (entry['sha256'],entry['version_id'],target[0] if target else None,'pending_cross_baseline_row_identity_and_provenance'))
            db.commit();n=db.execute('SELECT count(*) FROM recovery_17_review.documents').fetchone()[0]
            for entry in entries:db.execute('INSERT INTO recovery_17_review.documents VALUES (%s,%s,NULL,%s) ON CONFLICT DO NOTHING',(entry['sha256'],entry['version_id'],'pending_cross_baseline_row_identity_and_provenance'))
            db.commit()
            if db.execute('SELECT count(*) FROM recovery_17_review.documents').fetchone()[0]!=n:raise ValueError('not_idempotent')
            try:
                with db.transaction():
                    db.execute("INSERT INTO recovery_17_review.documents VALUES ('synthetic-rollback',0,NULL,'test')")
                    raise ValueError('rollback_probe')
            except ValueError as exc:
                if str(exc)!='rollback_probe':raise
            if db.execute("SELECT count(*) FROM recovery_17_review.documents WHERE source_sha='synthetic-rollback'").fetchone()[0]:raise ValueError('rollback_failed')
            after=counts(db)
            if after!=before:raise ValueError('baseline_counts_changed')
            invalid=db.execute("SELECT count(*) FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()[0]
            if invalid:raise ValueError('invalid_constraints')
            existing=db.execute('SELECT count(*) FROM recovery_17_review.documents WHERE target_document_id IS NOT NULL').fetchone()[0]
        with psycopg.connect(**kw,dbname='baseline',options='-c default_transaction_read_only=on') as source:
            if counts(source)!=before:raise ValueError('baseline_changed')
        print(json.dumps({'status':'CLONE_READY_QUARANTINED','database':name,'before':before,'after':after,
                          'migration_count':2,'manifest_unique_shas':n,'present_in_baseline':existing,'quarantined':n,
                          'idempotence':'staging_no_duplicates','rollback':'staging_transaction_only','functional_rows_added':0,
                          'limitations':['Existing 31-Oct reconciliation assumes previous baseline and has not been run on 17-Nov.',
                                         'Full rollback after functional reconciliation remains untested.']}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def api_worker(target_db='baseline17_trial',browser_check=False,recovery_loopback=False,hardened_role=False,restored_candidate=False,production_restrictions=False):
    import time,uuid,psycopg,httpx
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False;server=None;role='read17_'+uuid.uuid4().hex[:12];created=False
    if target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh','baseline_404_674_trial') and not (restored_candidate and target_db=='baseline'):raise ValueError('target_identity')
    kw=dict(host='/work/socket',port=55467,user='baseline_owner')
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)));created=True
        with psycopg.connect(**kw,dbname=target_db,autocommit=True) as db:
            if hardened_role:
                db.execute(sql.SQL('REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(target_db)))
                if production_restrictions:
                    db.execute('REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM PUBLIC')
                db.execute("""DO $$ DECLARE f record; BEGIN
                  FOR f IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                           WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND (p.prosecdef OR p.provolatile='v')
                  LOOP EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',f.fn); END LOOP;
                END $$""")
                db.execute("""DO $$ DECLARE f record; BEGIN
                  FOR f IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                           WHERE n.nspname='pg_catalog' AND p.proname IN ('pg_notify','lo_create','lo_creat','lo_import','lo_unlink','lo_put')
                  LOOP EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',f.fn); END LOOP;
                END $$""")
            db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(target_db),sql.Identifier(role)))
            db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            db.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
            if production_restrictions:
                with psycopg.connect(**{**kw,'user':role},dbname=target_db,autocommit=True) as restricted_probe:
                    try:restricted_probe.execute("SELECT public.normalize_text('GARCÍA')")
                    except psycopg.errors.InsufficientPrivilege:pass
                    else:raise ValueError('expected_normalize_permission_failure_missing')
                db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.normalize_text(text) TO {}').format(sql.Identifier(role)))
                with psycopg.connect(**{**kw,'user':role},dbname=target_db,autocommit=True) as restricted_probe:
                    try:restricted_probe.execute("SELECT public.normalize_text('GARCÍA')")
                    except psycopg.errors.InsufficientPrivilege:pass
                    else:raise ValueError('expected_unaccent_permission_failure_missing')
                db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.unaccent(regdictionary,text) TO {}').format(sql.Identifier(role)))
            for schema in ('recovery_batch','recovery_03nov','recovery_0519','recovery_quarantine'):
                if db.execute('SELECT to_regnamespace(%s) IS NOT NULL',(schema,)).fetchone()[0]:
                    db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
                    db.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            row=db.execute('SELECT person_name_normalized FROM award_results WHERE person_name_normalized IS NOT NULL LIMIT 1').fetchone()
            if not row:raise ValueError('sample_missing')
            name=row[0]
            bag=db.execute("""SELECT m.person_name_normalized FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id
              JOIN documents d ON d.id=s.document_id JOIN document_versions v ON v.id=d.document_version_id
              WHERE v.sha256='acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0' LIMIT 1""").fetchone()
            difficult=db.execute("""SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id
              WHERE v.sha256='823d06203fa90d2bafefc250793dfafa7141a2b1e1dd3ed2e1ce8a506f9f9793'""").fetchone()
            with psycopg.connect(**kw,dbname='baseline',options='-c default_transaction_read_only=on') as original:
                oldmax=0 if restored_candidate else original.execute('SELECT max(id) FROM award_results').fetchone()[0]
            recovered=db.execute("""SELECT r.person_name_normalized,r.specialty_code FROM award_results r
              JOIN award_assignments a ON a.award_result_id=r.id JOIN documents d ON d.id=r.document_id
              JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256='e9647ae9d20db8f1d95e1eafcd712f6c365ba643e1447f9d58da5699db7f1c04'
              AND r.id>%s AND a.matched_offered_position_id IS NOT NULL LIMIT 1""",(oldmax,)).fetchone()
            recovered436=db.execute("""SELECT r.person_name_normalized,r.specialty_code FROM award_results r JOIN award_assignments a ON a.award_result_id=r.id
              JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id
              WHERE v.sha256='229556fed06a084a7388e10fbdf6dd65d0895910900bbaa4f58d75c8c15cb94a' AND r.id>%s LIMIT 1""",(oldmax,)).fetchone()
            before={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('public_alerts','notification_posts','push_notification_events')}
        env={'PATH':'/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8','ENABLE_NOTIFICATIONS':'0','FUNK_RECOVERY_READ_ONLY':'1',
             'RADAR_DOCENT_DB_URL':make_conninfo(host='/work/socket',port=55467,user=role,dbname=target_db,options='-c default_transaction_read_only=on')}
        if recovery_loopback:
            from psycopg.rows import dict_row
            from app.api.main import verify_recovery_database_role
            with psycopg.connect(env['RADAR_DOCENT_DB_URL'],row_factory=dict_row) as inspection:
                verify_recovery_database_role(inspection)
        with psycopg.connect(env['RADAR_DOCENT_DB_URL']) as restricted:
            try:restricted.execute('INSERT INTO public_alerts DEFAULT VALUES')
            except (psycopg.errors.ReadOnlySqlTransaction,psycopg.errors.InsufficientPrivilege):restricted.rollback()
            else:raise ValueError('readonly_role_write_succeeded')
            restricted.execute('SET default_transaction_read_only=off');restricted.commit()
            try:restricted.execute('INSERT INTO public_alerts DEFAULT VALUES')
            except psycopg.errors.InsufficientPrivilege:restricted.rollback()
            else:raise ValueError('readonly_role_grants_allow_write')
            if recovery_loopback:
                try:restricted.execute('CREATE TEMPORARY TABLE forbidden_recovery_write (id int)')
                except psycopg.errors.InsufficientPrivilege:restricted.rollback()
                else:raise ValueError('readonly_role_temp_table_allowed')
                try:restricted.execute("SELECT pg_catalog.pg_notify('recovery_test','')")
                except psycopg.errors.InsufficientPrivilege:restricted.rollback()
                else:raise ValueError('readonly_role_notify_allowed')
        port=8001 if recovery_loopback else 18769
        command=['/bin/bash','/project/scripts/start_recovery_loopback.sh'] if recovery_loopback else ['/project/.venv/bin/python','-B','-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log']
        server=subprocess.Popen(command,cwd='/project',env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=60) as client:
            for _ in range(150):
                try:
                    if client.get('/').status_code==200:break
                except httpx.HTTPError:pass
                if server.poll() is not None:raise ValueError('readonly_api_startup_failed')
                time.sleep(.2)
            else:raise ValueError('readonly_api_timeout')
            checks={}
            if production_restrictions:
                if client.get('/health').status_code!=200:raise ValueError('health_search_check_failed')
                search=client.get('/api/search/persons',params={'q':'garcia','limit':5}).json()
                if search.get('count',0)<1 or not search.get('items'):raise ValueError('garcia_search_empty')
                checks['garcia_search']={'returned':len(search['items'])}
                for family,path in (('offered','/api/offered-positions'),('difficult','/api/difficult-coverage/positions'),('non_docent','/api/non-docent/positions')):
                    params={'include_unavailable':'true','limit':5,'order_by':'id','order_dir':'asc'}
                    first=client.get(path,params=params);second=client.get(path,params={**params,'offset':5})
                    if first.status_code!=200 or second.status_code!=200:raise ValueError('historical_'+family+'_failed')
                    a,b=first.json(),second.json()
                    if a['total']<6 or not a['items'] or not b['items'] or set(x['id'] for x in a['items']) & set(x['id'] for x in b['items']):raise ValueError('historical_'+family+'_pagination')
                    if any(x.get('availability_status')=='available' and (family=='non_docent' or x.get('historical_status')!='accredited') for x in a['items']+b['items']):raise ValueError('historical_'+family+'_availability')
                    sample=a['items'][0]
                    if sample.get('position_code'):
                        filtered=client.get(path,params={**params,'position_code':sample['position_code']}).json()
                        if not filtered['items'] or any(x['position_code']!=sample['position_code'] for x in filtered['items']):raise ValueError('historical_'+family+'_filter')
                    checks['historical_'+family]={'total':a['total'],'page_1':len(a['items']),'page_2':len(b['items']),'distinct_pages':True}
            for key,path,params in (('search','/api/search/persons',{'q':name[:10]}),('profile','/api/persons/profile',{'normalized_name':name}),
                                    ('positions','/api/offered-positions',{'limit':1,'include_unavailable':'true'}),
                                    ('centers','/api/centers',{'limit':1}),('difficult','/api/difficult-coverage/positions',{'limit':1,'include_unavailable':'true'}),
                                    ('procedures','/api/historical-procedures',{})):
                r=client.get(path,params=params)
                if r.status_code!=200:raise ValueError('api_'+key+'_failed')
                data=r.json()
                if key=='profile':
                    observed=len(data.get('awards',[]))
                    if observed<1:raise ValueError('profile_missing_awards')
                else:
                    observed=len(data.get('items',[]))
                    if key!='procedures' and observed<1:raise ValueError('api_'+key+'_empty')
                checks[key]={'status':r.status_code,'items_returned':observed}
            if client.post('/api/push/subscribe',json={}).status_code!=405:raise ValueError('write_endpoint_not_blocked')
            non_docent=client.get('/api/non-docent/positions').json()
            historical_non_docent=client.get('/api/non-docent/positions',params={'include_unavailable':'true','limit':20}).json()
            if non_docent.get('total')!=0 or any(x.get('availability_status')=='available' for x in historical_non_docent.get('items',[])):
                raise ValueError('non_docent_unverified_availability_exposed')
            checks['non_docent_unverified_availability']={'current_total':0,'historical_sample':len(historical_non_docent.get('items',[]))}
            publications=client.get('/api/non-docent/publications',params={'limit':100}).json()
            if any(x.get('availability_status')=='available' or x.get('is_current') is True for x in publications.get('items',[])):
                raise ValueError('non_docent_unverified_publication_exposed')
            checks['non_docent_unverified_publications']={'historical_sample':len(publications.get('items',[]))}
            if recovered:
                search=client.get('/api/search/persons',params={'q':recovered[0],'limit':20}).json()
                profile=client.get('/api/persons/profile',params={'normalized_name':recovered[0]}).json()
                matching=[r for r in profile.get('awards',[]) if r.get('source_document_sha256')=='e9647ae9d20db8f1d95e1eafcd712f6c365ba643e1447f9d58da5699db7f1c04' and r.get('matched_assignments_count',0)>0 and r.get('specialty_code')==recovered[1]]
                if not search.get('items') or not matching:raise ValueError('recovered_teacher_profile_missing')
                checks['recovered_maestros_profile']={'search_results':len(search['items']),'matching_awards':len(matching),'specialty_and_association':'verified'}
            if recovered436:
                search=client.get('/api/search/persons',params={'q':recovered436[0],'limit':20}).json()
                profile=client.get('/api/persons/profile',params={'normalized_name':recovered436[0]}).json()
                matching=[r for r in profile.get('awards',[]) if r.get('source_document_sha256')=='229556fed06a084a7388e10fbdf6dd65d0895910900bbaa4f58d75c8c15cb94a' and r.get('specialty_code')==recovered436[1] and r.get('assignments_count',0)>0]
                if not search.get('items') or not matching:raise ValueError('recovered_436_profile_missing')
                checks['recovered_436_profile']={'search_results':len(search['items']),'matching_awards':len(matching),'specialty_and_award':'verified'}
            procedure_response=client.get('/api/historical-procedures').json()
            keys={p['procedure_key'] for p in procedure_response.get('items',[])}
            expected={'interino-2026-11-03','expertos-2026-11-05','interino-2026-11-19'}
            if not expected<=keys:raise ValueError('procedure_list_incomplete')
            for key in sorted(expected):
                response=client.get('/api/historical-procedures/'+key)
                if response.status_code!=200:raise ValueError('procedure_detail_failed')
                detail=response.json();p=detail['procedure'];docs=detail['documents']
                if p['publication_date'] is not None or p['publication_date_quality']!='unknown':raise ValueError('procedure_date_misrepresented')
                if len(docs)!=(3 if key.endswith('03') else 1 if 'expertos' in key else 4):raise ValueError('procedure_documents_count')
                if any(not d.get('source_document_sha256') for d in docs):raise ValueError('procedure_sha_missing')
                if any(d.get('historical_status')=='accredited' for d in docs):raise ValueError('unaccredited_procedure_promoted')
                if 'expertos' in key and (len(detail['unawarded_vacancies'])!=6 or docs[0].get('extracted_rows')!=6):raise ValueError('expert_vacancies_missing')
                if key.endswith('19') and sorted(d.get('quarantined_rows',0) for d in docs)!=[0,0,299,299]:raise ValueError('quarantine_19nov_mismatch')
                checks['procedure_'+key]={'documents':len(docs),'unawarded':len(detail['unawarded_vacancies']),'publication_date_quality':'unknown',
                    'document_sha_and_counts':[{'sha':d['source_document_sha256'],'extracted_rows':d.get('extracted_rows'),'quarantined_rows':d.get('quarantined_rows',0),'validation_status':d.get('validation_status'),'historical_status':d.get('historical_status')} for d in docs]}
            with psycopg.connect(**kw,dbname=target_db,options='-c default_transaction_read_only=on') as db:
                rows19=db.execute("""SELECT count(*) FROM award_results r JOIN historical_procedure_documents pd ON pd.document_id=r.document_id WHERE pd.procedure_key='interino-2026-11-19'""").fetchone()[0]
                assignments19=db.execute("""SELECT count(*),count(*) FILTER (WHERE a.matched_offered_position_id IS NOT NULL) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN historical_procedure_documents pd ON pd.document_id=r.document_id WHERE pd.procedure_key='interino-2026-11-19'""").fetchone()
                links03=db.execute("""SELECT count(*) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN historical_procedure_documents pd ON pd.document_id=r.document_id WHERE pd.procedure_key='interino-2026-11-03' AND a.matched_offered_position_id IS NOT NULL""").fetchone()[0]
                strict03=db.execute("""SELECT count(*) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                  JOIN historical_procedure_documents pd ON pd.document_id=r.document_id JOIN offered_positions p ON p.id=a.matched_offered_position_id
                  JOIN documents od ON od.id=p.document_id JOIN document_versions ov ON ov.id=od.document_version_id
                  WHERE pd.procedure_key='interino-2026-11-03' AND ov.sha256='33e69ded5cd9c7ef44032e749d1893ca7bd2717fca0ad99c864e7708992693b4'
                    AND a.position_code=p.position_code AND a.center_code=p.center_code
                    AND COALESCE(a.position_specialty_code,r.specialty_code)=p.specialty_code
                    AND (r.body_code IS NULL OR p.body_code IS NULL OR r.body_code=p.body_code)""").fetchone()[0]
                if (rows19,assignments19)!=(16687,(235,0)):raise ValueError('procedure_19_counts_mismatch')
                document_rows={sha:db.execute('''SELECT count(*) FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchone()[0] for sha in (
                  'e9647ae9d20db8f1d95e1eafcd712f6c365ba643e1447f9d58da5699db7f1c04',
                  '229556fed06a084a7388e10fbdf6dd65d0895910900bbaa4f58d75c8c15cb94a',
                  '847dcc1233a91b5694d1738f75ddd09e536d6764638ba87e89ab7f714510b348')}
                if sorted(document_rows.values())!=[6416,11024,17051] or strict03!=213:raise ValueError('recovered_document_count_or_links')
                if target_db=='baseline_404_674_trial' or restored_candidate:
                    pending=db.execute("SELECT count(*) FROM recovery_unawarded.quarantine WHERE reason='raw_block_conflict_unresolved'").fetchone()[0]
                    if pending!=1:raise ValueError('ordinal_99_quarantine_mismatch')
                    checks['ordinal_99_quarantine']=pending
                else:
                    pending=db.execute("SELECT count(*) FROM recovery_03nov.other_maestros_pending WHERE reason='shared_ordinal_person_conflict'").fetchone()[0]
                    if pending!=14:raise ValueError('shifted_ordinal_quarantine_mismatch')
                    checks['shifted_ordinal_quarantine']=pending
                checks['recovered_document_rows']=document_rows
                checks['procedure_19_results']=rows19;checks['procedure_19_assignments']=assignments19[0];checks['procedure_03_existing_links']=links03;checks['procedure_03_strict_existing_links']=strict03
            if bag and difficult:
                profile=client.get('/api/persons/profile',params={'normalized_name':bag[0]}).json()
                if not any(r.get('pending_source_rows')==16 and r.get('staff_kind')=='career_practice' for r in profile.get('bag_memberships',[])):raise ValueError('reconciled_bag_provenance_missing')
                params={'document_id':difficult[0],'include_unavailable':'true','limit':1}
                response=client.get('/api/difficult-coverage/positions',params=params).json()
                if response.get('total')!=52:raise ValueError('reconciled_difficult_count')
                if client.get('/api/difficult-coverage/positions',params={'document_id':difficult[0]}).json().get('total')!=0:raise ValueError('unknown_vigency_exposed')
                checks['reconciled_bag']={'pending_rows':16};checks['reconciled_difficult']={'historical_positions':52,'current_positions':0}
        if browser_check:
            from playwright.sync_api import sync_playwright
            if not recovered or not recovered436:raise ValueError('recovered_browser_sample_missing')
            browser_result={}
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path='/browser/chrome-headless-shell',headless=True,args=['--no-sandbox','--disable-background-networking','--disable-dev-shm-usage'])
                for language,viewport in (('es',{'width':1440,'height':900}),('va',{'width':390,'height':844})):
                    ctx=browser.new_context(viewport=viewport,service_workers='block');page=ctx.new_page()
                    ctx.route('**/*',lambda r:r.continue_() if r.request.url.startswith(f'http://127.0.0.1:{port}/') else r.abort())
                    base=f'http://127.0.0.1:{port}';prefix='/va' if language=='va' else ''
                    if page.goto(base+prefix+'/',wait_until='domcontentloaded').status!=200:raise ValueError('browser_home')
                    recovery_notice='Consulta històrica: les dades poden estar desactualitzades.' if language=='va' else 'Consulta histórica: los datos pueden estar desactualizados.'
                    page.get_by_text(recovery_notice,exact=False).first.wait_for(timeout=30000)
                    page.goto(base+prefix+'/consulta-persona',wait_until='domcontentloaded')
                    page.locator('#person-query').fill(recovered[0]);page.locator('#person-search-form').evaluate('(f)=>f.requestSubmit()')
                    page.locator('#person-search-results button[data-person-detail]').first.wait_for(timeout=30000)
                    notice='Vigencia y fecha de publicación sin acreditar' if language=='es' else 'Vigència i data de publicació sense acreditar'
                    for sample in (recovered,recovered436):
                        page.evaluate("n => sessionStorage.setItem('radar_docent_selected_person',JSON.stringify({normalizedName:n}))",sample[0])
                        page.goto(base+prefix+'/resultado-persona',wait_until='domcontentloaded')
                        page.get_by_text(notice,exact=False).first.wait_for(timeout=30000)
                    if not bag:raise ValueError('partial_bag_sample_missing')
                    page.evaluate("n => sessionStorage.setItem('radar_docent_selected_person',JSON.stringify({normalizedName:n}))",bag[0])
                    page.goto(base+prefix+'/resultado-persona',wait_until='domcontentloaded')
                    partial='Extracción parcial: quedan registros por revisar' if language=='es' else 'Extracció parcial: queden registres per revisar'
                    page.get_by_text(partial,exact=False).first.wait_for(timeout=30000)
                    for path in ('/plazas-ofertadas','/centros','/dificil-cobertura','/no-docente/plazas'):
                        response=page.goto(base+prefix+path,wait_until='domcontentloaded')
                        if not response or response.status!=200:raise ValueError('browser_navigation')
                        if production_restrictions and path in ('/plazas-ofertadas','/dificil-cobertura','/no-docente/plazas'):
                            body={'/plazas-ofertadas':'#op-table-body','/dificil-cobertura':'#dc-table-body','/no-docente/plazas':'#nd-positions-body'}[path]
                            try:page.wait_for_function("s => {const e=document.querySelector(s); return e && e.querySelector('tr td:not(.data-table__empty)');}",arg=body,timeout=60000)
                            except Exception as exc:raise ValueError('browser_rows_missing_'+body[1:].replace('-','_')) from exc
                            header=page.locator('.site-header').bounding_box();notice_box=page.locator('.recovery-notice').bounding_box()
                            if not header or not notice_box or notice_box['y']<header['y']+header['height']-1:raise ValueError('recovery_notice_header_overlap')
                    if page.evaluate('document.documentElement.scrollWidth>window.innerWidth+2'):raise ValueError('browser_overflow')
                    browser_result[language]={'viewport':viewport,'search':True,'recovered_profiles':2,'historical_notice':True,'partial_notice':True,'navigation':True,'overflow':False}
                    ctx.close()
                browser.close()
            checks['chromium']=browser_result
        if len(Path('/proc/net/route').read_text().splitlines())!=1:raise ValueError('unexpected_egress_route')
        producer_tokens=(b'run_pipeline.sh',b'run_parse_',b'run_sync_',b'run_send_notifications',b'run_publish_notifications',b'cron\x00',b'celery\x00')
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():continue
            try:cmdline=(process/'cmdline').read_bytes()
            except (FileNotFoundError,ProcessLookupError):continue
            if any(token in cmdline for token in producer_tokens):raise ValueError('producer_started_in_isolation')
        checks['isolation']={'external_ipv4_route':False,'known_producers_started':False,'restricted_role_write_denied_even_after_readonly_setting_disabled':True}
        with psycopg.connect(**kw,dbname=target_db,autocommit=True) as db:
            after={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in before}
            if before!=after:raise ValueError('notifications_changed')
        print(json.dumps({'status':'PASS','api_checks':checks,'write_endpoint':'405','notification_counts':after,'network':'private_namespace','personal_data_exported':False,'limitations':['One reused Maestros block differs and remains under review; prior association identity mapping remains incomplete.']}))
    finally:
        if server and server.poll() is None:server.terminate();server.wait(timeout=20)
        if created:
            try:
                with psycopg.connect(**kw,dbname=target_db,autocommit=True) as db:db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                with psycopg.connect(**kw,dbname='postgres',autocommit=True) as db:db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            except Exception:raise RuntimeError('temporary_role_cleanup_failed')
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def rollback_worker():
    import uuid,psycopg
    from psycopg import sql
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False;created=[]
    kw={'host':'/work/socket','port':55467,'user':'baseline_owner'}
    def snapshot(name):
        with psycopg.connect(**kw,dbname=name,options='-c default_transaction_read_only=on') as db:
            tables=[r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
            counts={t:db.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(t))).fetchone()[0] for t in tables}
            cols=db.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
            return counts,cols
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        checks={}
        for source in ('baseline','baseline17_trial_prebatch','baseline17_pre_seven'):
            expected=snapshot(source);name='restorecheck_'+uuid.uuid4().hex[:10]
            with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
                admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(name),sql.Identifier(source)));created.append(name)
            actual=snapshot(name)
            if actual!=expected:raise ValueError('restored_clone_mismatch')
            checks[source]={'public_tables':len(actual[0]),'counts_and_columns_match':True}
        print(json.dumps({'status':'PASS','rollback_reconciliation':'clone_from_prebatch_template','rollback_migrations':'clone_from_original_baseline_template','checks':checks,'trial_untouched':True,'limitation':'Restore by cloning in same cluster; does not test external disaster recovery.'}))
    finally:
        if started:
            try:
                with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
                    for name in created:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            finally:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def interruption_worker():
    import psycopg
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner',dbname='baseline17_fresh')
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,options='-c default_transaction_read_only=on') as db:
            before=db.execute("SELECT count(*) FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()[0]
            if before!=3:raise ValueError('unexpected_procedure_state')
        writer=psycopg.connect(**kw)
        try:
            if writer.execute("DELETE FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").rowcount!=3:raise ValueError('interruption_write_mismatch')
        finally:writer.close()
        with psycopg.connect(**kw,options='-c default_transaction_read_only=on') as db:
            after=db.execute("SELECT count(*) FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()[0]
            if after!=before:raise ValueError('interruption_rollback_failed')
        print(json.dumps({'status':'PASS','scenario':'client_disconnect_before_commit','rows_before':before,'rows_restored':after,'limitations':['Does not simulate a host crash or power loss.']}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':
    if '--interruption-worker' in sys.argv:interruption_worker()
    elif '--browser-fresh-worker' in sys.argv:api_worker('baseline17_fresh',browser_check=True)
    elif '--api-fresh-worker' in sys.argv:api_worker('baseline17_fresh')
    elif '--api-reproduction-worker' in sys.argv:api_worker('baseline17_reproduction')
    elif '--rollback-worker' in sys.argv:rollback_worker()
    elif '--api-worker' in sys.argv:api_worker()
    elif '--trial-worker' in sys.argv:trial_worker()
    elif '--worker' in sys.argv:worker()
    else:launch()
