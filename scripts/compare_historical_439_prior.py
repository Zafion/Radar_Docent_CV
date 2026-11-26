"""Compare the 439 trial with a read-only copy of the preceding candidate."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import re

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
CURRENT=VOLUME/('funk-404-674-independent-trial' if '--compare-404-674' in sys.argv else 'funk-439-independent-trial')
PRIOR_ORIGINAL=ROOT/'auditoria_local/recovery_final_capacity/funk-baseline-dq0rrzet'
PRIOR=VOLUME/'funk-439-prior-comparison'
SHA='2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4'


def worker():
    from collections import Counter
    import psycopg
    from psycopg.rows import dict_row
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=[]
    try:
        for directory in ('/work/data','/prior/data'):
            subprocess.run([pg,'-D',directory,'-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started.append(directory)
        options='-c default_transaction_read_only=on'
        with psycopg.connect(host='/work/socket',port=55467,user='baseline_owner',dbname='baseline_404_674_trial' if '--compare-404-674' in sys.argv else 'baseline_439_trial',row_factory=dict_row,options=options) as current, \
             psycopg.connect(host='/prior/socket',port=55468,user='baseline_owner',dbname='baseline17_fresh',row_factory=dict_row,options=options) as prior:
            for db,datadir in ((current,'/work/data'),(prior,'/prior/data')):
                x=db.execute("SELECT current_setting('listen_addresses') AS net,current_setting('data_directory') AS data,current_setting('server_version_num') AS version").fetchone()
                if x['net'] or x['data']!=datadir or not 160000<=int(x['version'])<170000:raise ValueError('cluster_identity_invalid')
            if '--keys-only' in sys.argv:
                def keys(db):return {x['key'] for x in db.execute('SELECT DISTINCT jsonb_object_keys(payload) AS key FROM recovery_quarantine.positions_19nov')}
                a=keys(current);b=keys(prior)
                print(json.dumps({'status':'QUARANTINE_KEYS_COMPARED','current_only':sorted(a-b),'prior_only':sorted(b-a)}));return
            if '--metadata-only' in sys.argv:
                names=('availability_checked_at','availability_reason','availability_status','closed_at','closed_by_document_id')
                def profile(db):
                    result={}
                    for field in names:
                        x=db.execute('''SELECT count(*) AS total,count(*) FILTER(WHERE NOT (payload ? %s)) AS missing,
                          count(*) FILTER(WHERE payload ? %s AND payload->%s='null'::jsonb) AS json_null,
                          count(*) FILTER(WHERE payload ? %s AND payload->%s<>'null'::jsonb) AS non_null
                          FROM recovery_quarantine.positions_19nov''',(field,field,field,field,field)).fetchone()
                        values=[]
                        if field in ('availability_status','availability_reason'):
                            values=db.execute('''SELECT payload->>%s AS value,count(*) AS n FROM recovery_quarantine.positions_19nov
                              WHERE payload ? %s AND payload->%s<>'null'::jsonb GROUP BY 1 ORDER BY 1''',(field,field,field)).fetchall()
                        result[field]={**x,'values':values}
                    return result
                try:report={'status':'QUARANTINE_METADATA_PROFILE','current':profile(current),'prior':profile(prior)}
                except psycopg.Error as exc:report={'status':'QUARANTINE_METADATA_ERROR','error_type':type(exc).__name__,'sqlstate':exc.sqlstate}
                print(json.dumps(report));return
            if '--notifications-only' in sys.argv:
                queries={'public_alerts':"SELECT (to_jsonb(t)-'id') AS value FROM public_alerts t",
                         'notification_posts':"SELECT jsonb_build_array(a.event_key,to_jsonb(t)-ARRAY['id','alert_id']) AS value FROM notification_posts t JOIN public_alerts a ON a.id=t.alert_id",
                         'push_notification_events':"SELECT (to_jsonb(t)-'id') AS value FROM push_notification_events t"}
                result={}
                for name,q in queries.items():
                    a=Counter(json.dumps(x['value'],sort_keys=True,default=str) for x in current.execute(q))
                    b=Counter(json.dumps(x['value'],sort_keys=True,default=str) for x in prior.execute(q))
                    result[name]={'identical':sum((a&b).values()),'current_only':sum((a-b).values()),'prior_only':sum((b-a).values())}
                print(json.dumps({'status':'NOTIFICATIONS_COMPARED','tables':result}));return
            def sha_set(db):return {r['sha256'] for r in db.execute('SELECT v.sha256 FROM documents d JOIN document_versions v ON v.id=d.document_version_id')}
            a=sha_set(current);b=sha_set(prior)
            def entries(db):
                return db.execute('''SELECT r.order_number,r.list_scope,r.body_code,r.specialty_code,r.status,r.raw_block_text,
                  r.person_display_name,r.person_name_normalized,to_jsonb(a)-'id'-'award_result_id'-'matched_offered_position_id' AS assignment,
                  a.matched_offered_position_id IS NOT NULL AS linked
                  FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id
                  LEFT JOIN award_assignments a ON a.award_result_id=r.id WHERE v.sha256=%s''',(SHA,)).fetchall()
            left=entries(current);right=entries(prior)
            def canonical(row):return json.dumps(row,sort_keys=True,default=str,ensure_ascii=True)
            l=Counter(map(canonical,left));r=Counter(map(canonical,right))
            def groups(db,kind):
                if kind=='results':query='''SELECT v.sha256,count(*) AS n FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256'''
                elif kind=='assignments':query='''SELECT v.sha256,count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256'''
                elif kind=='offers':query='''SELECT v.sha256,count(*) AS n FROM offered_positions o JOIN documents d ON d.id=o.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256'''
                elif kind=='bag':query='''SELECT v.sha256,count(*) AS n FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id JOIN documents d ON d.id=s.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256'''
                else:query='''SELECT v.sha256,count(*) AS n FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256'''
                return {x['sha256']:x['n'] for x in db.execute(query)}
            families={}
            for kind in ('results','assignments','offers','bag','difficult'):
                x=groups(current,kind);y=groups(prior,kind)
                diff=[{'sha256':k,'current':x.get(k,0),'prior':y.get(k,0)} for k in sorted(x.keys()|y.keys()) if x.get(k,0)!=y.get(k,0)]
                families[kind]={'current':sum(x.values()),'prior':sum(y.values()),'document_count_differences':diff}
            def proc_links(db):return {(x['procedure_key'],x['sha256'],x['document_role'],x['relationship_evidence']) for x in db.execute('''SELECT p.procedure_key,v.sha256,p.document_role,p.relationship_evidence FROM historical_procedure_documents p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id''')}
            pa=proc_links(current);pb=proc_links(prior)
            def strict(db):return db.execute('SELECT count(*) AS n FROM award_assignments WHERE matched_offered_position_id IS NOT NULL').fetchone()['n']
            queries={
              'documents':'''SELECT v.sha256 AS sha,(to_jsonb(d)-ARRAY['id','document_version_id','source_id','validation_parse_run_id','created_at','parsed_at'])::text AS payload FROM documents d JOIN document_versions v ON v.id=d.document_version_id''',
              'results':'''SELECT v.sha256 AS sha,(to_jsonb(r)-ARRAY['id','document_id','created_at'])::text AS payload FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id''',
              'assignments':'''SELECT v.sha256 AS sha,jsonb_build_array(to_jsonb(r)-ARRAY['id','document_id','created_at'],to_jsonb(a)-ARRAY['id','award_result_id','matched_offered_position_id','created_at'],to_jsonb(p)-ARRAY['id','document_id','created_at'])::text AS payload FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id LEFT JOIN offered_positions p ON p.id=a.matched_offered_position_id''',
              'offers':'''SELECT v.sha256 AS sha,(to_jsonb(o)-ARRAY['id','document_id','created_at'])::text AS payload FROM offered_positions o JOIN documents d ON d.id=o.document_id JOIN document_versions v ON v.id=d.document_version_id''',
              'bag':'''SELECT v.sha256 AS sha,jsonb_build_array(to_jsonb(s)-ARRAY['id','document_id','created_at'],to_jsonb(m)-ARRAY['id','snapshot_id','created_at'])::text AS payload FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id JOIN documents d ON d.id=s.document_id JOIN document_versions v ON v.id=d.document_version_id''',
              'difficult_positions':'''SELECT v.sha256 AS sha,(to_jsonb(p)-ARRAY['id','document_id','created_at','closed_by_document_id'])::text AS payload FROM difficult_coverage_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id''',
              'difficult_candidates':'''SELECT v.sha256 AS sha,jsonb_build_array(to_jsonb(p)-ARRAY['id','document_id','created_at','closed_by_document_id'],to_jsonb(c)-ARRAY['id','position_id','created_at'])::text AS payload FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id''',
              'nov19_quarantine':'''SELECT source_sha AS sha,jsonb_build_array(payload,stage_label)::text AS payload FROM recovery_quarantine.positions_19nov'''}
            from dataclasses import fields
            from app.services.offered_positions_parser import ParsedOfferedPosition
            names=[f.name for f in fields(ParsedOfferedPosition)]
            obj=','.join("'"+name+"',payload->'"+name+"'" for name in names)
            queries['nov19_quarantine_core']='SELECT source_sha AS sha,jsonb_build_array(jsonb_build_object('+obj+'),stage_label)::text AS payload FROM recovery_quarantine.positions_19nov'
            def compare_rows(name,query):
                query='SELECT sha,md5(payload) AS digest,payload FROM ('+query+') x ORDER BY sha COLLATE "C",md5(payload),payload COLLATE "C"'
                ca=current.cursor(name='current_'+name);cb=prior.cursor(name='prior_'+name);ca.execute(query);cb.execute(query)
                def stream(cursor):
                    while batch:=cursor.fetchmany(500):yield from batch
                aa=iter(stream(ca));bb=iter(stream(cb));x=next(aa,None);y=next(bb,None);same=only_a=only_b=0
                def key(item):return (item['sha'],item['digest'],item['payload'])
                while x is not None or y is not None:
                    if y is None or (x is not None and key(x)<key(y)):only_a+=1;x=next(aa,None)
                    elif x is None or key(y)<key(x):only_b+=1;y=next(bb,None)
                    else:same+=1;x=next(aa,None);y=next(bb,None)
                ca.close();cb.close();return {'identical':same,'current_only':only_a,'prior_only':only_b}
            field_comparison={k:compare_rows(k,q) for k,q in queries.items()}
            def quarantine_keys(db):return {x['key'] for x in db.execute('SELECT DISTINCT jsonb_object_keys(payload) AS key FROM recovery_quarantine.positions_19nov')}
            current_keys=quarantine_keys(current);prior_keys=quarantine_keys(prior)
            result={'status':'IDENTITY_COMPARISON_PARTIAL','documents':{'current':len(a),'prior':len(b),'common':len(a&b),'current_only':len(a-b),'prior_only':len(b-a)},
                    'version_439':{'current_rows':len(left),'prior_rows':len(right),'identical_record_and_assignment_multiset':sum((l&r).values()),'current_only':sum((l-r).values()),'prior_only':sum((r-l).values())},
                    'families':families,'procedure_relations':{'common':len(pa&pb),'current_only':len(pa-pb),'prior_only':len(pb-pa)},
                    'field_multisets':field_comparison,
                    'quarantine_payload_keys':{'current_only':sorted(current_keys-prior_keys),'prior_only':sorted(prior_keys-current_keys)},
                    'linked_awards':{'current':strict(current),'prior':strict(prior)},
                    'limits':['Field-multiset comparison excludes database-local IDs and creation timestamps; it does not establish an individual identity where identical records repeat.']}
            print(json.dumps(result))
    finally:
        for directory in reversed(started):subprocess.run([pg,'-D',directory,'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    minimum=8*1024**3 if '--compare-404-674' in sys.argv and '--verify-existing' in sys.argv else 16*1024**3
    if validate_volatile_storage(minimum_bytes=minimum)!=VOLUME:raise ValueError('insufficient_encrypted_space')
    existing='--verify-existing' in sys.argv
    if not CURRENT.is_dir() or PRIOR_ORIGINAL.is_symlink() or (CURRENT/'data/postmaster.pid').exists() or (PRIOR_ORIGINAL/'data/postmaster.pid').exists():raise ValueError('cluster_identity_or_destination')
    if existing:
        if not PRIOR.is_dir() or PRIOR.is_symlink() or (PRIOR/'data/postmaster.pid').exists():raise ValueError('prior_copy_invalid')
    else:
        if os.path.lexists(PRIOR):raise ValueError('prior_copy_exists')
        shutil.copytree(PRIOR_ORIGINAL,PRIOR,symlinks=True)
        with (PRIOR/'data/postgresql.conf').open('a') as f:f.write("\nport=55468\nunix_socket_directories='/prior/socket'\nlisten_addresses=''\n")
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(CURRENT/'passwd'),'/etc/passwd','--ro-bind',str(CURRENT/'group'),'/etc/group','--ro-bind',str(ROOT),'/project',
         '--bind',str(CURRENT),'/work','--bind',str(PRIOR),'/prior','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
         '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/compare_historical_439_prior.py','--worker']+(['--compare-404-674'] if '--compare-404-674' in sys.argv else [])+(['--keys-only'] if '--keys-only' in sys.argv else ['--metadata-only'] if '--metadata-only' in sys.argv else ['--notifications-only'] if '--notifications-only' in sys.argv else [])
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1800)
    if p.returncode:
        last=p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''
        name=last.split(':',1)[0].strip()
        reason=name if name and all(c.isascii() and (c.isalnum() or c in '._') for c in name) else 'isolated_comparison_failed'
        match=re.search(r'[A-Za-z_.]*(?:Error|Exception)',last)
        safe_last=match.group(0) if match else ''
        print(json.dumps({'status':'STOP','reason':reason,'diagnostic':safe_last,'prior_copy_preserved':str(PRIOR)}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);path=VOLUME/(('reconciliation_404_674_previous_fields_' if '--compare-404-674' in sys.argv else 'reconciliation_439_previous_fields_')+uuid.uuid4().hex+'.json' if existing else 'reconciliation_439_previous_comparison.json')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    if result['status'] in ('QUARANTINE_KEYS_COMPARED','QUARANTINE_METADATA_PROFILE','QUARANTINE_METADATA_ERROR','NOTIFICATIONS_COMPARED'):
        print(json.dumps({**result,'receipt':str(path)}));return 0
    print(json.dumps({'status':result['status'],'documents':result['documents'],'version_439':result['version_439'],'field_multisets':result['field_multisets'],'quarantine_payload_keys':result['quarantine_payload_keys'],'procedure_relations':result['procedure_relations'],'linked_awards':result['linked_awards'],'receipt':str(path)}));return 0

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())
