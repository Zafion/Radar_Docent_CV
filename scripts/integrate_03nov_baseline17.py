"""Register evidenced procedure documents, leaving non-keyed 03-Nov rows untouched."""
import hashlib,json
import psycopg
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import ROOT,SHAS,counts

def run(source,socket,*,target_db='baseline17_trial'):
    if target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh'):raise ValueError('target_identity')
    raw=(ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/inventario_hetzner_snapshot_v2.json').read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest!='95263b363a91c6e6083ef8ae95cd3bebb8fd36773a53cad6a416785ddd2126c5':raise ValueError('inventory_identity_mismatch')
    evidence={}
    for row in json.loads(raw)['versions']:
        ver=row.get('version_id')
        if ver not in SHAS:continue
        assets=[a for a in row['assets'] if a.get('source_key')=='rrhh_main' and a.get('source_url')=='https://ceice.gva.es/es/web/rrhh-educacion' and a.get('canonical_url','').startswith('https://ceice.gva.es/documents/')]
        if row.get('sha256')!=SHAS[ver] or len(assets)!=1:raise ValueError('inventory_source_ambiguous')
        evidence[ver]=assets[0]['canonical_url']
    if set(evidence)!=set(SHAS):raise ValueError('inventory_evidence_missing')
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        backup={'baseline17_fresh':'baseline17_fresh_pre03meta','baseline17_reproduction':'baseline17_reproduction_pre03meta'}.get(target_db,'baseline17_trial_pre03meta')
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(backup,)).fetchone():
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(backup),sql.Identifier(target_db)))
    source.row_factory=dict_row
    source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        proc=source.execute("SELECT * FROM historical_procedures WHERE procedure_key='interino-2026-11-03'").fetchone()
        if not proc or proc['publication_date'] is not None or proc['publication_date_quality']!='unknown':raise ValueError('source_procedure_identity')
        with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
            identity=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
            if identity!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
            before=counts(dst);dst.commit()
            with dst.transaction():
                dst.execute('''CREATE SCHEMA IF NOT EXISTS recovery_03nov;
                  CREATE TABLE IF NOT EXISTS recovery_03nov.document_map(source_sha text PRIMARY KEY,target_document_id bigint NOT NULL UNIQUE REFERENCES documents(id));
                  CREATE TABLE IF NOT EXISTS recovery_03nov.document_evidence(source_sha text PRIMARY KEY REFERENCES recovery_03nov.document_map(source_sha),canonical_url text NOT NULL,source_key text NOT NULL,inventory_sha256 text NOT NULL,publication_date_quality text NOT NULL CHECK(publication_date_quality='unknown'));''')
                values=tuple(proc[x] for x in ('procedure_key','procedure_kind','procedure_date','date_evidence','publication_date','publication_date_quality'))
                dst.execute('INSERT INTO historical_procedures(procedure_key,procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',values)
                actual=dst.execute("SELECT procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality FROM historical_procedures WHERE procedure_key='interino-2026-11-03'").fetchone()
                if tuple(actual.values())!=values[1:]:raise ValueError('target_procedure_conflict')
                docs=[]
                for ver,sha in SHAS.items():
                    target=dst.execute('SELECT d.id,d.doc_family FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchall()
                    src=source.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
                    if len(target)!=1 or not src:raise ValueError('document_identity_ambiguous')
                    did=target[0]['id']
                    link=source.execute("SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03' AND document_id=%s",(src['id'],)).fetchone()
                    if not link:raise ValueError('procedure_link_missing')
                    dst.execute('INSERT INTO recovery_03nov.document_map VALUES(%s,%s) ON CONFLICT DO NOTHING',(sha,did))
                    m=dst.execute('SELECT target_document_id FROM recovery_03nov.document_map WHERE source_sha=%s',(sha,)).fetchone()
                    if m['target_document_id']!=did:raise ValueError('document_mapping_conflict')
                    dst.execute("INSERT INTO recovery_03nov.document_evidence VALUES(%s,%s,'rrhh_main',%s,'unknown') ON CONFLICT DO NOTHING",(sha,evidence[ver],digest))
                    ev=dst.execute('SELECT canonical_url,inventory_sha256 FROM recovery_03nov.document_evidence WHERE source_sha=%s',(sha,)).fetchone()
                    if ev!={'canonical_url':evidence[ver],'inventory_sha256':digest}:raise ValueError('document_evidence_conflict')
                    dst.execute("INSERT INTO historical_procedure_documents VALUES('interino-2026-11-03',%s,%s,%s) ON CONFLICT DO NOTHING",(did,link['document_role'],link['relationship_evidence']))
                    actual_link=dst.execute("SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03' AND document_id=%s",(did,)).fetchone()
                    if actual_link!=link:raise ValueError('procedure_document_conflict')
                    table='offered_positions' if ver==702 else 'award_results'
                    n=dst.execute('SELECT count(*) AS n FROM '+table+' WHERE document_id=%s',(did,)).fetchone()['n']
                    docs.append({'version':ver,'sha256':sha,'target_document_id':did,'existing_rows':n,'status':'partial_or_field_differences_need_review'})
                after=counts(dst)
                if after!=before:raise ValueError('business_counts_changed')
                if dst.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()['n']!=3:raise ValueError('procedure_document_count')
                try:
                    with dst.transaction():
                        dst.execute("DELETE FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'")
                        raise RuntimeError('injected_rollback')
                except RuntimeError as exc:
                    if str(exc)!='injected_rollback':raise
                if dst.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()['n']!=3:raise ValueError('rollback_failed')
            return {'status':'PROCEDURE_REGISTERED_PARTIAL','documents':docs,'before':before,'after':after,
                    'rows_inserted':0,'associations_inserted':0,'pending_associations':213,
                    'rollback_tested':True,'publication_date_quality':'unknown','historical_status':'unknown'}
    finally:source.rollback()
