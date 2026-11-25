"""Read-only aggregate comparison of version 701 in existing isolated DBs."""
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_final_capacity'
SHA='847dcc1233a91b5694d1738f75ddd09e536d6764638ba87e89ab7f714510b348'
OFFER='33e69ded5cd9c7ef44032e749d1893ca7bd2717fca0ad99c864e7708992693b4'


def worker():
    import psycopg
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner',options='-c default_transaction_read_only=on')
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        def fetch(dbname):
            with psycopg.connect(**kw,dbname=dbname) as db:
                if db.execute("SELECT current_setting('listen_addresses'),current_setting('data_directory'),current_setting('transaction_read_only')").fetchone()!=('','/work/data','on'):raise ValueError('database_identity')
                ds=db.execute('''SELECT d.id,d.doc_family FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(SHA,)).fetchall()
                if len(ds)!=1:raise ValueError('document_identity')
                did=ds[0][0]
                rows=db.execute('''SELECT order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,body_name
                    FROM award_results WHERE document_id=%s''',(did,)).fetchall()
                awards=db.execute('''SELECT count(*) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''',(did,)).fetchone()[0]
                strict=db.execute('''SELECT count(*) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                    JOIN offered_positions p ON p.id=a.matched_offered_position_id JOIN documents d ON d.id=p.document_id
                    JOIN document_versions v ON v.id=d.document_version_id
                    WHERE r.document_id=%s AND v.sha256=%s AND a.position_code=p.position_code AND a.center_code=p.center_code
                    AND COALESCE(a.position_specialty_code,r.specialty_code)=p.specialty_code
                    AND (r.body_code IS NULL OR p.body_code IS NULL OR r.body_code=p.body_code)''',(did,OFFER)).fetchone()[0]
                return Counter(rows),awards,strict,ds[0][1]
        source,sa,sm,sf=fetch('reconstruction_dr')
        original,oa,om,of=fetch('baseline')
        cumulative,ca,cm,cf=fetch('baseline_maestros_cumulative')
        missing=source-cumulative;extras=cumulative-source
        from psycopg import sql
        def column_names(db):
            return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='award_results'")}
        with psycopg.connect(**kw,dbname='reconstruction_dr') as src, psycopg.connect(**kw,dbname='baseline_maestros_cumulative') as dst:
            fields=sorted((column_names(src)&column_names(dst))-{'id','document_id','created_at'})
            def all_rows(db):
                query=sql.SQL('SELECT {} FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s').format(
                    sql.SQL(',').join(sql.SQL('r.{}').format(sql.Identifier(x)) for x in fields))
                return Counter(db.execute(query,(SHA,)).fetchall())
            full_src,full_dst=all_rows(src),all_rows(dst)
            def all_awards(db):
                return Counter(db.execute('''SELECT r.raw_block_text,a.assignment_kind,a.locality,a.center_code,a.center_name,
                    a.position_specialty_code,a.position_specialty_name,a.position_code,a.hours_text,a.hours_value,
                    a.petition_text,a.petition_number,a.request_type,a.raw_assignment_text
                    FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                    JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id
                    WHERE v.sha256=%s''',(SHA,)).fetchall())
            full_as,full_at=all_awards(src),all_awards(dst)
        print(json.dumps({'source_rows':sum(source.values()),'original_rows':sum(original.values()),'cumulative_rows':sum(cumulative.values()),
                          'missing_rows':sum(missing.values()),'missing_statuses':dict(Counter({status:sum(n for row,n in missing.items() if row[1]==status) for status in {row[1] for row in missing}})),
                          'destination_only_rows':sum(extras.values()),'source_awards':sa,'original_awards':oa,'cumulative_awards':ca,
                          'source_strict_links':sm,'original_strict_links':om,'cumulative_strict_links':cm,
                          'source_family':sf,'original_family':of,'cumulative_family':cf,
                          'all_common_result_fields_compared':fields,'full_field_missing':sum((full_src-full_dst).values()),
                          'full_field_target_only':sum((full_dst-full_src).values()),
                          'assignment_field_missing':sum((full_as-full_at).values()),'assignment_field_target_only':sum((full_at-full_as).values()),'read_only':True}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=1024**3)!=VOLUME:raise ValueError('encrypted_space_insufficient_for_read')
    receipt=json.loads((VOLUME/'maestros_700_436_receipt_reviewed.json').read_text());work=Path(receipt['cluster'])
    if receipt.get('status')!='RECONCILED_WITH_PENDING_REVIEW' or work.parent!=VOLUME or (work/'data/postmaster.pid').exists():raise ValueError('cluster_identity')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
        '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev',
        '--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
        '--ro-bind',str(ROOT),'/project','--bind',str(work),'/work','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
        '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/inspect_secondary_701_local.py','--worker']
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=600,check=True)
    result=json.loads(p.stdout.strip().splitlines()[-1]);out=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/secondary_701_fullfield_readonly_20261125.json'
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result))


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()
