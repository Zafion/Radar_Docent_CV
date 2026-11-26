"""Reconcile document 439 only on a newly copied isolated encrypted cluster."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
CERT=VOLUME/'cumulative_certification_final_20261126.json'
ORIGINAL=VOLUME/'funk-baseline-v3jm9so3'
CLONE=VOLUME/'funk-439-independent-trial'
SHA='2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4'


def worker():
    from collections import Counter
    import psycopg
    from psycopg.rows import dict_row
    from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
    from app.services.historical_batch_reconcile import copy_awards,copy_fields
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner',row_factory=dict_row)
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        if '--verify' in sys.argv:
            from psycopg import sql
            options='-c default_transaction_read_only=on';comparisons={};unverified=[]
            with psycopg.connect(**kw,dbname='baseline_future_trial',options=options) as original,psycopg.connect(**kw,dbname='baseline_439_trial',options=options) as trial:
                tables=[r['table_name'] for r in original.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
                for table in tables:
                    names=[x['column_name'] for x in original.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,)).fetchall()]
                    keys=[x['attname'] for x in original.execute('''SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                      JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) ON true JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
                      WHERE n.nspname='public' AND c.relname=%s AND i.indisprimary ORDER BY k.ord''',(table,)).fetchall()]
                    if not keys:unverified.append(table);continue
                    key_sql=sql.SQL(',').join(map(sql.Identifier,keys));sort_sql=sql.SQL(',').join(sql.SQL('{}::text COLLATE "C"').format(sql.Identifier(k)) for k in keys)
                    query=sql.SQL('SELECT {},to_jsonb(t) AS row FROM {} t ORDER BY {}').format(key_sql,sql.Identifier(table),sort_sql)
                    left=original.cursor(name='orig_'+table);right=trial.cursor(name='trial_'+table);left.execute(query);right.execute(query)
                    def stream(c):
                        while batch:=c.fetchmany(1000):yield from batch
                    a=iter(stream(left));b=iter(stream(right));old=next(a,None);new=next(b,None);matched=missing=changed=0
                    while old is not None:
                        identity=lambda r:tuple(str(r[k]) for k in keys)
                        while new is not None and identity(new)<identity(old):new=next(b,None)
                        if new is None or identity(new)!=identity(old):missing+=1
                        elif {k:new['row'].get(k) for k in names}!=old['row']:changed+=1
                        else:matched+=1
                        old=next(a,None)
                    left.close();right.close();comparisons[table]={'matched':matched,'missing':missing,'changed':changed}
                quarantines={}
                for schema,table in (('recovery_quarantine','positions_19nov'),('recovery_unawarded','quarantine')):
                    q=sql.SQL('SELECT count(*) AS n,md5(coalesce(string_agg(md5(to_jsonb(t)::text),\'\' ORDER BY to_jsonb(t)::text),\'\')) AS digest FROM {}.{} t').format(sql.Identifier(schema),sql.Identifier(table))
                    x=original.execute(q).fetchone();y=trial.execute(q).fetchone()
                    quarantines[schema+'.'+table]={'count':x['n'],'content_equal':x==y}
                notes=('public_alerts','notification_posts','push_notification_events')
                if unverified or any(v['missing'] or v['changed'] for v in comparisons.values()) or not all(v['content_equal'] for v in quarantines.values()):raise ValueError('certified_baseline_not_preserved')
                print(json.dumps({'status':'PRESERVATION_PASS','tables':len(tables),'comparisons':comparisons,'quarantines':quarantines,
                    'notification_contents_preserved':all(comparisons[t]['missing']==comparisons[t]['changed']==0 for t in notes)}))
            return
        registering='--register-evidence' in sys.argv
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            ident=admin.execute("SELECT current_setting('listen_addresses') AS net,current_setting('data_directory') AS data,current_setting('server_version_num') AS version").fetchone()
            if ident['net'] or ident['data']!='/work/data' or not 160000<=int(ident['version'])<170000:raise ValueError('cluster_identity')
            if not registering:
                if admin.execute("SELECT 1 FROM pg_database WHERE datname IN ('baseline_439_trial','baseline_439_rollback')").fetchone():raise ValueError('destination_database_exists')
                admin.execute('CREATE DATABASE baseline_439_trial TEMPLATE baseline_future_trial')
                admin.execute('CREATE DATABASE baseline_439_rollback TEMPLATE baseline_future_trial')
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
        entry=next(x for x in plan['entries'] if x['version_id']==439)
        pdf=ROOT/Path(entry['local_sha_verified_paths'][0]).relative_to('/home/zafion/Proyects/Radar_Docent_CV')
        if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=SHA or entry['sha256']!=SHA:raise ValueError('pdf_sha_mismatch')
        parsed=FinalAwardListingMaestrosParserService()._parse_document({'file_path':str(pdf),'list_scope':'maestros'})
        cols=('order_number','status','raw_block_text','person_display_name','person_name_normalized','specialty_code')
        def setup(db):
            db.execute('''CREATE SCHEMA IF NOT EXISTS recovery_batch;
              CREATE TABLE IF NOT EXISTS recovery_batch.row_map(kind text NOT NULL,source_id bigint NOT NULL,target_id bigint NOT NULL,PRIMARY KEY(kind,source_id),UNIQUE(kind,target_id));
              CREATE TABLE IF NOT EXISTS recovery_batch.quarantined_rows(kind text NOT NULL,source_id bigint NOT NULL,source_sha text NOT NULL,reason text NOT NULL,PRIMARY KEY(kind,source_id));
              CREATE TABLE IF NOT EXISTS recovery_batch.documents(source_sha text PRIMARY KEY,target_document_id bigint NOT NULL REFERENCES documents(id),family text NOT NULL,
                source_parser text NOT NULL,source_parser_version text NOT NULL,source_rows integer NOT NULL,quality text NOT NULL,official_url text NOT NULL,manifest_sha text NOT NULL,source_total integer);''')
        def marker(src,dst,did,source_rows,pending):
            snapshot=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/inventario_hetzner_snapshot_v2.json'
            raw=snapshot.read_bytes()
            if hashlib.sha256(raw).hexdigest()!='95263b363a91c6e6083ef8ae95cd3bebb8fd36773a53cad6a416785ddd2126c5':raise ValueError('inventory_hash_mismatch')
            snap=next((x for x in json.loads(raw)['versions'] if x['version_id']==439),None)
            if not snap or snap['sha256']!=SHA:raise ValueError('inventory_document_mismatch')
            urls={a['canonical_url'] for a in snap['assets'] if a.get('canonical_url','').startswith('https://ceice.gva.es/documents/')}
            if len(urls)!=1:raise ValueError('official_url_ambiguous')
            parser=src.execute('''SELECT p.parser_key,p.parser_version,p.rows_extracted FROM document_parse_runs p
              JOIN document_versions v ON v.id=p.document_version_id WHERE v.sha256=%s AND p.status='success' ORDER BY p.finished_at DESC NULLS LAST,p.id DESC LIMIT 1''',(SHA,)).fetchone()
            if not parser or parser['parser_key']!='final_award_listing_maestros_parser' or parser['parser_version']!='0.4.0' or parser['rows_extracted']!=source_rows:raise ValueError('parser_evidence_mismatch')
            manifest=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/lote_reconciliacion_02.json'
            manifest_sha=hashlib.sha256(manifest.read_bytes()).hexdigest()
            if manifest_sha!='39e69389ad730a62a1de40ca418089d355afcdb581506f3f2ef409e5054fddb8':raise ValueError('manifest_identity_mismatch')
            values=(SHA,did,'award_results',parser['parser_key'],parser['parser_version'],source_rows-pending,'partial_validated_with_limitations',next(iter(urls)),manifest_sha,source_rows)
            dst.execute('INSERT INTO recovery_batch.documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',values)
            existing=dst.execute('SELECT * FROM recovery_batch.documents WHERE source_sha=%s',(SHA,)).fetchone()
            if tuple(existing.values())!=values:raise ValueError('document_marker_conflict')
        def counts(db):
            return {t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('documents','award_results','award_assignments','offered_positions','public_alerts','notification_posts','push_notification_events')}
        def reconcile(src,dst,inject=False):
            with src.transaction(),dst.transaction():
                src.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                dst.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                docs=lambda db:db.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHA,)).fetchall()
                a=docs(src);b=docs(dst)
                if len(a)!=1 or len(b)!=1:raise ValueError('document_identity_ambiguous')
                sid=a[0]['id'];did=b[0]['id']
                rows=src.execute('SELECT * FROM award_results WHERE document_id=%s',(sid,)).fetchall()
                if not rows or Counter(tuple(getattr(x,k) for k in cols) for x in parsed)!=Counter(tuple(x[k] for k in cols) for x in rows):raise ValueError('source_pdf_result_mismatch')
                from app.services.historical_secondary_reconcile import AWARD_FIELDS
                awards=src.execute('SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s',(sid,)).fetchall()
                if Counter((r.raw_block_text,tuple(getattr(r.assignment,k) for k in AWARD_FIELDS)) for r in parsed if r.assignment)!=Counter((next(x['raw_block_text'] for x in rows if x['id']==a['award_result_id']),tuple(a[k] for k in AWARD_FIELDS)) for a in awards):raise ValueError('source_pdf_assignment_mismatch')
                setup(dst)
                prior=dst.execute('SELECT count(*) AS n FROM award_results WHERE document_id=%s',(did,)).fetchone()['n']
                if prior:
                    mappings=dst.execute("SELECT m.source_id,m.target_id FROM recovery_batch.row_map m JOIN award_results r ON r.id=m.target_id WHERE m.kind='award_results' AND r.document_id=%s",(did,)).fetchall()
                    mapped=len(mappings)
                    pending=dst.execute("SELECT count(*) AS n FROM recovery_batch.quarantined_rows WHERE source_sha=%s AND kind='award_results'",(SHA,)).fetchone()['n']
                    if mapped!=prior or mapped+pending!=len(rows):raise ValueError('existing_rows_require_comparison')
                    fields=[k for k in copy_fields(src,dst,'award_results') if k!='document_id']
                    source_by_id={x['id']:x for x in rows}
                    award_by_result={}
                    for award in awards:award_by_result.setdefault(award['award_result_id'],[]).append(award)
                    for m in mappings:
                        source_row=source_by_id.get(m['source_id'])
                        target_row=dst.execute('SELECT * FROM award_results WHERE id=%s',(m['target_id'],)).fetchone()
                        if not source_row or not target_row or any(source_row[k]!=target_row[k] for k in fields):raise ValueError('mapped_result_field_conflict')
                        target_awards=dst.execute('SELECT * FROM award_assignments WHERE award_result_id=%s',(m['target_id'],)).fetchall()
                        if Counter(tuple(a[k] for k in AWARD_FIELDS) for a in award_by_result.get(m['source_id'],[]))!=Counter(tuple(a[k] for k in AWARD_FIELDS) for a in target_awards):raise ValueError('mapped_assignment_field_conflict')
                        if any(a.get('matched_offered_position_id') is not None for a in target_awards):raise ValueError('unaccredited_offer_link')
                    marker(src,dst,did,len(rows),pending)
                    return {'source_rows':len(rows),'source_assignments':len(awards),'reused':mapped,'quarantined':pending,'inserted':0,'assignments_inserted':0}
                added=copy_awards(src,dst,sid,did,SHA)
                if added['award_results']+added['ambiguous_rows']!=len(rows):raise ValueError('source_rows_unaccounted')
                marker(src,dst,did,len(rows),added['ambiguous_rows'])
                if inject:raise RuntimeError('injected_rollback_439')
                return {'source_rows':len(rows),'source_assignments':len(awards),'reused':0,'quarantined':added['ambiguous_rows'],'inserted':added['award_results'],'assignments_inserted':added['award_assignments']}
        if registering:
            with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_439_trial') as dst:
                first=reconcile(src,dst);second=reconcile(src,dst)
                if first['inserted'] or second['inserted']:raise ValueError('marker_replay_inserted_results')
                print(json.dumps({'status':'DOCUMENT_EVIDENCE_REGISTERED','first':first,'second':second}))
            return
        with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_439_trial') as dst,psycopg.connect(**kw,dbname='baseline_439_rollback') as rollback:
            before=counts(dst);dst.commit();r_before=counts(rollback);rollback.commit()
            try:reconcile(src,rollback,inject=True)
            except RuntimeError as exc:
                if str(exc)!='injected_rollback_439':raise
            else:raise ValueError('rollback_not_triggered')
            if counts(rollback)!=r_before:raise ValueError('rollback_counts_changed')
            rollback.commit();first=reconcile(src,dst);second=reconcile(src,dst);after=counts(dst)
            if second['inserted'] or second['assignments_inserted']:raise ValueError('replay_not_idempotent')
            if any(after[k]!=before[k] for k in ('documents','offered_positions','public_alerts','notification_posts','push_notification_events')):raise ValueError('unrelated_counts_changed')
            invalid=dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
            if invalid:raise ValueError('constraints_not_validated')
            print(json.dumps({'status':'PARTIAL_UNTIL_FIELD_AND_BASELINE_COMPARISON','sha256':SHA,'first':first,'second':second,'before':before,'after':after,'rollback':'PASS','unvalidated_constraints':invalid}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=12*1024**3)!=VOLUME:raise ValueError('encrypted_capacity_insufficient')
    if json.loads(CERT.read_text()).get('status')!='complete':raise ValueError('source_certification_missing')
    verifying='--verify-existing' in sys.argv
    registering='--register-existing' in sys.argv
    if ORIGINAL.is_symlink() or (ORIGINAL/'data/postmaster.pid').exists():raise ValueError('source_running_or_invalid')
    if verifying or registering:
        if not CLONE.is_dir() or CLONE.is_symlink() or (CLONE/'data/postmaster.pid').exists():raise ValueError('trial_cluster_invalid')
    else:
        if os.path.lexists(CLONE):raise ValueError('target_exists')
        shutil.copytree(ORIGINAL,CLONE,symlinks=True)
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(CLONE/'passwd'),'/etc/passwd','--ro-bind',str(CLONE/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(CLONE),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),'--gid',str(os.getgid()),'--',
         '/project/.venv/bin/python','-B','/project/scripts/rehearse_historical_439.py','--worker']+(['--verify'] if verifying else ['--register-evidence'] if registering else [])
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2400)
    if p.returncode:
        last=p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''
        reason=last[len('ValueError: '):] if last.startswith('ValueError: ') else 'isolated_worker_failed'
        if not reason or len(reason)>80 or not all(c.isascii() and (c.isalnum() or c=='_') for c in reason):reason='isolated_worker_failed'
        print(json.dumps({'status':'STOP','condition':reason,'clone_preserved':str(CLONE)}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result['clone']=str(CLONE)
    path=VOLUME/('reconciliation_439_preservation.json' if verifying else 'reconciliation_439_document_evidence.json' if registering else 'reconciliation_439_trial.json');fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())
