"""Reconcile 700, 436 and 701 in a new isolated encrypted database clone."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_final_capacity'
OFFER_SHA='33e69ded5cd9c7ef44032e749d1893ca7bd2717fca0ad99c864e7708992693b4'


def worker():
    import asyncio,uuid
    import psycopg
    import httpx
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.conninfo import make_conninfo
    from app.services.historical_unawarded_reconcile import reconcile_unawarded
    from app.services.historical_secondary_reconcile import reconcile_secondary
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner')
    progress={'status':'IN_PROGRESS','steps':[]}
    def save():
        p=Path('/work/secondary_cumulative_progress.tmp')
        with p.open('w') as f:json.dump(progress,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(p,'/work/secondary_cumulative_progress.json')
    def counts(db):
        names=('documents','award_results','award_assignments','offered_positions','public_alerts','notification_posts','push_notification_events')
        return {name:db.execute('SELECT count(*) AS n FROM '+name).fetchone()['n'] for name in names}
    def notices(db):
        result={}
        for name in ('public_alerts','notification_posts','push_notification_events'):
            result[name]=db.execute("SELECT count(*) AS n,md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id),'')) AS digest FROM "+name+' t').fetchone()
        return result
    def api_check():
        role='secondary_read_'+uuid.uuid4().hex[:10]
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
        try:
            with psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',autocommit=True) as grant:
                grant.execute(sql.SQL('GRANT CONNECT ON DATABASE baseline_700_full_trial_v2 TO {}').format(sql.Identifier(role)))
                grant.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                grant.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
            os.environ.update(FUNK_RECOVERY_READ_ONLY='1',ENABLE_NOTIFICATIONS='0',
                RADAR_DOCENT_DB_URL=make_conninfo(host='/work/socket',port=55467,user=role,dbname='baseline_700_full_trial_v2',options='-c default_transaction_read_only=on'))
            with psycopg.connect(os.environ['RADAR_DOCENT_DB_URL']) as restricted:
                try:restricted.execute('INSERT INTO public_alerts DEFAULT VALUES')
                except (psycopg.errors.ReadOnlySqlTransaction,psycopg.errors.InsufficientPrivilege):restricted.rollback()
                else:raise ValueError('readonly_role_write_succeeded')
            from app.api.main import app
            from app.storage.db import close_pool
            async def check():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    response=await client.get('/api/historical-procedures/interino-2026-11-03')
                    if response.status_code!=200:raise ValueError('procedure_api_failed')
                    payload=response.json();docs={d['source_document_sha256']:d for d in payload['documents']}
                    expected={entries[700]['sha256']:6416,entries[701]['sha256']:11024,entries[702]['sha256']:210}
                    if set(docs)!=set(expected) or any(docs[k].get('extracted_rows')!=n for k,n in expected.items()):raise ValueError('procedure_api_document_count')
                    if payload['procedure']['publication_date'] is not None or payload['procedure']['publication_date_quality']!='unknown':raise ValueError('procedure_publication_date')
                    return {'status':'PASS','documents':len(docs),'secondary_rows':docs[entries[701]['sha256']]['extracted_rows'],
                            'publication_date_quality':'unknown','readonly_write_denied':True}
            try:return asyncio.run(check())
            finally:close_pool()
        finally:
            with psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',autocommit=True) as cleanup:
                cleanup.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        if '--verify-only' in sys.argv:
            plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
            entries={r['version_id']:r for r in plan['entries'] if r['version_id'] in (700,701,702,436)}
            if set(entries)!={700,701,702,436}:raise ValueError('document_evidence_missing')
            with psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',row_factory=dict_row,options='-c default_transaction_read_only=on') as observed:
                q=observed.execute('''SELECT document_sha,reason,count(*) AS n FROM recovery_unawarded.quarantine
                    WHERE document_sha=ANY(%s) GROUP BY document_sha,reason ORDER BY document_sha,reason''',
                    ([entries[700]['sha256'],entries[436]['sha256']],)).fetchall()
                sq=observed.execute('''SELECT reason,count(*) AS n FROM recovery_secondary.quarantine
                    WHERE document_sha=%s GROUP BY reason ORDER BY reason''',(entries[701]['sha256'],)).fetchall()
                docs=observed.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()['n']
            print(json.dumps({'status':'VERIFY_PASS','maestros_quarantine':q,'secondary_quarantine':sq,'procedure_documents':docs}));return
        if '--api-only' in sys.argv:
            plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
            entries={r['version_id']:r for r in plan['entries'] if r['version_id'] in (700,701,702)}
            if set(entries)!={700,701,702}:raise ValueError('document_evidence_missing')
            with psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',row_factory=dict_row,options='-c default_transaction_read_only=on') as observed:
                before_api=notices(observed)
            api=api_check()
            with psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',row_factory=dict_row,options='-c default_transaction_read_only=on') as observed:
                if notices(observed)!=before_api:raise ValueError('notifications_changed_after_api')
            print(json.dumps({'status':'API_PASS','api':api,'notifications_unchanged':True}));return
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname='baseline_700_full_trial_v2'").fetchone():raise ValueError('trial_database_exists')
            admin.execute('CREATE DATABASE baseline_700_full_trial_v2 TEMPLATE baseline')
        v=os.statvfs('/work')
        if v.f_bavail*v.f_frsize<6*1024**3:raise ValueError('post_clone_space_reserve_insufficient')
        save()
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
        entries={r['version_id']:r for r in plan['entries'] if r['version_id'] in (700,701,702,436)}
        if set(entries)!={700,701,702,436} or entries[702]['sha256']!=OFFER_SHA:raise ValueError('document_evidence_missing')
        old_root=Path('/home/zafion/Proyects/Radar_Docent_CV')
        paths={k:ROOT/Path(v['local_sha_verified_paths'][0]).relative_to(old_root) for k,v in entries.items()}
        if paths[702].is_symlink() or hashlib.sha256(paths[702].read_bytes()).hexdigest()!=OFFER_SHA:raise ValueError('offer_pdf_sha_mismatch')
        with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row) as src,psycopg.connect(**kw,dbname='baseline_700_full_trial_v2',row_factory=dict_row) as dst:
            links=src.execute('''SELECT count(*) AS n FROM historical_procedure_documents pd JOIN documents d ON d.id=pd.document_id
               JOIN document_versions v ON v.id=d.document_version_id WHERE pd.procedure_key='interino-2026-11-03' AND v.sha256=ANY(%s)''',
               ([entries[k]['sha256'] for k in (700,701,702)],)).fetchone()['n'];src.rollback()
            if links!=3:raise ValueError('procedure_document_relationship_missing')
            progress['before']=counts(dst);notices_before=notices(dst);dst.commit();save()
            for version in (700,436):
                args=dict(document_sha=entries[version]['sha256'],pdf_path=paths[version],family='maestros',include_awards=True,
                          accredited_offer_sha=OFFER_SHA if version==700 else None)
                first=reconcile_unawarded(src,dst,**args);second=reconcile_unawarded(src,dst,**args)
                if any(second.get(x,0) for x in ('inserted','awards_inserted','associations_inserted')):raise ValueError('maestros_replay_not_idempotent')
                progress['steps'].append({'version':version,'first':first,'second':second});save()
            args=dict(document_sha=entries[701]['sha256'],pdf_path=paths[701])
            before_secondary=counts(dst)
            dst.execute("CREATE FUNCTION fail_secondary_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected secondary rollback'; END $$")
            dst.execute('CREATE TRIGGER fail_secondary_insert BEFORE INSERT ON award_results FOR EACH ROW EXECUTE FUNCTION fail_secondary_insert()');dst.commit()
            try:reconcile_secondary(src,dst,**args)
            except psycopg.errors.RaiseException as exc:
                if exc.diag.message_primary!='injected secondary rollback':raise
            else:raise ValueError('rollback_injection_not_triggered')
            if counts(dst)!=before_secondary:raise ValueError('rollback_counts_changed')
            dst.execute('DROP TRIGGER fail_secondary_insert ON award_results');dst.execute('DROP FUNCTION fail_secondary_insert()');dst.commit()
            progress['rollback_secondary']='PASS';save()
            first=reconcile_secondary(src,dst,**args);second=reconcile_secondary(src,dst,**args)
            if any(second.get(x,0) for x in ('inserted','awards_inserted')):raise ValueError('secondary_replay_not_idempotent')
            progress['steps'].append({'version':701,'first':first,'second':second});save()
            # Add only source-backed links to already SHA-identified documents.
            proc=src.execute("SELECT * FROM historical_procedures WHERE procedure_key='interino-2026-11-03'").fetchone()
            if not proc or proc['publication_date'] is not None or proc['publication_date_quality']!='unknown':raise ValueError('procedure_evidence_invalid')
            dst.execute((ROOT/'app/storage/migrations/20261122_historical_procedures.sql').read_text())
            prior=dst.execute("SELECT * FROM historical_procedures WHERE procedure_key='interino-2026-11-03'").fetchone()
            pf=('procedure_key','procedure_kind','procedure_date','date_evidence','publication_date','publication_date_quality')
            if prior and any(prior[k]!=proc[k] for k in pf):raise ValueError('procedure_conflict')
            if not prior:dst.execute('INSERT INTO historical_procedures VALUES(%s,%s,%s,%s,%s,%s)',tuple(proc[k] for k in pf))
            for version in (700,701,702):
                sha=entries[version]['sha256']
                source_doc=src.execute('''SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchall()
                target_doc=dst.execute('''SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchall()
                if len(source_doc)!=1 or len(target_doc)!=1:raise ValueError('procedure_document_identity')
                link=src.execute("SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03' AND document_id=%s",(source_doc[0]['id'],)).fetchone()
                if not link:raise ValueError('procedure_link_missing')
                old=dst.execute("SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03' AND document_id=%s",(target_doc[0]['id'],)).fetchone()
                if old and old!=link:raise ValueError('procedure_link_conflict')
                if not old:dst.execute('INSERT INTO historical_procedure_documents VALUES(%s,%s,%s,%s)',('interino-2026-11-03',target_doc[0]['id'],link['document_role'],link['relationship_evidence']))
            dst.commit();src.rollback()
            strict=dst.execute('''SELECT count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
               JOIN documents rd ON rd.id=r.document_id JOIN document_versions rv ON rv.id=rd.document_version_id
               JOIN offered_positions p ON p.id=a.matched_offered_position_id JOIN documents pd ON pd.id=p.document_id
               JOIN document_versions pv ON pv.id=pd.document_version_id WHERE rv.sha256=ANY(%s) AND pv.sha256=%s
               AND a.position_code=p.position_code AND a.center_code=p.center_code
               AND COALESCE(a.position_specialty_code,r.specialty_code)=p.specialty_code
               AND (r.body_code IS NULL OR p.body_code IS NULL OR r.body_code=p.body_code)''',
               ([entries[700]['sha256'],entries[701]['sha256']],OFFER_SHA)).fetchone()['n']
            progress['api']=api_check();save()
            after=counts(dst);notices_after=notices(dst)
            fk=dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
            if notices_before!=notices_after or fk:raise ValueError('notifications_or_constraints_changed')
            progress.update(status='RECONCILED_WITH_PENDING_REVIEW',after=after,strict_links=strict,
                            procedure_documents=3,notifications_unchanged=True,unvalidated_constraints=fk);save()
        print(json.dumps(progress))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    # One ~0.75 GiB DB clone, <=1 GiB WAL and 0.5 GiB temporary allowance,
    # preserving at least 4.5 GiB working reserve; check again after cloning.
    minimum=1024**3 if '--api-only' in sys.argv or '--verify-only' in sys.argv else 7*1024**3
    if validate_volatile_storage(minimum_bytes=minimum)!=VOLUME:raise ValueError('encrypted_space_insufficient')
    receipt=json.loads((VOLUME/'maestros_700_436_receipt_reviewed.json').read_text());work=Path(receipt['cluster'])
    if receipt.get('status')!='RECONCILED_WITH_PENDING_REVIEW' or work.parent!=VOLUME or (work/'data/postmaster.pid').exists():raise ValueError('cluster_identity')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
       '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev',
       '--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
       '--ro-bind',str(ROOT),'/project','--bind',str(work),'/work','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
       '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/rehearse_secondary_03nov.py','--worker']
    if '--api-only' in sys.argv:cmd.append('--api-only')
    if '--verify-only' in sys.argv:cmd.append('--verify-only')
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2400)
    if p.returncode:
        lines=p.stderr.strip().splitlines();last=lines[-1] if lines else ''
        category=last if last.startswith(('ValueError: ','RuntimeError: ')) and len(last)<160 else 'isolated_worker_failed'
        print(json.dumps({'status':'STOP','condition':category,'progress_path':str(work/'secondary_cumulative_progress.json'),'automatic_retry':False}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result['cluster']=str(work)
    receipt_name='secondary_03nov_verify_receipt.json' if '--verify-only' in sys.argv else 'secondary_03nov_api_receipt.json' if '--api-only' in sys.argv else 'secondary_03nov_receipt.json'
    fd=os.open(VOLUME/receipt_name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()
