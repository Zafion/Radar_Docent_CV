"""Document-scoped orchestration for an explicitly isolated recovery trial.

The caller supplies already verified local evidence and two idle connections.
Every adapter commits or rolls back its own document; the report never treats
an unverified or interrupted run as a complete historical reconstruction.
"""
from pathlib import Path
import hashlib
import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from app.services.historical_unawarded_reconcile import reconcile_unawarded
from app.services.historical_secondary_reconcile import reconcile_secondary, _trial_fence
from app.services.historical_expert_reconcile import reconcile_expert_procedure
from app.services.historical_19nov_reconcile import reconcile_19nov, _link_procedure


def reconcile_cumulative(source, destination, *, documents, offers, execute=False, synthetic_token=None, progress=None):
    """Run five award documents and one expert document in documentary order."""
    required=('700','436','701','expert','1000067','1000444')
    if set(documents)!=set(required) or len(offers)!=2:
        raise ValueError('document_manifest_incomplete')
    if source.info.transaction_status!=TransactionStatus.IDLE or destination.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    source.row_factory=destination.row_factory=dict_row
    identities=[]
    for item in list(documents.values())+list(offers):
        path=Path(item['pdf_path']); digest=item['document_sha']
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('document_sha_mismatch')
        identities.append(digest)
    if len(set(identities))!=len(identities):raise ValueError('document_manifest_duplicate_sha')
    with destination.transaction():
        destination.execute('SET TRANSACTION READ ONLY')
        _trial_fence(destination,synthetic_token)
    report={'status':'planned' if not execute else 'in_progress','documents':{},'quarantine':'pending'}
    def save():
        if progress:progress(report)
    save()
    if not execute:
        report['documents']={k:{'sha256':documents[k]['document_sha'],'status':'planned'} for k in required}
        save();return report
    def one(key,call):
        try:
            value=call(); report['documents'][key]={'status':'applied','result':value}
        except (ValueError,psycopg.Error) as exc:
            report['documents'][key]={'status':'failed','error_type':type(exc).__name__}
        save()
    for key in ('700','436','701'):
        spec=documents[key]
        def invoke(key=key,spec=spec):
            args=dict(document_sha=spec['document_sha'],pdf_path=spec['pdf_path'],provenance=spec.get('provenance'),synthetic_token=synthetic_token)
            if key=='701': return reconcile_secondary(source,destination,**args)
            offer_sha=spec.get('accredited_offer_sha') if key=='700' else None
            if offer_sha:
                if not spec.get('accredited_offer_pdf_path'):raise ValueError('accredited_offer_pdf_missing')
                offer_path=Path(spec['accredited_offer_pdf_path'])
                if offer_path.is_symlink() or not offer_path.is_file() or hashlib.sha256(offer_path.read_bytes()).hexdigest()!=offer_sha:
                    raise ValueError('accredited_offer_pdf_mismatch')
                with source.transaction():
                    source.execute('SET TRANSACTION READ ONLY')
                    n=source.execute('''SELECT count(DISTINCT v.sha256) AS n FROM historical_procedure_documents l
                        JOIN documents d ON d.id=l.document_id JOIN document_versions v ON v.id=d.document_version_id
                        WHERE l.procedure_key='interino-2026-11-03' AND v.sha256=ANY(%s)''',
                        ([spec['document_sha'],offer_sha],)).fetchone()['n']
                    if n!=2:raise ValueError('accredited_offer_relation_missing')
            return reconcile_unawarded(source,destination,family='maestros',include_awards=True,
                accredited_offer_sha=offer_sha,**args)
        one(key,invoke)
        if key in ('700','701') and report['documents'][key]['status']=='applied':
            try: report['documents'][key]['procedure_link']=_link_procedure(source,destination,spec['document_sha'],synthetic_token,key='interino-2026-11-03')
            except (ValueError,psycopg.Error) as exc: report['documents'][key]['procedure_link']={'status':'pending','error_type':type(exc).__name__}
            if key=='700' and spec.get('accredited_offer_sha'):
                try: report['documents'][key]['offered_document_link']=_link_procedure(source,destination,spec['accredited_offer_sha'],synthetic_token,key='interino-2026-11-03')
                except (ValueError,psycopg.Error) as exc: report['documents'][key]['offered_document_link']={'status':'pending','error_type':type(exc).__name__}
            save()
    expert=documents['expert']; proof=expert['provenance']
    one('expert',lambda:reconcile_expert_procedure(source,destination,sha256=expert['document_sha'],pdf_bytes=Path(expert['pdf_path']).read_bytes(),
        html_bytes=proof['html_bytes'],html_sha256=proof['html_sha256'],page_url=proof['page_url'],document_url=proof['document_url'],
        procedure_key=expert['procedure_key'],synthetic_token=synthetic_token))
    try:
        nov=reconcile_19nov(source,destination,maestros=documents['1000067'],secondary=documents['1000444'],
                            offers=offers,execute=True,synthetic_token=synthetic_token)
        report['documents']['1000067']=nov['documents']['maestros'];report['documents']['1000444']=nov['documents']['secondary']
        report['quarantine']=nov['offers']
    except (ValueError,psycopg.Error) as exc:
        report['documents']['1000067']={'status':'pending','error_type':type(exc).__name__}
        report['documents']['1000444']={'status':'pending','error_type':type(exc).__name__}
    report['status']='partial'  # Completion additionally requires procedure-wide independent checks.
    save();return report
